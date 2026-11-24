"""Resumo de cada projeto para as telas (cartões, painéis, passo a passo). Lógica pura, sem terminal."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from . import state, tasks
from .liveness import OCIOSA, OCUPADA, Services, StatusReport, collect_status
from .models import GlobalConfig, Project

LOADING = "consultando"


def plural(n: int, singular: str, many: str | None = None) -> str:
    """`1 workstream`, `3 workstreams` (aceita a forma plural irregular)."""
    return f"{n} {singular if n == 1 else (many or singular + 's')}"


# estados de tarefa agrupados para contadores
_GROUPS = {
    "draft": ("draft",),
    "ready": ("ready",),
    "running": ("delegated", "running", "asked"),
    "review": ("needs_review",),
    "problem": ("partial", "blocked", "failed"),
    "done": ("done",),
}


@dataclass(frozen=True)
class TaskCounts:
    draft: int = 0
    ready: int = 0
    running: int = 0
    review: int = 0
    problem: int = 0
    done: int = 0
    cancelled: int = 0

    @property
    def total(self) -> int:
        """Tarefas contadas no progresso (as canceladas ficam de fora)."""
        return self.draft + self.ready + self.running + self.review + self.problem + self.done

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0


# Estado no delegador -> estado mostrado. O DONE do worker ainda é uma afirmação: vale "em teste" até o orquestrador
# conferir e gravar `done` no arquivo da tarefa.
LIVE_STATE = {
    "delegated": "delegated",
    "running": "running",
    "asked": "asked",
    "done": "needs_review",
    "partial": "partial",
    "blocked": "blocked",
    "failed": "failed",
    "needs_review": "needs_review",
    "cancelled": "cancelled",
}


def apply_live(items: list[tasks.Task], delegator_tasks: list[dict] | None) -> list[tasks.Task]:
    """Sobrepõe o estado de execução do delegador ao do arquivo (o orquestrador só grava o arquivo nos marcos).

    Tarefas já `done` ou `cancelled` no arquivo não mudam: o arquivo é a palavra final."""
    if not delegator_tasks:
        return items
    live = {t["task_id"]: t["state"] for t in delegator_tasks}
    out: list[tasks.Task] = []
    for t in items:
        raw = live.get(t.id)
        mapped = LIVE_STATE.get(raw or "")
        if mapped and t.state not in ("done", "cancelled") and mapped != t.state:
            out.append(replace(t, state=mapped, live=raw))
        else:
            out.append(t)
    return out


def short_detail(state_: str, detail: str) -> str:
    """Resumo do detalhe do status para a tabela (o texto completo aparece abaixo dela)."""
    parts = [p.strip() for p in detail.split(";")]
    if state_ in (OCIOSA, OCUPADA):
        return parts[0].split(",")[0]
    used = next((p for p in parts if p.startswith("workstream usado")), "")
    return used.replace("workstream usado", "usado") if used else ""


def task_counts(project: Project, items: list[tasks.Task] | None = None) -> TaskCounts:
    if items is None:
        items, _ = tasks.list_tasks(project)
    counts = {key: 0 for key in _GROUPS}
    cancelled = 0
    for t in items:
        if t.state == "cancelled":
            cancelled += 1
            continue
        for key, states in _GROUPS.items():
            if t.state in states:
                counts[key] += 1
                break
    return TaskCounts(cancelled=cancelled, **counts)


@dataclass(frozen=True)
class AgentRow:
    role: str
    name: str
    cli: str
    model: str
    state: str  # ociosa | ocupada | fechada | desconhecida | erro | consultando
    sent_label: str  # `enviadas 14/11 09:30` ou `pendentes`
    sent_short: str  # `✓ 14/11 09:30` ou `pendentes` (tabelas)
    sent: bool
    detail: str
    detail_short: str = ""  # cabe na tabela: `pid 4052400` ou `usado 1 month ago`
    effort: str = "-"
    agent_def: str = ""  # definição de agente do Claude Code (`--agent`), se houver
    session: str = ""  # `pid 4052400, status idle` (sessão aberta)
    used: str = ""  # `usado 2 minutes ago` (último uso do workstream no AI Memory)
    no_workstream: bool = False
    via: str = ""  # SendMessage | codex queue
    task: str = ""  # tarefa em que o agente está
    deleg_state: str = ""  # "" (não usa) | down | none | stale | ok
    deleg: str = ""  # texto: `registrado · sinal há 5 s`


@dataclass
class ProjectSummary:
    project: Project
    report: StatusReport | None
    agents: list[AgentRow]
    tasks: TaskCounts
    opened: int
    sent: int
    health: str  # loading | ok | warn | idle | new
    activity: str
    recent: list[dict] = field(default_factory=list)
    task_items: list[tasks.Task] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.agents)


def _delegator_row(
    project: Project, report: StatusReport | None, role: str, session_state: str = ""
) -> tuple[str, str]:
    """(estado, texto) do agente no delegador: down, none (não registrou), stale (sem sinal) ou ok."""
    if not project.delegator:
        return "", ""
    if report is None:
        return "", "consultando…"
    if report.delegator is None:
        return "down", "delegador fora do ar"
    mine = [s for s in report.delegator.get("sessions", []) if s["role"] == role and s["active"]]
    if not mine:
        return "none", "ainda não se registrou"
    s = mine[0]
    seen = state.ago(s["last_seen"])
    pending = f" · {s['pending']} pendente(s)" if s.get("pending") else ""
    if s["alive"]:
        return "ok", f"registrado · sinal {seen}{pending}"
    if session_state == OCUPADA:  # o listener só volta a rodar entre turnos; uma sessão ocupada não é "sem sinal"
        return "ok", f"registrado · trabalhando{pending}"
    return "stale", f"registrado, sem sinal há {seen.removeprefix('há ')}{pending}"


def current_task(role: str, items: list[tasks.Task]) -> str:
    """Em que o agente está: a tarefa em execução (o orquestrador coordena todas) ou quantas aguardam."""
    running = [
        t for t in items if t.state in ("delegated", "running", "asked") and (role == "orchestrator" or t.role == role)
    ]
    if running:
        first = running[0]
        more = f" (+{len(running) - 1})" if len(running) > 1 else ""
        verb = "coordenando" if role == "orchestrator" else "executando"
        return f"{verb} {first.id} {first.title}{more}"
    waiting = [t for t in items if t.state == "ready" and t.role == role]
    if waiting:
        return f"aguardando tarefa ({plural(len(waiting), 'pronta')})"
    return "—"


def build_summary(project: Project, report: StatusReport | None) -> ProjectSummary:
    by_role = {r.role: r for r in report.roles} if report else {}
    task_items, _ = tasks.list_tasks(project)
    task_items = apply_live(task_items, report.delegator.get("tasks") if report and report.delegator else None)
    agents: list[AgentRow] = []
    for role in project.role_order():
        name = project.agent_name(role)
        cfg = project.roles[role]
        info = state.sent_info(project.id, name, state.comm_mode(project.delegator))
        status = by_role.get(role)
        parts = [p.strip() for p in status.detail.split(";")] if status else []
        is_open = bool(status) and status.state in (OCIOSA, OCUPADA)
        deleg_state, deleg_text = _delegator_row(project, report, role, status.state if status else "")
        agents.append(
            AgentRow(
                role=role,
                name=name,
                cli=cfg.cli,
                model=cfg.default_model or "-",
                state=status.state if status else LOADING,
                sent_label=state.describe(info),
                sent_short=state.describe_short(info),
                sent=info is not None,
                detail=status.detail if status else "",
                detail_short=short_detail(status.state, status.detail) if status else "",
                effort=cfg.default_effort or "-",
                agent_def=cfg.agent or "",
                session=parts[0] if is_open else "",
                used=next(
                    (p.replace("workstream usado", "usado") for p in parts if p.startswith("workstream usado")), ""
                ),
                no_workstream=any(p.startswith("sem workstream") for p in parts),
                via=str(info.get("via", "")) if info else "",
                task=current_task(role, task_items),
                deleg_state=deleg_state,
                deleg=deleg_text,
            )
        )
    opened = sum(a.state in (OCIOSA, OCUPADA) for a in agents)
    sent = sum(a.sent for a in agents)
    if report is None:
        health = "loading"
    elif report.ai_memory.project_exists is False:
        health = "new"
    elif opened == len(agents):
        health = "ok"
    elif opened == 0:
        health = "idle"
    else:
        health = "warn"
    recent = _merge_activity(state.recent_activity(project.id, 6), report.delegator_events if report else [])
    return ProjectSummary(
        project=project,
        report=report,
        agents=agents,
        tasks=task_counts(project, task_items),
        opened=opened,
        sent=sent,
        health=health,
        activity=_activity_text(agents, recent),
        recent=recent,
        task_items=task_items,
    )


def _local_iso(iso: str) -> str:
    """Eventos do delegador vêm em UTC; a lista de atividade mostra a hora local."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).astimezone().isoformat(timespec="seconds")
    except ValueError:
        return iso


