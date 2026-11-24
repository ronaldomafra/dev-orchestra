"""Formulários e confirmações (telas modais) da TUI."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from ..core import tasks
from ..core.models import Project, explain
from .fields import selected, text_of


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "no", "Não")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form"):
            yield Label(self.message, markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Sim", id="yes", variant="warning")
                yield Button("Não", id="no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_no(self) -> None:
        self.dismiss(False)


class TaskFormScreen(ModalScreen["tasks.Task | None"]):
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        roles = self.project.role_order()
        with VerticalScroll(id="form"):
            yield Label("Nova tarefa", classes="title")
            yield Label("Id (ex.: DEMO-001)")
            yield Input(id="t-id")
            yield Label("Título")
            yield Input(id="t-title")
            yield Label("Papel que executa")
            yield Select([(r, r) for r in roles], allow_blank=False, value=roles[0], id="t-role")
            yield Label("Modelo (vazio = padrão do papel)")
            yield Input(id="t-model")
            yield Label("Esforço (vazio = padrão do papel)")
            yield Input(id="t-effort")
            yield Label("Objetivo")
            yield Input(id="t-objective")
            yield Static("", id="t-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Criar", id="save", variant="primary")
                yield Button("Cancelar", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            task = tasks.create_task(
                self.project,
                text_of(self.query_one("#t-id", Input)) or "",
                text_of(self.query_one("#t-title", Input)) or "",
                selected(self.query_one("#t-role", Select)) or "",
                model=text_of(self.query_one("#t-model", Input)),
                effort=text_of(self.query_one("#t-effort", Input)),
                objective=text_of(self.query_one("#t-objective", Input)) or "",
            )
        except (tasks.TaskError, ValueError, OSError) as exc:
            self.query_one("#t-error", Static).update(Text(f"Erro: {explain(exc)}"))
            return
        self.dismiss(task)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TaskStateScreen(ModalScreen["str | None"]):
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, task_id: str, current: str) -> None:
        super().__init__()
        self.task_id = task_id
        self.current = current

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form"):
            yield Label(f"Estado da tarefa {self.task_id}", classes="title")
            yield Select([(s, s) for s in tasks.STATES], allow_blank=False, value=self.current, id="s-state")
            with Horizontal(classes="buttons"):
                yield Button("Salvar", id="save", variant="primary")
                yield Button("Cancelar", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(selected(self.query_one("#s-state", Select)))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


from .import_form import ImportFormScreen, ImportPathScreen  # noqa: E402,F401
from .project_form import ProjectFormScreen  # noqa: E402,F401  (reexportado: as telas importam daqui)
