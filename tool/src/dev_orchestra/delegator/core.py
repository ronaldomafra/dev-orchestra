"""Regras do delegador: sessões, tarefas, mensagens e permissões. Sem rede: o servidor MCP e o listener o usam."""

from __future__ import annotations

import functools
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..core.models import Project
from . import perms
from .consults import Consults
from .db import Store
from .errors import DelegatorError
from .profiles import profile_for

REPORT_STATUSES = ("DONE", "PARTIAL", "BLOCKED", "FAILED", "NEEDS_REVIEW")
_STATUS_TO_STATE = {s: s.lower() for s in REPORT_STATUSES}
ACTIVE_TASK_STATES = ("delegated", "running", "asked")
MAX_BODY = 16_000  # caracteres por mensagem (o conteúdo longo fica no repositório e é referenciado)
ALIVE_SECONDS = 120.0
_EVENT_META = ("role_from", "role_to", "task_id", "state", "attempt", "message_id", "latency_ms")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class Registry(Protocol):
    def get(self, project_id: str) -> Project | None: ...


class Waker(Protocol):
    def notify(self, project: Project, role: str, session: dict[str, Any] | None, message_id: int) -> str: ...


class NullWaker:
    """Claude Code é acordado pelo listener do hook (ele consulta o delegador); aqui não há nada a fazer."""

    def notify(self, project: Project, role: str, session: dict[str, Any] | None, message_id: int) -> str:
        return "listener"


