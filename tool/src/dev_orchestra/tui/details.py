"""Janelas de detalhe: tarefa (kanban) e agente (lista de cartões)."""

from __future__ import annotations

import re

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..core import state, tasks
from ..core.models import Project
from ..core.summary import LIVE_STATE, AgentRow
from . import theme as T


def _key(label: str, key: str) -> Text:
    t = Text()
    t.append(f" {key} ", style=f"bold #10141a on {T.C_STRONG}")
    t.append(f" {label}   ", style=T.C_TEXT)
    return t


def body_text(body: str) -> Text:
    """Mostra o corpo Markdown da tarefa: títulos em destaque e a lista de critérios como caixas."""
    t = Text()
    for line in body.splitlines():
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        done = re.match(r"^\s*[-*]\s+\[[xX]\]\s+(.*)$", line)
        todo = re.match(r"^\s*[-*]\s+\[\s\]\s+(.*)$", line)
        if heading:
            t.append(f"{heading.group(1)}\n", style=f"bold {T.C_STRONG}")
        elif done:
            t.append(f"  ☑ {done.group(1)}\n", style=T.C_OK)
        elif todo:
            t.append(f"  ☐ {todo.group(1)}\n", style=T.C_TEXT)
        else:
            t.append(f"{line}\n", style=T.C_TEXT)
    t.rstrip()
    return t


class TaskDetailScreen(ModalScreen["str | None"]):
    """Detalhe de uma tarefa. Devolve "send" (g), "state" (s) ou None (Esc)."""

    BINDINGS = [("escape", "close", "Fechar"), ("g", "pick('send')", "Enviar"), ("s", "pick('state')", "Estado")]

    def __init__(self, project: Project, task_id: str, live: str | None = None) -> None:
        super().__init__()
        self.project = project
        self.task_id = task_id
        self.live = live  # estado no delegador, se ele estiver à frente do arquivo

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form"):
            yield Static(self._content(), id="task-detail")

    def _content(self) -> Text:
        try:
            task = tasks.read_task(self.project, self.task_id)
        except tasks.TaskError as exc:
            return Text(f"Não foi possível ler a tarefa {self.task_id}: {exc}", style=T.C_OFF)
        shown = LIVE_STATE.get(self.live or "", task.state) if self.live else task.state
        label, color = T.TASK_STATE.get(shown, (shown, T.C_UNK))
        t = Text()
        t.append(f"{task.id} ", style=f"bold {color}")
        t.append(f"{task.title}\n", style=f"bold {T.C_STRONG}")
        t.append_text(T.badge(label.upper(), color))
        t.append(" ")
        t.append_text(T.badge(task.role.upper(), T.C_OWN))
        if task.trello:
            t.append(" ")
            t.append_text(T.badge("TRELLO", T.C_TRELLO))
        extra = []
        if task.list_name:
            extra.append(f"lista: {task.list_name}")
        extra.append(f"criada {task.created.strftime('%d/%m')}")
        if task.model:
            extra.append(f"modelo {task.model}" + (f" · esforço {task.effort}" if task.effort else ""))
        t.append("  " + " · ".join(extra) + "\n", style=T.C_MUTED)
        if self.live:
            t.append(
                f"↻ no delegador: {self.live} (o arquivo da tarefa ainda diz {task.state}; o orquestrador o atualiza "
                "nos marcos)\n",
                style=T.C_NEW,
            )
        info = state.task_sent_info(self.project.id, task.id)
        if info:
            t.append(f"enviada {state.task_sent_label(info)}\n", style=T.C_MUTED)
        if task.trello:
            t.append(f"{task.trello}\n", style=T.C_DIM)
        t.append("\n")
        t.append_text(body_text(task.body))
        t.append("\n\n")
        t.append_text(_key("Enviar ao orquestrador", "g"))
        t.append_text(_key("Mudar estado", "s"))
        t.append_text(_key("Fechar", "Esc"))
        return t

    def action_close(self) -> None:
        self.dismiss(None)

    def action_pick(self, what: str) -> None:
        self.dismiss(what)


class AgentDetailScreen(ModalScreen["str | None"]):
    """Detalhe de um agente. Devolve "send_one" (i), "launch" (u) ou None (Esc)."""

    BINDINGS = [
        ("escape", "close", "Fechar"),
        ("i", "pick('send_one')", "Instruções"),
        ("u", "pick('launch')", "Subir"),
    ]

    def __init__(self, row: AgentRow) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form"):
            yield Static(self._content(), id="agent-detail-text")

    def _content(self) -> Text:
        r = self.row
        color = T.STATE_COLOR.get(r.state, T.C_UNK)
        t = Text()
        t.append(f"{T.STATE_DOT.get(r.state, '?')} ", style=f"bold {color}")
        t.append(f"{r.name} ", style=f"bold {T.C_STRONG}")
        t.append(f"{r.state}\n", style=color)
        t.append_text(T.badge(r.role.upper(), T.C_OWN))
        t.append(" ")
        t.append_text(T.badge(r.cli.upper(), T.C_TRELLO if r.cli == "codex" else "#6c7a89"))
        t.append(f"  modelo {r.model} · esforço {r.effort}\n\n", style=T.C_TEXT)
        for label, value in [
            ("Agente (--agent)", r.agent_def or "—"),
            ("Status", r.detail or "ainda não consultado"),
            ("Instruções", f"{r.sent_label}" + (f" via {r.via}" if r.via else "")),
            *([("Delegador", r.deleg)] if r.deleg else []),
            ("Tarefa", r.task or "—"),
        ]:
            t.append(f"{label:<17}", style=T.C_MUTED)
            t.append(f"{value}\n", style=T.C_TEXT)
        t.append("\n")
        t.append_text(_key("Enviar instruções", "i"))
        t.append_text(_key("Comandos de subida", "u"))
        t.append_text(_key("Fechar", "Esc"))
        return t

    def action_close(self) -> None:
        self.dismiss(None)

    def action_pick(self, what: str) -> None:
        self.dismiss(what)
