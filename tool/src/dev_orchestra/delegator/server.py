"""Servidor do delegador: ferramentas MCP (HTTP) e uma pequena API para a TUI, a CLI e os listeners."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core import config, paths
from ..core.models import Project
from . import DEFAULT_PORT, MCP_NAME
from .core import Delegator, Registry
from .db import Store
from .errors import DelegatorError
from .wake import RoutingWaker


class ConfigRegistry:
    """Lê o cadastro dos projetos (pasta de instalação montada no container) a cada consulta."""

    def get(self, project_id: str) -> Project | None:
        try:
            return config.load_project(project_id)
        except config.ConfigError:
            return None


def make_delegator(db_path: Path, registry: Registry | None = None, install: Path | None = None) -> Delegator:
    return Delegator(
        Store(db_path),
        registry or ConfigRegistry(),
        RoutingWaker(),
        install=install if install is not None else paths.install_dir(),
    )


def build_app(delegator: Delegator, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
    """Monta o servidor FastMCP (ferramentas em inglês, como decidido) e as rotas `/healthz` e `/api/*`."""
    try:
        from mcp.server.fastmcp import FastMCP
        from starlette.requests import Request
        from starlette.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "o delegador precisa do pacote 'mcp': instale com `uv tool install 'dev-orchestra[delegator]'`"
        ) from exc

    mcp = FastMCP(MCP_NAME, host=host, port=port, stateless_http=True, json_response=True)

    # -------------------------------------------------------------- ferramentas
    @mcp.tool()
    def register_session(project: str, role: str, cli: str, session_ref: str) -> dict[str, Any]:
        """Register this session with the delegator. Call it first, when the session opens (and again if you lose
        your context). `session_ref` is the session name (Claude Code) or the thread UUID (Codex). Returns the
        `session_id` that every other tool requires, and the profile (instructions) of your role."""
        return delegator.register_session(project, role, cli, session_ref)

    @mcp.tool()
    def heartbeat(session_id: str) -> dict[str, Any]:
        """Tell the delegator this session is alive. Returns the number of pending messages."""
        return delegator.heartbeat(session_id)

    @mcp.tool()
    def delegate(
        session_id: str,
        task_id: str,
        to_role: str,
        instruction: str,
        model: str,
        effort: str,
        model_reason: str = "",
        user_approved: bool = False,
    ) -> dict[str, Any]:
        """(Orchestrator only) Delegate a task to a worker role. `instruction` must be self-contained (objective, scope,
        acceptance criteria, constraints, expected evidence). `user_approved` must be true: the person approved the
        model and effort. After this call, END YOUR TURN: the result arrives as a new message."""
        return delegator.delegate(session_id, task_id, to_role, instruction, model, effort, model_reason, user_approved)

    @mcp.tool()
    def receive(session_id: str, message_id: int | None = None) -> dict[str, Any]:
        """Get the next message addressed to your role (or a specific `message_id`). Messages are single-use.
        Types: delegate (your task), result, question, answer, cancel, profile. `message` is null when there is none."""
        return delegator.receive(session_id, message_id)

    @mcp.tool()
    def report(
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
        """(Worker only, your own task) Send the result. `status` is DONE, PARTIAL, BLOCKED, FAILED or NEEDS_REVIEW.
        Keep it brief and reference files for long content. After this call, END YOUR TURN."""
        return delegator.report(
            session_id, task_id, status, summary, evidence, changes, validation, risks, next_step, git
        )

    @mcp.tool()
    def ask(session_id: str, task_id: str, question: str, options: list[str] | None = None) -> dict[str, Any]:
        """(Worker only, your own task) Ask the orchestrator instead of guessing. The task becomes 'asked'.
        After this call, END YOUR TURN: the answer arrives as a new message."""
        return delegator.ask(session_id, task_id, question, options)

    @mcp.tool()
    def answer(session_id: str, question_id: int, answer: str) -> dict[str, Any]:
        """(Orchestrator only) Answer a worker's question. The answer goes to the same worker."""
        return delegator.answer(session_id, question_id, answer)

    @mcp.tool()
    def cancel_task(session_id: str, task_id: str, reason: str = "") -> dict[str, Any]:
        """(Orchestrator only) Cancel a delegated, running or asked task. The worker is told to stop."""
        return delegator.cancel_task(session_id, task_id, reason)

    @mcp.tool()
    def project_status(session_id: str) -> str:
        """Plain-text status: sessions, tasks and states. Workers only see their own task."""
        return delegator.project_status(session_id)

    # --------------------------------------------------- agentes passivos (consultas)
    @mcp.tool()
    def list_agents() -> list[dict[str, Any]]:
        """List the passive agents you can consult (id and description). A passive agent answers one request at a
        time and keeps no open session; `runner_alive` false means requests wait in the queue."""
        return delegator.consults.list_agents()

    @mcp.tool()
    def consult(agent_id: str, instruction: str, origin: str = "") -> dict[str, Any]:
        """Send a request to a passive agent. Only its id is needed: the delegator chooses how it runs.
        `instruction` must be self-contained (include the data to analyse); the answer format is defined by the agent.
        `origin` labels who is asking (project or system), for the history and the cost. Returns `consult_id`;
        get the answer with `consult_result`. Requests to the same agent are queued and answered one at a time."""
        return delegator.consults.consult(agent_id, instruction, origin)

    @mcp.tool()
    def consult_result(consult_id: int) -> dict[str, Any]:
        """State of a consult: pending, running, done, failed or cancelled. When done, `result` holds the answer and
        `usage` the tokens consumed. Do not poll in a tight loop: wait a few seconds between calls."""
        return delegator.consults.result(consult_id)

    # ------------------------------------------------------------------- rotas
    def _json(payload: Any, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status)

    def _guard(fn):
        try:
            return _json(fn())
        except DelegatorError as exc:
            return _json({"error": str(exc), "code": exc.code}, 400)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        return _json({"ok": True, "service": MCP_NAME})

    @mcp.custom_route("/api/pending", methods=["GET"])
    async def api_pending(request: Request) -> JSONResponse:
        q = request.query_params
        return _guard(lambda: delegator.pending_for_agent(q.get("project", ""), q.get("agent", "")))

    @mcp.custom_route("/api/status", methods=["GET"])
    async def api_status(request: Request) -> JSONResponse:
        return _guard(lambda: delegator.status_data(request.query_params.get("project", "")))

    @mcp.custom_route("/api/events", methods=["GET"])
    async def api_events(request: Request) -> JSONResponse:
        q = request.query_params
        return _guard(lambda: {"events": delegator.events(q.get("project", ""), int(q.get("limit", "30")))})

    @mcp.custom_route("/api/timeline", methods=["GET"])
    async def api_timeline(request: Request) -> JSONResponse:
        q = request.query_params
        project, limit, after = q.get("project") or None, int(q.get("limit", "200")), int(q.get("after", "0"))
        return _guard(lambda: {"events": delegator.timeline(project, limit, after)})

    @mcp.custom_route("/api/message", methods=["GET"])
    async def api_message(request: Request) -> JSONResponse:
        q = request.query_params
        return _guard(lambda: delegator.message(q.get("project", ""), int(q.get("id", "0"))))

    @mcp.custom_route("/api/task", methods=["GET"])
    async def api_task(request: Request) -> JSONResponse:
        q = request.query_params
        return _guard(lambda: {"history": delegator.task_history(q.get("project", ""), q.get("task", ""))})

    @mcp.custom_route("/api/stats", methods=["GET"])
    async def api_stats(request: Request) -> JSONResponse:
        return _guard(lambda: delegator.stats(request.query_params.get("project") or None))

    @mcp.custom_route("/api/signal", methods=["POST"])
    async def api_signal(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(
            lambda: delegator.signal(
                data.get("project", ""), data.get("agent", ""), data.get("kind", ""), data.get("message", ""),
                data.get("detail", ""),
            )
        )  # fmt: skip

    @mcp.custom_route("/api/signal/clear", methods=["POST"])
    async def api_signal_clear(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(lambda: delegator.clear_signals(data.get("project", ""), data.get("agent", "")))

    @mcp.custom_route("/api/alerts", methods=["GET"])
    async def api_alerts(request: Request) -> JSONResponse:
        return _guard(lambda: {"alerts": delegator.active_signals(request.query_params.get("project") or None)})

    @mcp.custom_route("/api/cancel", methods=["POST"])
    async def api_cancel(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(
            lambda: delegator.cancel_task_as_person(
                data.get("project", ""), data.get("task_id", ""), data.get("reason", "")
            )
        )

    @mcp.custom_route("/api/profile", methods=["POST"])
    async def api_profile(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(lambda: delegator.push_profile(data.get("project", ""), data.get("role", "")))

    # ---- agentes passivos: o executor (host) bate, pega e devolve; a TUI acompanha
    @mcp.custom_route("/api/passive/heartbeat", methods=["POST"])
    async def api_passive_heartbeat(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(
            lambda: delegator.consults.heartbeat(data.get("name", ""), data.get("pid"), list(data.get("agents", [])))
        )

    @mcp.custom_route("/api/passive/pending", methods=["GET"])
    async def api_passive_pending(request: Request) -> JSONResponse:
        return _guard(lambda: {"consults": delegator.consults.pending()})

    @mcp.custom_route("/api/passive/claim", methods=["POST"])
    async def api_passive_claim(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(lambda: delegator.consults.claim(int(data.get("id", 0)), data.get("runner", "")))

    @mcp.custom_route("/api/passive/finish", methods=["POST"])
    async def api_passive_finish(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(
            lambda: delegator.consults.finish(
                int(data.get("id", 0)), data.get("state", ""), data.get("result", ""), data.get("error", ""),
                data.get("usage"), data.get("duration_ms"), data.get("model"),
            )
        )  # fmt: skip

    @mcp.custom_route("/api/passive/requeue", methods=["POST"])
    async def api_passive_requeue(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(lambda: {"requeued": delegator.consults.requeue_stale(float(data.get("minutes", 30)))})

    @mcp.custom_route("/api/passive/overview", methods=["GET"])
    async def api_passive_overview(request: Request) -> JSONResponse:
        q = request.query_params
        return _guard(lambda: delegator.consults.overview(q.get("today") or None, q.get("week") or None))

    @mcp.custom_route("/api/passive/consults", methods=["GET"])
    async def api_passive_consults(request: Request) -> JSONResponse:
        q = request.query_params
        return _guard(
            lambda: {"consults": delegator.consults.recent(int(q.get("limit", "30")), q.get("agent") or None)}
        )

    @mcp.custom_route("/api/passive/consult", methods=["GET"])
    async def api_passive_consult(request: Request) -> JSONResponse:
        return _guard(lambda: delegator.consults.get(int(request.query_params.get("id", "0"))))

    @mcp.custom_route("/api/passive/timeline", methods=["GET"])
    async def api_passive_timeline(request: Request) -> JSONResponse:
        q = request.query_params
        return _guard(lambda: {"events": delegator.consults.timeline(q.get("agent", ""), int(q.get("limit", "40")))})

    @mcp.custom_route("/api/passive/cancel", methods=["POST"])
    async def api_passive_cancel(request: Request) -> JSONResponse:
        data = json.loads((await request.body()) or b"{}")
        return _guard(lambda: delegator.consults.cancel(int(data.get("id", 0))))

    return mcp


def serve(host: str, port: int, db_path: Path) -> None:
    delegator = make_delegator(db_path)
    build_app(delegator, host=host, port=port).run(transport="streamable-http")
