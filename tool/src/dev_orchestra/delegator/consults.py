"""Consultas a agentes passivos: fila, histórico e consumo, no mesmo SQLite do delegador.

Um agente passivo não é uma sessão aberta nem pertence a um projeto: quem consulta informa só o **id** (`consult`) e
o delegador guarda o pedido. O executor do host (`dev-orchestra passive run`) pega o pedido, roda a CLI do agente
uma vez (`--resume`, ou Ollama) e devolve a resposta com o uso de tokens. Quem consultou não sabe qual CLI respondeu.

As definições dos agentes são lidas da pasta de instalação (`<instalação>/agentes/`, montada no container em
`/data`, somente leitura): não há sincronização. Fica separado do fluxo de tarefas dos papéis (sem
`register_session`, permissões por papel ou `report`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from .db import Store
from .errors import DelegatorError

MAX_INSTRUCTION = 60_000  # análises levam dados (transações, trechos de log): mais que o limite das tarefas
MAX_RESULT = 200_000
FINAL = ("done", "failed", "cancelled")
RUNNER_ALIVE_SECONDS = 30.0  # o executor bate a cada 2 s

SCHEMA = """
CREATE TABLE IF NOT EXISTS consults (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    cli TEXT,
    model TEXT,
    result TEXT,
    error TEXT,
    usage TEXT,
    duration_ms INTEGER,
    runner TEXT,
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS consults_inbox ON consults (agent, state, id);

CREATE TABLE IF NOT EXISTS passive_runners (
    name TEXT PRIMARY KEY,
    pid INTEGER,
    agents TEXT,
    started_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""

AgentInfo = dict[str, Any]  # id, cli, model, effort, description


def load_agents_from_disk() -> dict[str, AgentInfo]:
    """Definições em `<instalação>/agentes/` (no container: /data/agentes)."""
    from ..core import passive

    return {
        a.id: {"id": a.id, "cli": a.cli, "model": a.model, "effort": a.effort, "description": a.description}
        for a in passive.list_agents()
    }


class Consults:
    def __init__(
        self,
        store: Store,
        clock: Callable[[], datetime] | None = None,
        agents: Callable[[], dict[str, AgentInfo]] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._agents = agents or load_agents_from_disk
        store.executescript(SCHEMA)

    def _now(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    def _event(self, conn, origin: str, text: str, **meta: Any) -> None:
        """Entra na linha do tempo do monitor (projeto = a origem, ou `passivos` quando não informada)."""
        fields = {k: v for k, v in meta.items() if k in ("role_from", "role_to", "state", "message_id", "latency_ms")}
        columns = ", ".join(["ts", "project", "kind", "text", *fields])
        marks = ", ".join("?" * (4 + len(fields)))
        conn.execute(
            f"INSERT INTO events ({columns}) VALUES ({marks})",
            (self._now(), origin or "passivos", "consult", text, *fields.values()),
        )

    def _latency(self, since: str | None) -> int | None:
        try:
            return max(0, int((self._clock() - datetime.fromisoformat(since or "")).total_seconds() * 1000))
        except ValueError:
            return None

    # ------------------------------------------------------------- agentes

    def agents(self) -> dict[str, AgentInfo]:
        try:
            return self._agents()
        except Exception as exc:  # noqa: BLE001 - pasta ilegível não derruba o serviço
            raise DelegatorError(f"could not read the passive agents: {exc}") from exc

    def runner(self) -> dict[str, Any] | None:
        """O executor que bateu por último (ou None). `alive` = bateu há pouco."""
        row = self.store.one("SELECT * FROM passive_runners ORDER BY last_seen DESC LIMIT 1")
        if row is None:
            return None
        age = self._latency(row["last_seen"])
        return {
            "name": row["name"], "pid": row["pid"], "started_at": row["started_at"], "last_seen": row["last_seen"],
            "alive": age is not None and age <= RUNNER_ALIVE_SECONDS * 1000,
            "agents": json.loads(row["agents"] or "[]"),
        }  # fmt: skip

    def heartbeat(self, name: str, pid: int | None, agents: list[str]) -> dict[str, Any]:
        now = self._now()
        with self.store.tx() as conn:
            conn.execute(
                "INSERT INTO passive_runners (name, pid, agents, started_at, last_seen) VALUES (?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET pid=excluded.pid, agents=excluded.agents, last_seen=excluded.last_seen",
                (name[:80], pid, json.dumps(agents), now, now),
            )
        return {"ok": True}

    def list_agents(self) -> list[dict[str, Any]]:
        runner = self.runner()
        alive = bool(runner and runner["alive"])
        return [
            {"id": a["id"], "description": a.get("description", ""), "runner_alive": alive}
            for a in sorted(self.agents().values(), key=lambda a: a["id"])
        ]

    # ----------------------------------------------------------- consultas

    def consult(self, agent_id: str, instruction: str, origin: str = "") -> dict[str, Any]:
        if not instruction.strip():
            raise DelegatorError("instruction is empty")
        if len(instruction) > MAX_INSTRUCTION:
            raise DelegatorError(
                f"instruction too long ({len(instruction)} > {MAX_INSTRUCTION} characters)", "too_long"
            )
        agents = self.agents()
        agent = agents.get(agent_id)
        if agent is None:
            known = ", ".join(sorted(agents)) or "none"
            raise DelegatorError(f"unknown agent '{agent_id}' (known: {known})", "unknown_agent")
        origin = origin.strip()[:80]
        with self.store.tx() as conn:
            cur = conn.execute(
                "INSERT INTO consults (agent, origin, instruction, state, cli, model, created_at) "
                "VALUES (?,?,?,'pending',?,?,?)",
                (agent_id, origin, instruction, agent["cli"], agent.get("model"), self._now()),
            )
            consult_id = int(cur.lastrowid)
            queue = conn.execute(
                "SELECT COUNT(*) AS n FROM consults WHERE agent=? AND state IN ('pending','running') AND id<?",
                (agent_id, consult_id),
            ).fetchone()["n"]
            self._event(
                conn, origin, f"consulta #{consult_id} → {agent_id}" + (f" (fila {queue})" if queue else ""),
                role_from=origin or "?", role_to=agent_id, state="pending", message_id=consult_id,
            )  # fmt: skip
        runner = self.runner()
        out = {"consult_id": consult_id, "agent": agent_id, "state": "pending", "ahead_in_queue": queue}
        if not (runner and runner["alive"]):
            out["note"] = "the passive-agent runner is not running: the consult waits in the queue"
        return out

    @staticmethod
    def _usage(raw: str | None) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _row(self, row, *, full: bool = True, public: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "consult_id": row["id"], "agent": row["agent"], "origin": row["origin"], "state": row["state"],
            "usage": self._usage(row["usage"]), "duration_ms": row["duration_ms"], "created_at": row["created_at"],
            "claimed_at": row["claimed_at"], "finished_at": row["finished_at"],
        }  # fmt: skip
        if not public:  # quem consulta não precisa saber qual CLI respondeu; a TUI e o executor sim
            out.update(cli=row["cli"], model=row["model"], runner=row["runner"])
        if full:
            out.update(result=row["result"], error=row["error"])
            if not public:
                out["instruction"] = row["instruction"]
        else:
            out["preview"] = " ".join(str(row["result"] or row["error"] or row["instruction"] or "").split())[:120]
        return out

    def _one(self, consult_id: int):
        row = self.store.one("SELECT * FROM consults WHERE id=?", (consult_id,))
        if row is None:
            raise DelegatorError(f"unknown consult {consult_id}", "unknown_consult")
        return row

    def result(self, consult_id: int) -> dict[str, Any]:
        """O que a ferramenta MCP devolve (sem revelar a CLI)."""
        return self._row(self._one(consult_id), public=True)

    def get(self, consult_id: int) -> dict[str, Any]:
        return self._row(self._one(consult_id))

    def recent(self, limit: int = 30, agent: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM consults", ()
        if agent:
            sql, params = sql + " WHERE agent=?", (agent,)
        rows = self.store.query(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(limit, 500))))
        return [self._row(r, full=False) for r in rows]

    def cancel(self, consult_id: int) -> dict[str, Any]:
        with self.store.tx() as conn:
            row = conn.execute("SELECT * FROM consults WHERE id=?", (consult_id,)).fetchone()
            if row is None:
                raise DelegatorError(f"unknown consult {consult_id}", "unknown_consult")
            if row["state"] != "pending":
                raise DelegatorError(f"consult {consult_id} is {row['state']}: only pending ones can be cancelled")
            conn.execute("UPDATE consults SET state='cancelled', finished_at=? WHERE id=?", (self._now(), consult_id))
            self._event(
                conn, row["origin"], f"consulta #{consult_id} cancelada", role_to=row["agent"], state="cancelled",
                message_id=consult_id,
            )  # fmt: skip
        return {"consult_id": consult_id, "state": "cancelled"}

    # ------------------------------------------------------------ executor

    def pending(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM consults WHERE state='pending' ORDER BY id LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def claim(self, consult_id: int, runner: str = "") -> dict[str, Any]:
        """Pega o pedido para executar (atômico: só um executor leva; um por vez por agente)."""
        with self.store.tx() as conn:
            row = conn.execute("SELECT * FROM consults WHERE id=?", (consult_id,)).fetchone()
            if row is None:
                raise DelegatorError(f"unknown consult {consult_id}", "unknown_consult")
            if row["state"] != "pending":
                raise DelegatorError(f"consult {consult_id} is already {row['state']}", "not_pending")
            busy = conn.execute("SELECT id FROM consults WHERE agent=? AND state='running'", (row["agent"],)).fetchone()
            if busy is not None:
                raise DelegatorError(f"agent {row['agent']} is busy with consult {busy['id']}", "agent_busy")
            conn.execute(
                "UPDATE consults SET state='running', claimed_at=?, runner=? WHERE id=?",
                (self._now(), runner[:80], consult_id),
            )
            self._event(
                conn, row["origin"], f"consulta #{consult_id}: {row['agent']} começou", role_to=row["agent"],
                state="running", message_id=consult_id, latency_ms=self._latency(row["created_at"]),
            )  # fmt: skip
        return self.get(consult_id)

    def finish(
        self,
        consult_id: int,
        state: str,
        result: str = "",
        error: str = "",
        usage: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        if state not in ("done", "failed"):
            raise DelegatorError("state must be 'done' or 'failed'")
        with self.store.tx() as conn:
            row = conn.execute("SELECT * FROM consults WHERE id=?", (consult_id,)).fetchone()
            if row is None:
                raise DelegatorError(f"unknown consult {consult_id}", "unknown_consult")
            if row["state"] != "running":
                raise DelegatorError(f"consult {consult_id} is {row['state']}, not running", "not_running")
            conn.execute(
                "UPDATE consults SET state=?, result=?, error=?, usage=?, duration_ms=?, finished_at=?, "
                "model=COALESCE(?, model) WHERE id=?",
                (
                    state, result[:MAX_RESULT], error[:2000], json.dumps(usage) if usage else None, duration_ms,
                    self._now(), model, consult_id,
                ),
            )  # fmt: skip
            u = usage or {}
            tokens = sum(
                int(u.get(k) or 0) for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
            )
            extra = f" · {tokens} tokens" if tokens else ""
            verb = "respondeu" if state == "done" else f"falhou: {' '.join(error.split())[:80]}"
            self._event(
                conn, row["origin"], f"consulta #{consult_id}: {row['agent']} {verb}{extra}",
                role_from=row["agent"], role_to=row["origin"] or "?", state=state, message_id=consult_id,
                latency_ms=duration_ms,
            )  # fmt: skip
        return self.get(consult_id)

    def requeue_stale(self, minutes: float = 30.0) -> int:
        """Pedidos `running` há muito tempo (o executor caiu) voltam para a fila."""
        limit = (self._clock() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self.store.tx() as conn:
            cur = conn.execute(
                "UPDATE consults SET state='pending', claimed_at=NULL, runner=NULL "
                "WHERE state='running' AND claimed_at < ?",
                (limit,),
            )
        return cur.rowcount

    # --------------------------------------------------------------- painel

    def overview(self, today: str | None = None, week: str | None = None) -> dict[str, Any]:
        """Uma consulta para a TUI: cada agente com fila, execução atual, números do dia e a última consulta.

        `today` e `week` são instantes ISO (meia-noite local de hoje e de 7 dias atrás, calculados por quem pede)."""
        now = self._clock()
        today = today or (now - timedelta(days=1)).isoformat(timespec="seconds")
        week = week or (now - timedelta(days=7)).isoformat(timespec="seconds")
        out: dict[str, dict[str, Any]] = {}

        def item(agent: str) -> dict[str, Any]:
            return out.setdefault(agent, {
                "pending": 0, "running": None, "today": {"consults": 0, "failed": 0, "cancelled": 0, "input_tokens": 0,
                "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0, "durations": []},
                "week_cost_usd": 0.0, "last": None,
            })  # fmt: skip

        for row in self.store.query("SELECT * FROM consults WHERE state IN ('pending','running') ORDER BY id"):
            it = item(row["agent"])
            if row["state"] == "pending":
                it["pending"] += 1
            else:
                it["running"] = {"consult_id": row["id"], "origin": row["origin"], "since": row["claimed_at"]}
        for row in self.store.query("SELECT * FROM consults WHERE created_at >= ? ORDER BY id", (min(today, week),)):
            it = item(row["agent"])
            usage = self._usage(row["usage"]) or {}
            if row["created_at"] >= week:
                it["week_cost_usd"] += float(usage.get("cost_usd") or 0.0)
            if row["created_at"] < today:
                continue
            day = it["today"]
            day["consults"] += 1
            day["failed"] += row["state"] == "failed"
            day["cancelled"] += row["state"] == "cancelled"
            for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
                day[key] += int(usage.get(key) or 0)
            day["cost_usd"] += float(usage.get("cost_usd") or 0.0)
            if row["state"] == "done" and row["duration_ms"] is not None:
                day["durations"].append(int(row["duration_ms"]))
        for row in self.store.query(
            "SELECT c.* FROM consults c JOIN (SELECT agent, MAX(id) AS id FROM consults "
            "WHERE state IN ('done','failed','cancelled') GROUP BY agent) last ON last.id = c.id"
        ):
            it = item(row["agent"])
            it["last"] = self._row(row, full=False)
        for it in out.values():
            durations = sorted(it["today"].pop("durations"))
            it["today"]["median_ms"] = int(median(durations)) if durations else None
            it["today"]["p90_ms"] = durations[min(len(durations) - 1, int(len(durations) * 0.9))] if durations else None
        return {"agents": out, "runner": self.runner()}

    def timeline(self, agent: str, limit: int = 40) -> list[dict[str, Any]]:
        """Eventos de um agente (os das consultas dele), do mais novo ao mais antigo."""
        rows = self.store.query(
            "SELECT * FROM events WHERE kind='consult' AND (role_to=? OR role_from=?) ORDER BY id DESC LIMIT ?",
            (agent, agent, limit),
        )
        return [dict(r) for r in rows]
