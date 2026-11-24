"""Quem pode chamar o quê (tabela de permissões do delegador, docs/SUGESTAO-ESPECIFICACAO-DELEGADOR.md seção 2)."""

from __future__ import annotations

from .errors import DelegatorError

ORCHESTRATOR = "orchestrator"
WORKER = "worker"  # qualquer papel que não seja o orquestrador (developer, qa, planner, especialistas)
PERSON = "person"  # a pessoa, pela TUI/CLI (não é uma sessão)

ALLOWED: dict[str, set[str]] = {
    "register_session": {ORCHESTRATOR, WORKER},
    "heartbeat": {ORCHESTRATOR, WORKER},
    "delegate": {ORCHESTRATOR},
    "receive": {ORCHESTRATOR, WORKER},
    "report": {WORKER},
    "ask": {WORKER},
    "answer": {ORCHESTRATOR},
    "cancel_task": {ORCHESTRATOR, PERSON},
    "project_status": {ORCHESTRATOR, WORKER, PERSON},
}

# ferramentas em que o worker só age sobre a tarefa que ele recebeu
OWN_TASK_ONLY = {"report", "ask", "project_status"}


def kind_of(role: str) -> str:
    return ORCHESTRATOR if role == ORCHESTRATOR else WORKER


def check(tool: str, role_kind: str, role: str = "") -> None:
    if role_kind not in ALLOWED.get(tool, set()):
        who = role or role_kind
        raise DelegatorError(
            f"role '{who}' is not allowed to call {tool} (allowed: {', '.join(sorted(ALLOWED[tool]))})", "forbidden"
        )