def audited(fn):
    """Registra como evento `refused` o que o delegador recusou (permissão, estado, dados), com quem tentou."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except DelegatorError as exc:
            self._record_refusal(fn.__name__, args, exc)
            raise

    return wrapper


class Delegator:
    def __init__(
        self,
        store: Store,
        registry: Registry,
        waker: Waker | None = None,
        *,
        install: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        alive_seconds: float = ALIVE_SECONDS,
        passive_agents: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.waker = waker or NullWaker()
        self.install = install
        self._clock = clock or (lambda: datetime.now(UTC))
        self.alive_seconds = alive_seconds
        self.consults = Consults(store, clock, passive_agents)  # agentes passivos: fila, histórico e consumo

    # ------------------------------------------------------------ utilidades

    def _now(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    def _project(self, project_id: str) -> Project:
        project = self.registry.get(project_id)
        if project is None:
            raise DelegatorError(f"unknown project '{project_id}' (register it in the dev-orchestra TUI first)")
        return project

    def _session(self, session_id: str, tool: str) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if row is None:
            raise DelegatorError("unknown session_id: call register_session first", "unknown_session")
        session = dict(row)
        if not session["active"]:
            raise DelegatorError(
                "this session was replaced by a newer registration of the same role: call register_session again",
                "inactive_session",
            )
        perms.check(tool, perms.kind_of(session["role"]), session["role"])
        with self.store.tx() as conn:
            conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (self._now(), session_id))
        return session

    def _event(self, conn, project: str, kind: str, text: str, **meta: Any) -> None:
        """Registra um evento; `meta` (de, para, tarefa, estado, tentativa, mensagem, latência) alimenta o monitor."""
        fields = {k: v for k, v in meta.items() if k in _EVENT_META}
        columns = ", ".join(["ts", "project", "kind", "text", *fields])
        marks = ", ".join("?" * (4 + len(fields)))
        conn.execute(
            f"INSERT INTO events ({columns}) VALUES ({marks})", (self._now(), project, kind, text, *fields.values())
        )

    def _latency_ms(self, created_at: str) -> int | None:
        try:
            return max(0, int((self._clock() - datetime.fromisoformat(created_at)).total_seconds() * 1000))
        except ValueError:
            return None

    def _alive(self, last_seen: str) -> bool:
        try:
            seen = datetime.fromisoformat(last_seen)
        except ValueError:
            return False
        return self._clock() - seen <= timedelta(seconds=self.alive_seconds)

    def _post(
        self,
        conn,
        *,
        project: str,
        type_: str,
        from_role: str,
        from_session: str | None,
        to_role: str,
        to_session: str | None,
        task_id: str | None,
        attempt: int,
        body: str,
    ) -> int:
        if len(body) > MAX_BODY:
            raise DelegatorError(
                f"message too long ({len(body)} > {MAX_BODY} characters): keep it brief and reference files in the "
                "repository for long content",
                "too_long",
            )
        cur = conn.execute(
            "INSERT INTO messages (project, type, from_role, from_session, to_role, to_session, task_id, attempt, "
            "body, state, created_at) VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
            (project, type_, from_role, from_session, to_role, to_session, task_id, attempt, body, self._now()),
        )
        return int(cur.lastrowid)

    def _wake(self, project: Project, role: str, to_session: str | None, message_id: int) -> str:
        session = None
        if to_session:
            row = self.store.one("SELECT * FROM sessions WHERE id=?", (to_session,))
            session = dict(row) if row else None
        try:
            return self.waker.notify(project, role, session, message_id)
        except Exception as exc:  # noqa: BLE001 - o aviso é "melhor esforço": a mensagem já está guardada
            return f"wake failed: {exc}"

    def _pending_for(self, session: dict[str, Any]) -> int:
        row = self.store.one(
            "SELECT COUNT(*) AS n FROM messages WHERE project=? AND state='pending' AND "
            "(to_session=? OR (to_session IS NULL AND to_role=?))",
            (session["project"], session["id"], session["role"]),
        )
        return int(row["n"]) if row else 0

    def _pick_worker(self, conn, project: str, role: str) -> str | None:
        rows = conn.execute(
            "SELECT id, last_seen FROM sessions WHERE project=? AND role=? AND active=1", (project, role)
        )
        best: tuple[int, str] | None = None
        for row in rows:
            if not self._alive(row["last_seen"]):
                continue
            load = conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE project=? AND session_id=? "
                "AND state IN ('delegated','running','asked')",
                (project, row["id"]),
            ).fetchone()["n"]
            if best is None or load < best[0]:
                best = (load, row["id"])
        return best[1] if best else None

    @staticmethod
    def _message_dict(row) -> dict[str, Any]:
        return {
            "message_id": row["id"],
            "type": row["type"],
            "task_id": row["task_id"],
            "from_role": row["from_role"],
            "attempt": row["attempt"],
            "created_at": row["created_at"],
            "body": row["body"],
        }

    def _record_refusal(self, tool: str, args: tuple, exc: DelegatorError) -> None:
        project = role = None
        if tool == "register_session" and len(args) >= 2:
            project, role = args[0], args[1]
        elif args:
            row = self.store.one("SELECT project, role FROM sessions WHERE id=?", (args[0],))
            if row:
                project, role = row["project"], row["role"]
        if not project:
            return  # sem como atribuir a um projeto
        try:
            with self.store.tx() as conn:
                self._event(conn, project, "refused", f"{tool} negado: {exc}", role_from=role, role_to="delegator")
        except Exception:  # noqa: BLE001 - a auditoria nunca derruba a resposta
            pass

    # ----------------------------------------------------------- ferramentas

    @audited
    def register_session(self, project: str, role: str, cli: str, session_ref: str) -> dict[str, Any]:
        p = self._project(project)
        if role not in p.roles:
            raise DelegatorError(f"role '{role}' is not in project '{project}' (roles: {', '.join(p.role_order())})")
        configured = p.roles[role].cli
        if configured != cli:
            raise DelegatorError(f"role '{role}' is configured for cli '{configured}', not '{cli}'")
        ref = (session_ref or "").strip()
        if not ref:
            raise DelegatorError("session_ref is required (the session name, or the Codex thread UUID)")
        now = self._now()
        with self.store.tx() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE project=? AND role=? AND ref=?", (project, role, ref)
            ).fetchone()
            if row:
                session_id = row["id"]
                conn.execute("UPDATE sessions SET active=1, last_seen=?, cli=? WHERE id=?", (now, cli, session_id))
            else:
                session_id = "s-" + uuid.uuid4().hex[:10]
                conn.execute(
                    "INSERT INTO sessions (id, project, role, cli, ref, registered_at, last_seen, active) "
                    "VALUES (?,?,?,?,?,?,?,1)",
                    (session_id, project, role, cli, ref, now, now),
                )
            if role == perms.ORCHESTRATOR:
                conn.execute(
                    "UPDATE sessions SET active=0 WHERE project=? AND role=? AND id<>? AND active=1",
                    (project, role, session_id),
                )
            self._event(conn, project, "register", f"{ref} registrado ({cli})", role_from=role, role_to="delegator")
        session = dict(self.store.one("SELECT * FROM sessions WHERE id=?", (session_id,)))
        return {
            "session_id": session_id,
            "project": project,
            "role": role,
            "role_kind": perms.kind_of(role),
            "agent_name": p.agent_name(role),
            "pending_messages": self._pending_for(session),
            "profile": profile_for(role, project, self.install),
        }

    @audited
    def heartbeat(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id, "heartbeat")
        return {"ok": True, "pending_messages": self._pending_for(session)}

    @audited
    def delegate(
        self,
        session_id: str,
        task_id: str,
        to_role: str,
        instruction: str,
        model: str,
        effort: str,
        model_reason: str = "",
        user_approved: bool = False,
    ) -> dict[str, Any]:
        caller = self._session(session_id, "delegate")
        project = self._project(caller["project"])
        if not _TASK_ID.match(task_id or ""):
            raise DelegatorError("invalid task_id (letters, digits, '.', '-', '_')")
        if to_role == perms.ORCHESTRATOR or to_role not in project.roles:
            workers = [r for r in project.role_order() if r != perms.ORCHESTRATOR]
            raise DelegatorError(f"to_role must be a worker role of this project: {', '.join(workers)}")
        if not (instruction or "").strip():
            raise DelegatorError("instruction is required")
        if not (model or "").strip() or not (effort or "").strip():
            raise DelegatorError("model and effort are required (recommend them to the person and wait for approval)")
        if not user_approved:
            raise DelegatorError(
                "user_approved must be true: ask the person to approve the model and effort first "
                "(AGENTS.md, docs/MODEL-SELECTION.md)",
                "approval_required",
            )
        now = self._now()
        with self.store.tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE project=? AND task_id=?", (project.id, task_id)).fetchone()
            attempt = 1
            if task:
                if task["state"] in ACTIVE_TASK_STATES:
                    raise DelegatorError(
                        f"task {task_id} is already {task['state']}: wait for its result or cancel it first", "busy"
                    )
                if task["state"] == "done":
                    raise DelegatorError(f"task {task_id} is already done")
                attempt = int(task["attempt"]) + 1
            target = self._pick_worker(conn, project.id, to_role)
            fields = (to_role, caller["id"], target, attempt, model, effort, model_reason, 1, now)
            if task:
                conn.execute(
                    "UPDATE tasks SET state='delegated', to_role=?, orchestrator_session=?, session_id=?, attempt=?, "
                    "model=?, effort=?, model_reason=?, user_approved=?, updated_at=?, summary=NULL "
                    "WHERE project=? AND task_id=?",
                    (*fields, project.id, task_id),
                )
            else:
                conn.execute(
                    "INSERT INTO tasks (project, task_id, state, to_role, orchestrator_session, session_id, attempt, "
                    "model, effort, model_reason, user_approved, created_at, updated_at) "
                    "VALUES (?,?,'delegated',?,?,?,?,?,?,?,?,?,?)",
                    (
                        project.id,
                        task_id,
                        to_role,
                        caller["id"],
                        target,
                        attempt,
                        model,
                        effort,
                        model_reason,
                        1,
                        now,
                        now,
                    ),
                )
            body = (
                f"TASK: {task_id}\nATTEMPT: {attempt}\nMODEL: {model}\nEFFORT: {effort}\n"
                f"MODEL_REASON: {model_reason or '-'}\nINSTRUCTION:\n{instruction.strip()}\n"
            )
            mid = self._post(
                conn,
                project=project.id,
                type_="delegate",
                from_role=caller["role"],
                from_session=caller["id"],
                to_role=to_role,
                to_session=target,
                task_id=task_id,
                attempt=attempt,
                body=body,
            )
            first_line = " ".join(instruction.split())[:110]
            self._event(
                conn, project.id, "delegate", f"{model} / {effort} · {first_line}", role_from=caller["role"],
                role_to=to_role, task_id=task_id, state="delegated", attempt=attempt, message_id=mid,
            )  # fmt: skip
        wake = self._wake(project, to_role, target, mid)
        out = {
            "message_id": mid,
            "task_id": task_id,
            "task_state": "delegated",
            "attempt": attempt,
            "to_role": to_role,
            "delivered_to_session": target,
            "wake": wake,
            "next": "End your turn: the result will arrive as a new message (call receive when notified).",
        }
        if target is None:
            out["warning"] = (
                f"no live '{to_role}' session is registered: the message is queued (pending) and will be delivered "
                "when a session of that role registers or is woken. Tell the person the worker did not receive it yet."
            )
        return out

    @audited
    def receive(self, session_id: str, message_id: int | None = None) -> dict[str, Any]:
        session = self._session(session_id, "receive")
        addressed = "project=? AND state='pending' AND (to_session=? OR (to_session IS NULL AND to_role=?))"
        params = (session["project"], session["id"], session["role"])
        with self.store.tx() as conn:
            if message_id is not None:
                row = conn.execute(
                    "SELECT * FROM messages WHERE id=? AND project=?", (message_id, session["project"])
                ).fetchone()
                if row is None or not (
                    row["to_session"] == session["id"]
                    or (row["to_session"] is None and row["to_role"] == session["role"])
                ):
                    raise DelegatorError("message not found or not addressed to this session", "not_addressed")
                if row["state"] != "pending":
                    raise DelegatorError(f"message {message_id} is already {row['state']}", row["state"])
            else:
                row = conn.execute(f"SELECT * FROM messages WHERE {addressed} ORDER BY id LIMIT 1", params).fetchone()
                if row is None:
                    return {"message": None, "remaining": 0}
            conn.execute(
                "UPDATE messages SET state='claimed', claimed_at=?, claimed_by=?, to_session=? WHERE id=?",
                (self._now(), session["id"], session["id"], row["id"]),
            )
            if row["type"] == "delegate" and row["task_id"]:
                conn.execute(
                    "UPDATE tasks SET state='running', session_id=?, updated_at=? "
                    "WHERE project=? AND task_id=? AND state='delegated'",
                    (session["id"], self._now(), session["project"], row["task_id"]),
                )
            task_state = None
            if row["task_id"]:
                found = conn.execute(
                    "SELECT state FROM tasks WHERE project=? AND task_id=?", (session["project"], row["task_id"])
                ).fetchone()
                task_state = found["state"] if found else None
            self._event(
                conn, session["project"], "receive", f"{session['role']} recebeu {row['type']} #{row['id']}",
                role_from="delegator", role_to=session["role"], task_id=row["task_id"], state=task_state,
                attempt=row["attempt"], message_id=row["id"], latency_ms=self._latency_ms(row["created_at"]),
            )  # fmt: skip
            remaining = conn.execute(f"SELECT COUNT(*) AS n FROM messages WHERE {addressed}", params).fetchone()["n"]
        return {"message": self._message_dict(row), "remaining": remaining}

    def _own_task(self, conn, session: dict[str, Any], task_id: str, tool: str):
        task = conn.execute(
            "SELECT * FROM tasks WHERE project=? AND task_id=?", (session["project"], task_id)
        ).fetchone()
        if task is None:
            raise DelegatorError(f"unknown task '{task_id}'")
        if task["session_id"] != session["id"]:
            raise DelegatorError(
                f"task {task_id} is not assigned to this session: {tool} only works on your own task", "not_own_task"
            )
        return task

    @audited
    def report(
        self,
        session_id: str,
        task_id: str,
        status: str,
        summary: str,
        evidence: str,
        changes: str = "",
        validation: str = "",
        risks: str = "",
        next_step: str = "",
        git: str = "",
    ) -> dict[str, Any]:
        session = self._session(session_id, "report")
        status = (status or "").upper()
        if status not in REPORT_STATUSES:
            raise DelegatorError(f"status must be one of {', '.join(REPORT_STATUSES)}")
        if not (summary or "").strip() or not (evidence or "").strip():
            raise DelegatorError("summary and evidence are required")
        project = self._project(session["project"])
        with self.store.tx() as conn:
            task = self._own_task(conn, session, task_id, "report")
            if task["state"] == "cancelled":
                raise DelegatorError(f"task {task_id} was cancelled: stop working on it", "cancelled")
            if task["state"] == "asked":
                raise DelegatorError(f"task {task_id} has an unanswered question: wait for the answer", "asked")
            if task["state"] != "running":
                raise DelegatorError(f"task {task_id} is {task['state']}, not running: nothing to report", "bad_state")
            lines = [
                f"STATUS: {status}",
                f"TASK: {task_id}",
                f"ATTEMPT: {task['attempt']}",
                f"SUMMARY: {summary.strip()}",
                f"EVIDENCE: {evidence.strip()}",
            ]
            for label, value in (
                ("CHANGES", changes),
                ("VALIDATION", validation),
                ("RISKS", risks),
                ("NEXT_STEP", next_step),
                ("GIT", git),
            ):
                if (value or "").strip():
                    lines.append(f"{label}: {value.strip()}")
            target = task["orchestrator_session"]
            alive = (
                conn.execute("SELECT id FROM sessions WHERE id=? AND active=1", (target,)).fetchone()
                if target
                else None
            )
            mid = self._post(
                conn,
                project=project.id,
                type_="result",
                from_role=session["role"],
                from_session=session["id"],
                to_role=perms.ORCHESTRATOR,
                to_session=alive["id"] if alive else None,
                task_id=task_id,
                attempt=int(task["attempt"]),
                body="\n".join(lines) + "\n",
            )
            conn.execute(
                "UPDATE tasks SET state=?, summary=?, updated_at=? WHERE project=? AND task_id=?",
                (_STATUS_TO_STATE[status], summary.strip()[:300], self._now(), project.id, task_id),
            )
            self._event(
                conn, project.id, "report", f"{status} · {' '.join(summary.split())[:140]}",
                role_from=session["role"], role_to=perms.ORCHESTRATOR, task_id=task_id,
                state=_STATUS_TO_STATE[status], attempt=int(task["attempt"]), message_id=mid,
            )  # fmt: skip
        wake = self._wake(project, perms.ORCHESTRATOR, alive["id"] if alive else None, mid)
        return {
            "accepted": True,
            "message_id": mid,
            "task_state": _STATUS_TO_STATE[status],
            "wake": wake,
            "next": "End your turn.",
        }

    @audited
    def ask(self, session_id: str, task_id: str, question: str, options: list[str] | None = None) -> dict[str, Any]:
        session = self._session(session_id, "ask")
        if not (question or "").strip():
            raise DelegatorError("question is required")
        project = self._project(session["project"])
        with self.store.tx() as conn:
            task = self._own_task(conn, session, task_id, "ask")
            if task["state"] != "running":
                raise DelegatorError(
                    f"task {task_id} is {task['state']}: you can only ask while it is running", "bad_state"
                )
            cur = conn.execute(
                "INSERT INTO questions (project, task_id, session_id, question, options, state, created_at) "
                "VALUES (?,?,?,?,?,'open',?)",
                (project.id, task_id, session["id"], question.strip(), json.dumps(options or []), self._now()),
            )
            qid = int(cur.lastrowid)
            body = f"QUESTION_ID: {qid}\nTASK: {task_id}\nFROM: {session['role']}\nQUESTION: {question.strip()}\n"
            if options:
                body += "OPTIONS: " + " | ".join(options) + "\n"
            target = task["orchestrator_session"]
            alive = (
                conn.execute("SELECT id FROM sessions WHERE id=? AND active=1", (target,)).fetchone()
                if target
                else None
            )
            mid = self._post(
                conn,
                project=project.id,
                type_="question",
                from_role=session["role"],
                from_session=session["id"],
                to_role=perms.ORCHESTRATOR,
                to_session=alive["id"] if alive else None,
                task_id=task_id,
                attempt=int(task["attempt"]),
                body=body,
            )
            conn.execute(
                "UPDATE tasks SET state='asked', updated_at=? WHERE project=? AND task_id=?",
                (self._now(), project.id, task_id),
            )
            self._event(
                conn, project.id, "ask", f"pergunta #{qid}: {' '.join(question.split())[:140]}",
                role_from=session["role"], role_to=perms.ORCHESTRATOR, task_id=task_id, state="asked",
                attempt=int(task["attempt"]), message_id=mid,
            )  # fmt: skip
        wake = self._wake(project, perms.ORCHESTRATOR, alive["id"] if alive else None, mid)
        return {
            "question_id": qid,
            "task_state": "asked",
            "wake": wake,
            "next": "End your turn: the answer will arrive as a new message.",
        }

    @audited
    def answer(self, session_id: str, question_id: int, answer: str) -> dict[str, Any]:
        session = self._session(session_id, "answer")
        if not (answer or "").strip():
            raise DelegatorError("answer is required")
        project = self._project(session["project"])
        with self.store.tx() as conn:
            q = conn.execute("SELECT * FROM questions WHERE id=? AND project=?", (question_id, project.id)).fetchone()
            if q is None:
                raise DelegatorError(f"unknown question {question_id}")
            if q["state"] != "open":
                raise DelegatorError(f"question {question_id} is already {q['state']}", q["state"])
            task = conn.execute(
                "SELECT * FROM tasks WHERE project=? AND task_id=?", (project.id, q["task_id"])
            ).fetchone()
            conn.execute(
                "UPDATE questions SET state='answered', answer=?, answered_at=? WHERE id=?",
                (answer.strip(), self._now(), question_id),
            )
            if task and task["state"] == "asked":
                conn.execute(
                    "UPDATE tasks SET state='running', updated_at=? WHERE project=? AND task_id=?",
                    (self._now(), project.id, q["task_id"]),
                )
            body = f"QUESTION_ID: {question_id}\nTASK: {q['task_id']}\nANSWER: {answer.strip()}\n"
            asker = conn.execute("SELECT id, role FROM sessions WHERE id=?", (q["session_id"],)).fetchone()
            mid = self._post(
                conn,
                project=project.id,
                type_="answer",
                from_role=session["role"],
                from_session=session["id"],
                to_role=asker["role"] if asker else (task["to_role"] if task else "developer"),
                to_session=q["session_id"],
                task_id=q["task_id"],
                attempt=int(task["attempt"]) if task else 1,
                body=body,
            )
            self._event(
                conn, project.id, "answer", f"resposta à pergunta #{question_id}: {' '.join(answer.split())[:140]}",
                role_from=session["role"], role_to=asker["role"] if asker else None, task_id=q["task_id"],
                state="running", attempt=int(task["attempt"]) if task else 1, message_id=mid,
            )  # fmt: skip
        wake = self._wake(project, asker["role"] if asker else "developer", q["session_id"], mid)
        return {"ok": True, "message_id": mid, "wake": wake}

    def _cancel(
        self, project: Project, task_id: str, reason: str, actor: str, actor_session: str | None
    ) -> dict[str, Any]:
        with self.store.tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE project=? AND task_id=?", (project.id, task_id)).fetchone()
            if task is None:
                raise DelegatorError(f"unknown task '{task_id}' (it was never delegated)")
            if task["state"] not in ACTIVE_TASK_STATES:
                raise DelegatorError(
                    f"task {task_id} is {task['state']}: only delegated, running or asked tasks can be cancelled"
                )
            conn.execute(
                "UPDATE tasks SET state='cancelled', updated_at=? WHERE project=? AND task_id=?",
                (self._now(), project.id, task_id),
            )
            conn.execute(
                "UPDATE messages SET state='cancelled' WHERE project=? AND task_id=? AND state='pending'",
                (project.id, task_id),
            )
            conn.execute(
                "UPDATE questions SET state='cancelled' WHERE project=? AND task_id=? AND state='open'",
                (project.id, task_id),
            )
            notices: list[tuple[str, str | None, int]] = []
            if task["session_id"] and task["state"] in ("running", "asked"):
                mid = self._post(
                    conn,
                    project=project.id,
                    type_="cancel",
                    from_role=actor,
                    from_session=actor_session,
                    to_role=task["to_role"],
                    to_session=task["session_id"],
                    task_id=task_id,
                    attempt=int(task["attempt"]),
                    body=f"TASK: {task_id}\nCANCELLED: {reason or '-'}\nStop working on it and do not call report.\n",
                )
                notices.append((task["to_role"], task["session_id"], mid))
            if actor == perms.PERSON:
                alive = (
                    conn.execute(
                        "SELECT id FROM sessions WHERE id=? AND active=1", (task["orchestrator_session"],)
                    ).fetchone()
                    if task["orchestrator_session"]
                    else None
                )
                mid = self._post(
                    conn,
                    project=project.id,
                    type_="cancel",
                    from_role=actor,
                    from_session=None,
                    to_role=perms.ORCHESTRATOR,
                    to_session=alive["id"] if alive else None,
                    task_id=task_id,
                    attempt=int(task["attempt"]),
                    body=f"TASK: {task_id}\nCANCELLED by the person: {reason or '-'}\n",
                )
                notices.append((perms.ORCHESTRATOR, alive["id"] if alive else None, mid))
            self._event(
                conn, project.id, "cancel", f"cancelada por {actor}: {reason or '-'}", role_from=actor,
                role_to=task["to_role"], task_id=task_id, state="cancelled", attempt=int(task["attempt"]),
            )  # fmt: skip
        for role, to_session, mid in notices:
            self._wake(project, role, to_session, mid)
        return {"ok": True, "task_id": task_id, "task_state": "cancelled"}

    @audited
    def cancel_task(self, session_id: str, task_id: str, reason: str = "") -> dict[str, Any]:
        session = self._session(session_id, "cancel_task")
        return self._cancel(self._project(session["project"]), task_id, reason, session["role"], session["id"])

    def cancel_task_as_person(self, project: str, task_id: str, reason: str = "") -> dict[str, Any]:
        return self._cancel(self._project(project), task_id, reason, perms.PERSON, None)

    # ------------------------------------------------------------- consulta

    def status_data(self, project: str, only_session: str | None = None) -> dict[str, Any]:
        p = self._project(project)
        sessions = []
        for row in self.store.query("SELECT * FROM sessions WHERE project=? ORDER BY role, registered_at", (project,)):
            if only_session and row["id"] != only_session:
                continue
            session = dict(row)
            sessions.append(
                {
                    "session_id": row["id"],
                    "role": row["role"],
                    "cli": row["cli"],
                    "ref": row["ref"],
                    "active": bool(row["active"]),
                    "alive": bool(row["active"]) and self._alive(row["last_seen"]),
                    "last_seen": row["last_seen"],
                    "pending": self._pending_for(session) if row["active"] else 0,
                }
            )
        if only_session:
            tasks = self.store.query(
                "SELECT * FROM tasks WHERE project=? AND session_id=? ORDER BY updated_at DESC", (project, only_session)
            )
        else:
            tasks = self.store.query("SELECT * FROM tasks WHERE project=? ORDER BY updated_at DESC", (project,))
        last_event = {
            r["task_id"]: r["m"]
            for r in self.store.query(
                "SELECT task_id, MAX(ts) AS m FROM events WHERE project=? AND task_id IS NOT NULL GROUP BY task_id",
                (project,),
            )
        }
        open_questions = self.store.query(
            "SELECT id, task_id, question, created_at FROM questions WHERE project=? AND state='open' ORDER BY id",
            (project,),
        )
        pending = self.store.one("SELECT COUNT(*) AS n FROM messages WHERE project=? AND state='pending'", (project,))
        return {
            "project": project,
            "roles": p.role_order(),
            "sessions": sessions,
            "tasks": [
                {
                    "task_id": t["task_id"],
                    "state": t["state"],
                    "to_role": t["to_role"],
                    "attempt": t["attempt"],
                    "model": t["model"],
                    "effort": t["effort"],
                    "session_id": t["session_id"],
                    "summary": t["summary"],
                    "updated_at": t["updated_at"],
                    "last_event": last_event.get(t["task_id"], t["updated_at"]),
                }
                for t in tasks
            ],
            "open_questions": [dict(q) for q in open_questions],
            "pending_messages": int(pending["n"]) if pending else 0,
        }

    @staticmethod
    def render_status(data: dict[str, Any]) -> str:
        lines = [f"Project {data['project']}: {data['pending_messages']} pending message(s)"]
        lines.append("Sessions:")
        if not data["sessions"]:
            lines.append("  (none registered)")
        for s in data["sessions"]:
            live = "alive" if s["alive"] else ("stale" if s["active"] else "replaced")
            lines.append(f"  {s['role']} [{s['cli']}] {s['ref']}: {live}, {s['pending']} pending")
        lines.append("Tasks:")
        if not data["tasks"]:
            lines.append("  (none delegated)")
        for t in data["tasks"]:
            extra = f" - {t['summary']}" if t.get("summary") else ""
            lines.append(f"  {t['task_id']}: {t['state']} (to {t['to_role']}, attempt {t['attempt']}){extra}")
        for q in data["open_questions"]:
            lines.append(f"Open question #{q['id']} on {q['task_id']}: {q['question']}")
        return "\n".join(lines)

    @audited
    def project_status(self, session_id: str) -> str:
        session = self._session(session_id, "project_status")
        only = session["id"] if perms.kind_of(session["role"]) == perms.WORKER else None
        return self.render_status(self.status_data(session["project"], only))

    def events(self, project: str, limit: int = 30) -> list[dict[str, Any]]:
        self._project(project)
        rows = self.store.query(
            "SELECT ts, kind, text FROM events WHERE project=? ORDER BY id DESC LIMIT ?", (project, limit)
        )
        return [dict(r) for r in rows]

    # ----------------------------------------------------- listener e perfis

    def pending_for_agent(self, project: str, agent: str) -> dict[str, Any]:
        """Para o listener do hook: quantas mensagens esperam o agente (só leitura, sem entregar o conteúdo).

        Cada consulta conta como sinal de vida da sessão (o listener roda enquanto a sessão está aberta)."""
        p = self._project(project)
        role = next((r for r in p.roles if p.agent_name(r) == agent), None)
        row = self.store.one("SELECT * FROM sessions WHERE project=? AND ref=? AND active=1", (project, agent))
        if row is not None:
            role = row["role"]
            with self.store.tx() as conn:
                conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (self._now(), row["id"]))
        if role is None:
            return {"count": 0, "ids": [], "registered": False, "role": None}
        sid = row["id"] if row is not None else None
        rows = self.store.query(
            "SELECT id, type FROM messages WHERE project=? AND state='pending' AND "
            "((to_session IS NOT NULL AND to_session=?) OR (to_session IS NULL AND to_role=?)) ORDER BY id",
            (project, sid or "", role),
        )
        return {
            "count": len(rows),
            "ids": [r["id"] for r in rows],
            "types": [r["type"] for r in rows],
            "registered": row is not None,
            "role": role,
        }

    def push_profile(self, project: str, role: str) -> dict[str, Any]:
        """Reenvia o perfil (mensagem `profile`) às sessões ativas do papel."""
        p = self._project(project)
        if role not in p.roles:
            raise DelegatorError(f"role '{role}' is not in project '{project}'")
        sent = []
        with self.store.tx() as conn:
            rows = conn.execute(
                "SELECT id, last_seen FROM sessions WHERE project=? AND role=? AND active=1", (project, role)
            ).fetchall()
            for row in rows:
                if not self._alive(row["last_seen"]):
                    continue
                mid = self._post(
                    conn,
                    project=project,
                    type_="profile",
                    from_role="delegator",
                    from_session=None,
                    to_role=role,
                    to_session=row["id"],
                    task_id=None,
                    attempt=1,
                    body=profile_for(role, project, self.install),
                )
                sent.append((row["id"], mid))
            self._event(conn, project, "profile", f"profile of {role} pushed to {len(sent)} session(s)")
        for sid, mid in sent:
            self._wake(p, role, sid, mid)
        return {"sessions": [s for s, _ in sent]}

    # ------------------------------------------------- sinais dos hooks (alertas)

    SIGNAL_KINDS = ("permission", "question", "turn_error")

    def _role_of_agent(self, project: Project, agent: str) -> tuple[str | None, str | None]:
        """(papel, id da sessão) de um agente pelo nome: sessão registrada ou nome do cadastro."""
        row = self.store.one(
            "SELECT id, role FROM sessions WHERE project=? AND ref=? AND active=1", (project.id, agent)
        )
        if row:
            return row["role"], row["id"]
        return next((r for r in project.roles if project.agent_name(r) == agent), None), None

    def signal(self, project: str, agent: str, kind: str, message: str = "", detail: str = "") -> dict[str, Any]:
        """Um hook do Claude avisa que a sessão parou esperando a pessoa (permissão, pergunta) ou que o turno falhou."""
        p = self._project(project)
        if kind not in self.SIGNAL_KINDS:
            raise DelegatorError(f"signal kind must be one of {', '.join(self.SIGNAL_KINDS)}")
        if not (agent or "").strip():
            raise DelegatorError("agent is required")
        role, session_id = self._role_of_agent(p, agent)
        now = self._now()
        with self.store.tx() as conn:
            task = None
            if session_id:
                task = conn.execute(
                    "SELECT task_id FROM tasks WHERE project=? AND session_id=? AND state IN ('running','asked') "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (project, session_id),
                ).fetchone()
            task_id = task["task_id"] if task else None
            if kind == "permission":
                # perguntar à pessoa também gera "Claude needs your permission": já é o alerta de pergunta
                asking = conn.execute(
                    "SELECT id FROM alerts WHERE project=? AND agent=? AND kind='question' AND cleared_at IS NULL",
                    (project, agent),
                ).fetchone()
                if asking:
                    conn.execute("UPDATE alerts SET updated_at=? WHERE id=?", (now, asking["id"]))
                    return {"alert_id": asking["id"], "merged": "question"}
            row = conn.execute(
                "SELECT id FROM alerts WHERE project=? AND agent=? AND kind=? AND cleared_at IS NULL",
                (project, agent, kind),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE alerts SET message=?, detail=?, updated_at=?, task_id=COALESCE(?, task_id) WHERE id=?",
                    (message[:300], detail[:1500], now, task_id, row["id"]),
                )
                alert_id = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO alerts (project, agent, role, kind, message, detail, task_id, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (project, agent, role, kind, message[:300], detail[:1500], task_id, now, now),
                )
                alert_id = int(cur.lastrowid)
                label = {"permission": "aguardando permissão", "question": "pergunta para a pessoa",
                         "turn_error": "turno interrompido"}[kind]  # fmt: skip
                self._event(
                    conn, project, "alert", f"{agent}: {label} · {' '.join(message.split())[:120]}",
                    role_from=role, role_to="pessoa", task_id=task_id, state=kind,
                )  # fmt: skip
        return {"alert_id": alert_id}

    def clear_signals(self, project: str, agent: str) -> dict[str, Any]:
        """A sessão voltou a trabalhar (ou terminou o turno): os sinais de espera dela deixam de valer."""
        self._project(project)
        with self.store.tx() as conn:
            cur = conn.execute(
                "UPDATE alerts SET cleared_at=? WHERE project=? AND agent=? AND cleared_at IS NULL",
                (self._now(), project, agent),
            )
        return {"cleared": cur.rowcount}

    def active_signals(self, project: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alerts WHERE cleared_at IS NULL"
        params: tuple = ()
        if project:
            sql += " AND project=?"
            params = (project,)
        return [dict(r) for r in self.store.query(sql + " ORDER BY id", params)]

    # ----------------------------------------------------------------- monitor

    def timeline(self, project: str | None = None, limit: int = 200, after_id: int = 0) -> list[dict[str, Any]]:
        """Eventos para o monitor, do mais novo ao mais antigo (de todos os projetos se `project` for None)."""
        sql = "SELECT * FROM events WHERE id > ?"
        params: list[Any] = [after_id]
        if project:
            sql += " AND project=?"
            params.append(project)
        rows = self.store.query(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))
        return [dict(r) for r in rows]

    def message(self, project: str, message_id: int) -> dict[str, Any]:
        """Texto completo de uma mensagem (para o monitor; é conteúdo de outro agente: só exibir)."""
        row = self.store.one("SELECT * FROM messages WHERE id=? AND project=?", (message_id, project))
        if row is None:
            raise DelegatorError(f"unknown message {message_id}")
        out = self._message_dict(row)
        out.update(state=row["state"], to_role=row["to_role"], claimed_at=row["claimed_at"])
        if row["type"] == "question":
            q = self.store.one(
                "SELECT id, state, answer FROM questions WHERE project=? AND task_id=? ORDER BY id DESC LIMIT 1",
                (project, row["task_id"]),
            )
            out["question"] = dict(q) if q else None
        return out

    def task_history(self, project: str, task_id: str) -> list[dict[str, Any]]:
        rows = self.store.query(
            "SELECT ts, kind, state, attempt, latency_ms FROM events WHERE project=? AND task_id=? ORDER BY id",
            (project, task_id),
        )
        return [dict(r) for r in rows]

    def stats(self, project: str | None = None) -> dict[str, Any]:
        """Números da faixa do monitor."""
        scope = "project=?" if project else "1=1"
        params: tuple = (project,) if project else ()

        def count(table: str, cond: str, extra: tuple = ()) -> int:
            row = self.store.one(f"SELECT COUNT(*) AS n FROM {table} WHERE {scope} AND {cond}", (*params, *extra))
            return int(row["n"]) if row else 0

        sessions = self.store.query(f"SELECT last_seen FROM sessions WHERE {scope} AND active=1", params)
        today = self._clock().date().isoformat()
        latencies = [
            r["latency_ms"]
            for r in self.store.query(
                f"SELECT latency_ms FROM events WHERE {scope} AND kind='receive' AND latency_ms IS NOT NULL "
                "ORDER BY id DESC LIMIT 50",
                params,
            )
        ]
        return {
            "sessions": len(sessions),
            "alive": sum(self._alive(r["last_seen"]) for r in sessions),
            "executing": count("tasks", "state IN ('delegated','running')"),
            "open_questions": count("questions", "state='open'"),
            "pending": count("messages", "state='pending'"),
            "refusals_today": count("events", "kind='refused' AND ts >= ?", (today,)),
            "events_today": count("events", "ts >= ?", (today,)),
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
        }
