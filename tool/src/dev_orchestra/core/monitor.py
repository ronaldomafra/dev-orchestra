"""Dados da tela Monitor: junta os eventos do delegador e a atividade local da TUI numa linha do tempo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import state
from .liveness import Services
from .models import GlobalConfig, Project

# tipo -> (ícone, rótulo, grupo do filtro)
KINDS: dict[str, tuple[str, str]] = {
    "delegate": ("➜", "delegar"),
    "report": ("◀", "resultado"),
    "ask": ("?", "pergunta"),
    "answer": ("✓", "resposta"),
    "cancel": ("✕", "cancelar"),
    "register": ("●", "registro"),
    "refused": ("⚠", "recusado"),
    "alert": ("⚑", "alerta"),
    "receive": ("↓", "recebeu"),
    "profile": ("◇", "perfil"),
    "tui": ("▪", "TUI"),
}
FILTER_ORDER = ["delegate", "report", "ask", "answer", "cancel", "register", "refused", "alert", "receive", "tui"]
# presets da tecla f
PRESETS: dict[str, set[str]] = {
    "todos": set(FILTER_ORDER),
    "atenção": {"ask", "refused", "cancel", "alert"},
    "fluxo": {"delegate", "report", "answer", "ask"},
}
BLOCKING = {"ask", "refused", "cancel", "alert"}  # o que para ou desvia o trabalho de um agente


@dataclass
class Entry:
    key: str
    when: datetime  # com fuso local
    project: str
    kind: str
    text: str
    role_from: str | None = None
    role_to: str | None = None
    task: str | None = None
    state: str | None = None
    attempt: int | None = None
    message_id: int | None = None
    latency_ms: int | None = None
    source: str = "delegador"

    @property
    def hhmmss(self) -> str:
        return self.when.strftime("%H:%M:%S")

    @property
    def route(self) -> str:
        if self.role_from and self.role_to:
            return f"{self.role_from} → {self.role_to}"
        return self.role_from or self.role_to or ""

    @property
    def latency(self) -> str:
        if self.latency_ms is None:
            return ""
        return f"+{self.latency_ms / 1000:.0f} s" if self.latency_ms >= 950 else f"+{self.latency_ms} ms"


def _local(iso: str) -> datetime:
    try:
        return datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return datetime.now().astimezone()


def entry_from_event(project: str, e: dict[str, Any]) -> Entry:
    return Entry(
        key=f"{project}:{e['id']}",
        when=_local(str(e.get("ts", ""))),
        project=project,
        kind=str(e.get("kind", "?")),
        text=str(e.get("text", "")),
        role_from=e.get("role_from"),
        role_to=e.get("role_to"),
        task=e.get("task_id"),
        state=e.get("state"),
        attempt=e.get("attempt"),
        message_id=e.get("message_id"),
        latency_ms=e.get("latency_ms"),
    )


def entry_from_activity(project: str, index: int, item: dict[str, Any]) -> Entry:
    return Entry(
        key=f"tui:{project}:{item.get('em')}:{index}",
        when=_local(str(item.get("em", ""))),
        project=project,
        kind="tui",
        text=str(item.get("texto", "")),
        role_from="pessoa",
        source="tui",
    )


def _timeline(services: Services, url: str, project: str) -> list[dict] | None:
    if services.delegator_timeline is not None:
        return services.delegator_timeline(url, project)
    from ..delegator.client import DelegatorClient, DelegatorUnavailable

    try:
        return DelegatorClient(url, timeout=2.0).timeline(project, 120)
    except (DelegatorUnavailable, RuntimeError, ValueError):
        return None


def _stats(services: Services, url: str, project: str | None) -> dict | None:
    if services.delegator_stats is not None:
        return services.delegator_stats(url, project)
    from ..delegator.client import DelegatorClient, DelegatorUnavailable

    try:
        return DelegatorClient(url, timeout=2.0).stats(project)
    except (DelegatorUnavailable, RuntimeError, ValueError):
        return None


def message_for(services: Services | None, url: str, project: str, message_id: int) -> dict | None:
    services = services or Services()
    if services.delegator_message is not None:
        return services.delegator_message(url, project, message_id)
    from ..delegator.client import DelegatorClient, DelegatorUnavailable

    try:
        return DelegatorClient(url, timeout=2.0).message(project, message_id)
    except (DelegatorUnavailable, RuntimeError, ValueError):
        return None


def task_history_for(services: Services | None, url: str, project: str, task: str) -> list[dict]:
    services = services or Services()
    if services.delegator_task is not None:
        return services.delegator_task(url, project, task)
    from ..delegator.client import DelegatorClient, DelegatorUnavailable

    try:
        return DelegatorClient(url, timeout=2.0).task_history(project, task)
    except (DelegatorUnavailable, RuntimeError, ValueError):
        return []


@dataclass
class ProjectLine:
    project: Project
    delegator_up: bool | None  # None = o projeto não usa o delegador
    stats: dict | None
    last: Entry | None = None

    @property
    def health(self) -> str:
        if not self.project.delegator:
            return "idle"
        if self.stats is None:
            return "off"
        if not self.stats["sessions"]:
            return "idle"
        return "ok" if self.stats["alive"] == self.stats["sessions"] else "warn"


@dataclass
class MonitorData:
    entries: list[Entry]
    lines: list[ProjectLine]
    stats: dict | None  # do projeto selecionado (ou de todos)
    delegator_up: bool | None


def load(
    projects: list[Project],
    cfg: GlobalConfig,
    services: Services | None,
    selected: str | None,
    *,
    local_limit: int = 30,
) -> MonitorData:
    services = services or Services()
    entries: list[Entry] = []
    lines: list[ProjectLine] = []
    reachable: list[bool] = []
    for p in projects:
        mine: list[Entry] = []
        stats = None
        if p.delegator:
            events = _timeline(services, cfg.delegator_url, p.id)
            reachable.append(events is not None)
            if events is not None:
                mine += [entry_from_event(p.id, e) for e in events]
                stats = _stats(services, cfg.delegator_url, p.id)
        mine += [entry_from_activity(p.id, i, a) for i, a in enumerate(state.recent_activity(p.id, local_limit))]
        mine.sort(key=lambda e: e.when, reverse=True)
        entries += mine
        lines.append(ProjectLine(p, (events is not None) if p.delegator else None, stats, mine[0] if mine else None))
    entries.sort(key=lambda e: e.when, reverse=True)
    wanted = {p.id for p in projects if p.delegator}
    scope = selected if selected in wanted else None
    overall = _stats(services, cfg.delegator_url, scope) if reachable and any(reachable) else None
    return MonitorData(entries, lines, overall, (any(reachable) if reachable else None))


def apply_filters(entries: list[Entry], project: str | None, kinds: set[str], text: str = "") -> list[Entry]:
    needle = text.strip().lower()
    out = []
    for e in entries:
        if project and e.project != project:
            continue
        if e.kind not in kinds:
            continue
        if (
            needle
            and needle
            not in " ".join(
                str(x) for x in (e.text, e.project, e.task, e.role_from, e.role_to, e.kind, e.state) if x
            ).lower()
        ):
            continue
        out.append(e)
    return out