def _merge_activity(local: list[dict], remote: list[dict]) -> list[dict]:
    """Atividade da TUI junto com os eventos do delegador, mais recente primeiro (6 itens)."""
    extra = [
        {"em": _local_iso(str(e.get("ts", ""))), "texto": f"[delegador] {e.get('text', '')}", "tipo": "info"}
        for e in remote
    ]
    return sorted([*local, *extra], key=lambda i: str(i.get("em", "")), reverse=True)[:6]


def _activity_text(agents: list[AgentRow], recent: list[dict]) -> str:
    if recent:
        last = recent[0]
        return f"{last.get('texto', '')} ({state.ago(last.get('em'))})"
    busy = [a.name for a in agents if a.state == OCUPADA]
    if busy:
        return f"{busy[0]} ocupada"
    open_ = [a.name for a in agents if a.state == OCIOSA]
    if open_:
        return f"{open_[0]} ociosa"
    if any(a.state == LOADING for a in agents):
        return "consultando as sessões…"
    return "nenhuma sessão aberta; tecle Enter e depois u para subir"


def next_step(summary: ProjectSummary) -> str:
    """Sugestão curta do que fazer a seguir (mostrada no passo a passo)."""
    p = summary.project
    if summary.report is None:
        return "consultando o estado das sessões…"
    closed = [a.name for a in summary.agents if a.state not in (OCIOSA, OCUPADA)]
    if closed:
        shown = ", ".join(closed[:3]) + ("…" if len(closed) > 3 else "")
        return f"abra {shown} (tecle u para ver os comandos)"
    if summary.sent < summary.total:
        return "tecle i para enviar as instruções aos agentes"
    t = summary.tasks
    if p.task_source.kind == "trello" and t.total == 0 and state.trello_import_info(p.id) is None:
        return "tecle y para trazer os cartões do Trello para arquivos locais"
    if t.ready or t.draft:
        return "selecione uma tarefa e tecle g para enviá-la ao orquestrador"
    if t.total == 0:
        return "crie uma tarefa com t"
    return "acompanhe as tarefas; tecle g para enviar uma nova"


