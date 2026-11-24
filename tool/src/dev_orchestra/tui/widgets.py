"""Componentes da TUI: cartão de projeto, indicador do topo e cartão de comandos."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widgets import Static

from ..core import alerts as al
from ..core import state, tasks
from ..core.models import Project
from ..core.summary import LOADING, AgentRow, ProjectSummary, plural
from . import theme as T

_HEALTH_CLASSES = ("loading", "ok", "warn", "idle", "new")


def memory_text(summary: ProjectSummary | None) -> Text:
    """`AI Memory ✓ retomada · 11 workstreams` / `● primeira vez` / `✗ indisponível`."""
    report = summary.report if summary else None
    if report is None:
        return Text("AI Memory …", style=T.C_DIM)
    ai = report.ai_memory
    if ai.reachable is False:
        return Text("AI Memory ✗ indisponível", style=T.C_OFF)
    if ai.project_exists is False:
        return Text("AI Memory ● primeira vez", style=T.C_NEW)
    count = f" · {plural(ai.workstreams, 'workstream')}" if ai.workstreams is not None else ""
    return Text(f"AI Memory ✓ retomada{count}", style=T.C_OK)


class Tile(Static):
    """Indicador do topo da tela inicial: rótulo, valor e uma linha de apoio."""

    def __init__(self, label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.set_content(Text("…", style=T.C_DIM))

    def set_content(self, value: Text, hint: Text | str = "") -> None:
        text = Text()
        text.append(self.label.upper() + "\n", style=f"bold {T.C_MUTED}")
        text.append_text(value)
        if hint:
            text.append("\n")
            text.append_text(hint if isinstance(hint, Text) else Text(hint, style=T.C_MUTED))
        self.update(text)


class ProjectCard(Static, can_focus=True):
    """Cartão de um projeto: selos, agentes com estado, progresso das tarefas e última atividade."""

    # as setas ficam no próprio cartão: o contêiner de rolagem consumiria cima/baixo antes da tela
    BINDINGS = [
        ("enter", "open", "Abrir"),
        Binding("left", "move(-1, 0)", show=False),
        Binding("right", "move(1, 0)", show=False),
        Binding("up", "move(0, -1)", show=False),
        Binding("down", "move(0, 1)", show=False),
    ]

    class Open(Message):
        def __init__(self, project_id: str) -> None:
            super().__init__()
            self.project_id = project_id

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.project_alerts: list[al.Alert] = []
        self.summary: ProjectSummary | None = None

    def on_mount(self) -> None:
        self.set_summary(None)

    def action_open(self) -> None:
        self.post_message(self.Open(self.project.id))

    def action_move(self, dx: int, dy: int) -> None:
        mover = getattr(self.screen, "action_move", None)
        if mover is not None:
            mover(dx, dy)

    # ------------------------------------------------------------ conteúdo

    def set_summary(self, summary: ProjectSummary | None) -> None:
        self.summary = summary
        health = summary.health if summary else "loading"
        for name in _HEALTH_CLASSES:
            self.set_class(name == health, name)
        center = getattr(self.app, "alerts", None) if self.is_attached else None
        self.project_alerts = center.for_project(self.project.id) if center else []
        top = min((a.severity for a in self.project_alerts), key=lambda s: al.SEVERITY_ORDER[s], default=None)
        self.set_class(top == al.CRIT, "alert-crit")
        self.set_class(top == al.WARN, "alert-warn")
        p = self.project
        self.border_title = Text.assemble((f" {p.name} ", f"bold {T.C_STRONG}"), (f" {p.id} ", T.C_MUTED))
        if summary:
            sub = Text(f" ● {summary.opened}/{summary.total} sessões abertas ", style=f"bold {T.HEALTH_TEXT[health]}")
            if self.project_alerts:
                n_crit = sum(a.severity == al.CRIT for a in self.project_alerts)
                n_other = len(self.project_alerts) - n_crit
                parts = ([f"▲ {n_crit}"] if n_crit else []) + ([f"● {n_other}"] if n_other else [])
                color = T.C_OFF if n_crit else T.C_BUSY
                label = f" {' · '.join(parts)} alerta{'s' if len(self.project_alerts) > 1 else ''} "
                sub = Text.assemble((label, f"bold {color}"), ("· ", T.C_DIM), sub)
        else:
            sub = Text(" consultando… ", style=T.C_DIM)
        self.border_subtitle = sub
        self.update(self._body(summary))

    def _body(self, summary: ProjectSummary | None) -> Text:
        p = self.project
        t = Text()
        t.append(f"{p.path}\n", style=T.C_MUTED)
        t.append_text(T.method_badge(p.method))
        t.append(" ")
        t.append_text(T.source_badge(p.task_source.kind))
        t.append(" ")
        if p.delegator:
            t.append_text(T.badge("DELEGADOR", T.C_NEW))
        else:  # fora do padrão do método: precisa saltar aos olhos
            t.append_text(T.badge("SEM DELEGADOR · SendMessage", T.C_BUSY))
        t.append(" ")
        t.append_text(memory_text(summary))
        t.append("\n\n")
        agents = summary.agents if summary else None
        if agents is None:
            for role in p.role_order():
                t.append(f" … {p.agent_name(role)}\n", style=T.C_DIM)
        else:
            width = max(18, *(len(a.name) for a in agents)) + 2
            for a in agents:
                color = T.STATE_COLOR.get(a.state, T.C_UNK)
                t.append(f" {T.STATE_DOT.get(a.state, '?')} ", style=f"bold {color}")
                t.append(a.name.ljust(width), style=f"bold {T.C_STRONG}")
                t.append(f"{a.cli:<7}", style=T.C_OWN if a.cli == "claude" else T.C_TRELLO)
                t.append(f"{a.state:<14}", style=color)
                mine = [x for x in getattr(self, "project_alerts", []) if x.agent == a.name]
                if mine:
                    worst = min(mine, key=lambda x: al.SEVERITY_ORDER[x.severity])
                    color_alert = T.C_OFF if worst.severity == al.CRIT else T.C_BUSY
                    t.append(f" {al.SEVERITY_ICON[worst.severity]} {worst.label}\n", style=f"bold {color_alert}")
                elif a.state == LOADING:
                    t.append("\n")
                elif a.sent:
                    t.append(f"{a.sent_label}\n", style=T.C_MUTED)
                else:
                    t.append("instruções –\n", style=T.C_DIM)
        t.append("\n")
        counts = summary.tasks if summary else None
        if counts is not None:
            t.append("Tarefas   ", style=f"bold {T.C_STRONG}")
            t.append_text(T.bar(counts.done, counts.total))
            t.append(f"  {counts.done}/{counts.total} ({counts.percent}%)\n", style=T.C_TEXT)
            t.append("          ")
            for label, n, color in [
                ("rascunho", counts.draft, T.C_UNK),
                ("prontas", counts.ready, T.C_NEW),
                ("executando", counts.running, T.C_BUSY),
                ("em teste", counts.review, T.C_REV),
                ("com problema", counts.problem, T.C_OFF),
            ]:
                if label == "com problema" and not n:
                    continue
                t.append(f"▌{n} {label}  ", style=color if n else "#4a5563")
            t.append("\n")
            t.append(f"\n⟳ {summary.activity}", style=T.C_MUTED)
        return t


class LaunchCard(Static):
    """Cartão de um terminal na tela de subida (comandos prontos para copiar)."""

    def __init__(self, title: str, subtitle: str, body: Text, color: str) -> None:
        super().__init__()
        self._title, self._subtitle, self._body, self._color = title, subtitle, body, color

    def on_mount(self) -> None:
        self.border_title = Text(f" {self._title} ", style=f"bold {T.C_STRONG}")
        self.border_subtitle = Text(f" {self._subtitle} ", style=f"bold {self._color}")
        self.styles.border = ("round", self._color)
        self.update(self._body)


def activity_text(items: list[dict], warnings: list[str] | None = None) -> Text:
    """Linhas da faixa "Atividade recente" (aviso de tarefas inválidas primeiro)."""
    t = Text()
    for w in warnings or []:
        t.append(f" aviso  {w}\n", style=T.C_BUSY)
    for item in items:
        em = str(item.get("em", ""))
        hora = em[11:16] if len(em) >= 16 else "--:--"
        color = T.ACTIVITY_COLOR.get(str(item.get("tipo", "info")), T.C_NEW)
        t.append(f" {hora}  ", style=T.C_DIM)
        t.append(f"{item.get('texto', '')}\n", style=color)
    if not t.plain:
        t.append(" Nenhuma atividade registrada ainda. Os envios e as importações aparecem aqui.", style=T.C_DIM)
    t.rstrip()
    return t


# ====================================================================== abas, agentes e kanban

TABS = [("overview", "Visão geral"), ("agents", "Agentes"), ("tasks", "Tarefas")]


class TabsBar(Static):
    """Abas do projeto (1 Visão geral · 2 Agentes · 3 Tarefas); clique ou teclas 1, 2 e 3."""

    class Selected(Message):
        def __init__(self, tab: str) -> None:
            super().__init__()
            self.tab = tab

    def __init__(self, project: Project | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.active = "overview"
        self.label = ""
        self.ranges: dict[str, tuple[int, int]] = {}

    def set_active(self, tab: str, label: str = "") -> None:
        self.active = tab
        self.label = label or self.label
        t = Text()
        self.ranges = {}
        for number, (key, name) in enumerate(TABS, start=1):
            start = len(t.plain)
            if key == self.active:
                t.append(f" {number} {name} ", style=f"bold #10141a on {T.C_STRONG}")
            else:
                t.append(f" {number} {name} ", style=f"{T.C_TEXT} on #1b232d")
            self.ranges[key] = (start, len(t.plain))
            t.append("  ")
        if self.label:
            t.append("      " + self.label, style=T.C_MUTED)
        self.update(t)

    def on_click(self, event: Click) -> None:
        for key, (start, end) in self.ranges.items():
            if start <= event.x < end:
                self.post_message(self.Selected(key))
                event.stop()
                return


def _class_for(state_: str) -> str:
    return state_ if state_ in ("ociosa", "ocupada", "fechada") else "desconhecida"


class AgentCard(Static, can_focus=True):
    """Cartão de um agente (lista vertical): papel, CLI, modelo, sessão, instruções, AI Memory e tarefa atual."""

    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        ("enter", "detail", "Detalhes"),
    ]

    def __init__(self, row: AgentRow) -> None:
        super().__init__()
        self.row = row
        self.set_row(row)

    def action_move(self, step: int) -> None:
        mover = getattr(self.screen, "agent_move", None)
        if mover is not None:
            mover(step)

    def action_detail(self) -> None:
        opener = getattr(self.screen, "action_agent_detail", None)
        if opener is not None:
            opener()

    def on_click(self) -> None:
        self.focus()

    def set_row(self, row: AgentRow) -> None:
        self.row = row
        for name in ("ociosa", "ocupada", "fechada", "desconhecida"):
            self.set_class(name == _class_for(row.state), name)
        color = T.STATE_COLOR.get(row.state, T.C_UNK)
        self.border_title = Text.assemble(
            (f" {T.STATE_DOT.get(row.state, '?')} ", f"bold {color}"),
            (f"{row.name} ", f"bold {T.C_STRONG}"),
            (f" {row.state} ", color),
        )
        self.border_subtitle = Text(" i instruções · u comandos · Enter detalhes ", style=T.C_MUTED)
        self.update(self._body(row, color))

    @staticmethod
    def _body(row: AgentRow, color: str) -> Text:
        t = Text()
        t.append_text(T.badge(row.role.upper(), T.C_OWN))
        t.append(" ")
        t.append_text(T.badge(row.cli.upper(), T.C_TRELLO if row.cli == "codex" else "#6c7a89"))
        t.append(f"  modelo {row.model} · esforço {row.effort}", style=T.C_TEXT)
        if row.agent_def:
            t.append(f"  · agente {row.agent_def}", style=T.C_TEXT)
        t.append("\n\n")
        t.append("Sessão       ", style=T.C_MUTED)
        t.append((row.session or "—") + "\n", style=color if row.session else T.C_DIM)
        t.append("Instruções   ", style=T.C_MUTED)
        if row.sent:
            via = f" via {row.via}" if row.via else ""
            t.append(f"{row.sent_label}{via}\n", style=T.C_OK)
        else:
            t.append("pendentes\n", style=T.C_DIM)
        t.append("AI Memory    ", style=T.C_MUTED)
        if row.used:
            t.append(f"workstream {row.name} · {row.used}\n", style=T.C_TEXT)
        elif row.no_workstream:
            t.append("sem workstream ainda\n", style=T.C_DIM)
        else:
            t.append("…\n", style=T.C_DIM)
        if row.deleg:
            color_ = {"ok": T.C_OK, "stale": T.C_BUSY, "none": T.C_BUSY, "down": T.C_OFF}.get(row.deleg_state, T.C_DIM)
            t.append("Delegador    ", style=T.C_MUTED)
            t.append(f"{row.deleg}\n", style=color_)
        t.append("Tarefa       ", style=T.C_MUTED)
        busy = row.task.startswith(("executando", "coordenando"))
        t.append(row.task or "—", style=T.C_BUSY if busy else T.C_TEXT)
        return t


KANBAN_COLUMNS = [
    ("draft", "Rascunho", T.C_UNK, ("draft",)),
    ("ready", "Prontas", T.C_NEW, ("ready",)),
    ("running", "Executando", T.C_BUSY, ("delegated", "running", "asked")),
    ("review", "Em teste", T.C_REV, ("needs_review",)),
    ("done", "Concluídas", T.C_OK, ("done",)),
    ("problem", "Com problema", T.C_OFF, ("partial", "blocked", "failed")),
]
TITLE_LIMIT = 80  # títulos longos são cortados no cartão do kanban
COLUMN_LIMIT = 30  # cartões por coluna (o restante aparece como "+ N mais")


class TaskCard(Static, can_focus=True):
    """Cartão de uma tarefa no kanban. Enter ou clique abre o detalhe."""

    BINDINGS = [
        Binding("left", "move(-1, 0)", show=False),
        Binding("right", "move(1, 0)", show=False),
        Binding("up", "move(0, -1)", show=False),
        Binding("down", "move(0, 1)", show=False),
        ("enter", "open", "Abrir"),
    ]

    class Open(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    def __init__(self, task: tasks.Task, color: str, project_id: str) -> None:
        super().__init__()
        self.task_id = task.id
        t = Text()
        t.append(task.id, style=f"bold {color}")
        t.append("  ")
        t.append(task.role, style=T.C_OWN)
        title = task.title if len(task.title) <= TITLE_LIMIT else task.title[: TITLE_LIMIT - 1].rstrip() + "…"
        t.append(f"\n{title}", style=T.C_TEXT)  # o título inteiro aparece no detalhe
        if task.live:
            t.append(f"\n↻ {task.live} no delegador", style=T.C_NEW)
        info = state.task_sent_info(project_id, task.id)
        if info:
            t.append(f"\n{state.task_sent_label(info)} ✓", style=T.C_MUTED)
        self.update(t)

    def action_move(self, dx: int, dy: int) -> None:
        mover = getattr(self.screen, "board_move", None)
        if mover is not None:
            mover(dx, dy)

    def action_open(self) -> None:
        self.post_message(self.Open(self.task_id))

    def on_click(self) -> None:
        self.focus()
        self.post_message(self.Open(self.task_id))


class KanbanColumn(VerticalScroll):
    pass


class KanbanBoard(Horizontal):
    """Quadro com uma coluna por estado; as colunas vazias continuam visíveis (o fluxo fica claro)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._signature: tuple | None = None

    def populate(self, project_id: str, items: list[tasks.Task]) -> None:
        sent = tuple(state.task_sent_label(state.task_sent_info(project_id, t.id)) for t in items)
        signature = (tuple((t.id, t.state, t.live, t.title, t.role) for t in items), sent)
        if signature == self._signature:
            return
        self._signature = signature
        wanted = self.focused_task_id()
        columns: list[KanbanColumn] = []
        for key, label, color, states in KANBAN_COLUMNS:
            group = [t for t in items if t.state in states]
            if key == "problem" and not group:
                continue
            if key == "done":
                group = group[::-1]  # as concluídas mais recentes primeiro
            children: list[Static] = [TaskCard(t, color, project_id) for t in group[:COLUMN_LIMIT]]
            if len(group) > COLUMN_LIMIT:
                children.append(Static(Text(f"+ {len(group) - COLUMN_LIMIT} mais…", style=T.C_MUTED)))
            column = KanbanColumn(*children)
            column.border_title = Text.assemble(
                (f" {label} ", f"bold {color}"), (f" {len(group)} ", f"bold #10141a on {color}")
            )
            columns.append(column)
        if columns:
            columns[-1].add_class("last")
        self.remove_children()
        self.mount(*columns)
        self.call_after_refresh(self._restore_focus, wanted)

    def _restore_focus(self, wanted: str | None) -> None:
        """Depois de reconstruir o quadro, devolve o foco à tarefa que estava selecionada."""
        if wanted is None:
            return
        target = next((c for c in self.query(TaskCard) if c.task_id == wanted), None)
        if target is not None:
            target.focus()

    def focused_task_id(self) -> str | None:
        focused = self.screen.focused if self.is_attached else None
        return focused.task_id if isinstance(focused, TaskCard) else None

    def first_card(self) -> TaskCard | None:
        return next(iter(self.query(TaskCard)), None)

    def move(self, dx: int, dy: int) -> None:
        columns = [(c, list(c.query(TaskCard))) for c in self.query(KanbanColumn)]
        focused = self.screen.focused
        place = next(((ci, cards.index(focused)) for ci, (_, cards) in enumerate(columns) if focused in cards), None)
        if place is None:
            first = self.first_card()
            if first:
                first.focus()
            return
        ci, ri = place
        if dy:
            cards = columns[ci][1]
            new = ri + dy
            if 0 <= new < len(cards):
                cards[new].focus()
                cards[new].scroll_visible()
            return
        step = 1 if dx > 0 else -1
        cj = ci + step
        while 0 <= cj < len(columns):
            cards = columns[cj][1]
            if cards:
                target = cards[min(ri, len(cards) - 1)]
                target.focus()
                target.scroll_visible()
                return
            cj += step


__all__ = [
    "AgentCard",
    "KanbanBoard",
    "LaunchCard",
    "ProjectCard",
    "TabsBar",
    "TaskCard",
    "Tile",
    "activity_text",
    "memory_text",
]