@dataclass(frozen=True)
class Infra:
    ai_memory_ok: bool | None = None
    app_server: bool | None = None  # None = nenhum projeto usa o Codex
    app_server_url: str = ""
    delegator: bool | None = None  # None = nenhum projeto usa o delegador
    delegator_url: str = ""


def collect_all(
    projects: list[Project], cfg: GlobalConfig, services: Services | None = None
) -> tuple[dict[str, StatusReport], Infra]:
    """Status de todos os projetos, consultando `claude agents` e o `ai-memory list-projects` uma vez só."""
    services = services or Services()
    claude = services.claude_sessions() if any(r.cli == "claude" for p in projects for r in p.roles.values()) else None
    known = services.ai_memory_projects()
    shared = Services(
        claude_sessions=lambda: claude,
        codex_threads=services.codex_threads,
        workstreams=services.workstreams,
        ai_memory_projects=lambda: known,
        scan_project=services.scan_project,
        delegator_status=services.delegator_status,
        delegator_events=services.delegator_events,
    )
    reports: dict[str, StatusReport] = {}
    if projects:
        with ThreadPoolExecutor(max_workers=min(4, len(projects))) as pool:
            for project, report in zip(
                projects, pool.map(lambda p: collect_status(p, cfg, shared), projects), strict=True
            ):
                reports[project.id] = report
    states = [r.app_server_up for r in reports.values() if r.app_server_up is not None]
    app_server = None if not states else any(states)
    used = [r.delegator is not None for r in reports.values() if r.delegator_used]
    return reports, Infra(
        ai_memory_ok=known is not None,
        app_server=app_server,
        app_server_url=cfg.app_server_url,
        delegator=any(used) if used else None,
        delegator_url=cfg.delegator_url,
    )
