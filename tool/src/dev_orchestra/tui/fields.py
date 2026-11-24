"""Campos e seletores reutilizáveis dos formulários: esforço em chips, caminho com completar e escolhas em lista."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Click
from textual.message import Message
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ..core import pathhints
from ..core.catalog import Catalog
from ..core.sessions import SessionInfo
from . import theme as T

CLI_OPTIONS = [("claude", "claude"), ("codex", "codex")]


def selected(widget) -> str | None:
    """Valor escolhido num Select, ou None se estiver em branco."""
    value = widget.value
    return value if isinstance(value, str) else None


def text_of(widget) -> str | None:
    value = widget.value.strip()
    return value or None


class ModelInput(Input):
    """Campo de modelo: digite livremente ou aperte F2 para o catálogo lido da CLI."""

    BINDINGS = [Binding("f2", "catalog", "Catálogo de modelos", show=True)]

    class Catalog(Message):
        def __init__(self, input_: ModelInput) -> None:
            super().__init__()
            self.input = input_

    def action_catalog(self) -> None:
        self.post_message(self.Catalog(self))


class ChoiceChips(Static, can_focus=True):
    """Escolha entre poucas opções, em chips (←/→ trocam, clique escolhe). `value` é a opção marcada ou None."""

    BINDINGS = [Binding("left", "step(-1)", show=False), Binding("right", "step(1)", show=False),
                Binding("backspace", "clear", show=False), Binding("delete", "clear", show=False)]  # fmt: skip

    class Changed(Message):
        def __init__(self, chips: ChoiceChips, value: str | None) -> None:
            super().__init__()
            self.chips, self.value = chips, value

    def __init__(self, options: list[str], value: str | None = None, *, allow_none: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.options = list(options)
        self.value: str | None = value
        self.allow_none = allow_none
        self._ranges: list[tuple[int, int, str]] = []
        self._draw()

    def set_options(self, options: list[str]) -> None:
        self.options = list(options)
        if self.value not in self.options:
            self.value = None
        self._draw()

    def set_value(self, value: str | None) -> None:
        self.value = value if value in self.options else None
        self._draw()

    def _draw(self) -> None:
        t = Text()
        self._ranges = []
        for option in self.options:
            start = len(t.plain)
            on = option == self.value
            t.append(f" {option} ", style=f"bold #10141a on {T.C_STRONG}" if on else f"{T.C_MUTED} on #1b232d")
            self._ranges.append((start, len(t.plain), option))
            t.append(" ")
        if not self.options:
            t.append("—", style=T.C_DIM)
        self.update(t)

    def _choose(self, value: str | None) -> None:
        if value == self.value:
            return
        self.value = value
        self._draw()
        self.post_message(self.Changed(self, value))

    def action_step(self, step: int) -> None:
        if not self.options:
            return
        current = (
            self.options.index(self.value) if self.value in self.options else (-1 if step > 0 else len(self.options))
        )
        self._choose(self.options[max(0, min(len(self.options) - 1, current + step))])

    def action_clear(self) -> None:
        if self.allow_none:
            self._choose(None)

    def on_click(self, event: Click) -> None:
        for start, end, option in self._ranges:
            if start <= event.x < end:
                self._choose(None if (option == self.value and self.allow_none) else option)
                return


class DirSuggester(Suggester):
    """Completa o caminho com a primeira subpasta que casa (aceita com → ou End, como em qualquer Input)."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        found = pathhints.complete(value)
        if not found or not value:
            return None
        first = found[0]
        folder, _prefix = pathhints.split_typed(value)
        typed_home = value.startswith("~")
        full = str(folder / first.name) + "/"
        return ("~" + full[len(str(Path.home())) :]) if typed_home and full.startswith(str(Path.home())) else full


class PathInput(Input):
    """Campo de caminho: Tab completa (até onde as sugestões concordam), Ctrl+O abre o navegador de pastas."""

    BINDINGS = [Binding("tab", "complete", show=False), Binding("ctrl+o", "browse", "Escolher pasta", show=True)]

    class Browse(Message):
        def __init__(self, input_: PathInput) -> None:
            super().__init__()
            self.input = input_

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("suggester", DirSuggester())
        super().__init__(*args, **kwargs)

    def action_complete(self) -> None:
        found = pathhints.complete(self.value)
        completed = pathhints.common_completion(self.value, found)
        if completed != self.value:
            self.value = completed
            self.cursor_position = len(self.value)
        else:
            self.screen.focus_next()  # nada a completar: Tab segue para o próximo campo

    def action_browse(self) -> None:
        self.post_message(self.Browse(self))


class PickerScreen(ModalScreen["str | None"]):
    """Lista para escolher um item (modelo, pasta, sessão...). Devolve o id do item ou None."""

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, title: str, options: list[tuple[Text, str]], *, hint: str = "") -> None:
        super().__init__()
        self._title, self._options, self._hint = title, options, hint

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label(self._title, classes="title")
            if self._hint:
                yield Static(Text(self._hint, style=T.C_MUTED))
            yield OptionList(*[Option(label, id=value) for label, value in self._options], id="picker-list")

    def on_mount(self) -> None:
        options = self.query_one("#picker-list", OptionList)
        if options.option_count:
            options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


def model_picker(catalog: Catalog, current: str | None) -> PickerScreen:
    """Catálogo de modelos da CLI (F2). O que não está na lista continua podendo ser digitado no campo."""
    rows = []
    for m in catalog.models:
        label = Text()
        label.append(
            ("● " if m.slug == current else "  ") + m.slug.ljust(26),
            style=f"bold {T.C_STRONG}" if m.slug == current else T.C_TEXT,
        )
        label.append((m.description or "")[:60].ljust(62), style=T.C_MUTED)
        if m.efforts:
            label.append("esforços: " + ", ".join(m.efforts), style=T.C_DIM)
        rows.append((label, m.slug))
    note = catalog.note or (
        f"{len(catalog.models)} modelos lidos da CLI ({catalog.models[0].source})" if catalog.models else ""
    )
    return PickerScreen(f"Modelos de {catalog.cli}", rows, hint=note)


def session_picker(sessions: list[SessionInfo]) -> PickerScreen:
    rows = []
    for s in sessions:
        label = Text()
        label.append(f"{s.title[:24]:<26}", style=f"bold {T.C_STRONG}")
        label.append(f"{s.id[:8]}  {s.size_text:>7}  ", style=T.C_TEXT)
        label.append((s.last.strftime("%d/%m %H:%M") if s.last else "-").ljust(13), style=T.C_MUTED)
        label.append(f"{(s.model or '-'):<12}", style=T.C_MUTED)
        label.append(
            f"aberta (pid {s.open_pid})" if s.open_pid else "fechada", style=T.C_BUSY if s.open_pid else T.C_OK
        )
        if s.first_message:
            label.append(f"\n    {s.first_message[:90]}", style=T.C_DIM)
        rows.append((label, s.id))
    return PickerScreen("Sessões do Claude neste projeto", rows,
                        hint="Enter adota (uma sessão aberta em outro terminal precisa ser fechada antes)")  # fmt: skip


class FolderPicker(ModalScreen["str | None"]):
    """Navegador de pastas com completar: digite, ↑↓ escolhe, Tab completa, Enter confirma."""

    BINDINGS = [("escape", "cancel", "Cancelar"), ("tab", "complete", "Completar"), ("ctrl+h", "hidden", "Ocultos")]

    def __init__(self, start: str = "") -> None:
        super().__init__()
        self.start = start or "~/"
        self.hidden = False
        self.found: list[pathhints.Suggestion] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label("Escolher a pasta do projeto", classes="title")
            yield Input(value=self.start, id="fp-input")
            yield Static("", id="fp-info")
            yield OptionList(id="fp-list")
            yield Static(
                Text("↑↓ escolhe · Tab completa · Enter confirma · Ctrl+H mostra ocultos · Esc cancela", style=T.C_DIM)
            )

    def on_mount(self) -> None:
        self.query_one("#fp-input", Input).focus()
        self.query_one("#fp-input", Input).cursor_position = len(self.start)
        self._refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh()

    def _refresh(self) -> None:
        text = self.query_one("#fp-input", Input).value
        self.found = pathhints.complete(text, hidden=self.hidden)
        options = self.query_one("#fp-list", OptionList)
        options.clear_options()
        for s in self.found:
            label = Text()
            label.append(s.shown.ljust(28), style=f"bold {T.C_STRONG}" if s.markers else T.C_TEXT)
            for marker in s.markers:
                label.append_text(T.badge(marker, T.C_OK if marker == "GIT" else T.C_NEW))
                label.append(" ")
            if s.modified:
                label.append(f"  {s.modified.strftime('%d/%m %H:%M')}", style=T.C_DIM)
            options.add_option(Option(label, id=str(s.path)))
        folder = Path(text).expanduser()
        info = pathhints.describe_folder(folder)
        t = Text()
        if info.get("exists"):
            t.append("✓ existe", style=T.C_OK)
            for key, label in (("git", "Git"), ("agents_md", "AGENTS.md próprio"), ("claude_dir", ".claude/")):
                if info.get(key):
                    extra = (
                        f" ({info['branch']} · {info['modified']} modificados)"
                        if key == "git" and info.get("branch")
                        else ""
                    )
                    t.append(f"   ✓ {label}{extra}", style=T.C_OK)
            if not info.get("ai_memory_toml"):
                t.append("   ○ .ai-memory.toml", style=T.C_MUTED)
        else:
            t.append("pasta ainda não existe: continue digitando ou escolha abaixo", style=T.C_MUTED)
        self.query_one("#fp-info", Static).update(t)

    def action_complete(self) -> None:
        field = self.query_one("#fp-input", Input)
        field.value = pathhints.common_completion(field.value, self.found)
        field.cursor_position = len(field.value)

    def action_hidden(self) -> None:
        self.hidden = not self.hidden
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        field = self.query_one("#fp-input", Input)
        chosen = str(event.option.id)
        home = str(Path.home())
        field.value = ("~" + chosen[len(home) :] if chosen.startswith(home) else chosen) + "/"
        field.cursor_position = len(field.value)
        self.query_one("#fp-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().rstrip("/") or None)

    def on_key(self, event) -> None:
        if event.key in ("down", "up") and self.focused is self.query_one("#fp-input", Input):
            self.query_one("#fp-list", OptionList).focus()
            event.stop()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_click(self, event: Click) -> None:
        return


class NameScreen(ModalScreen["str | None"]):
    """Pede um texto curto (ex.: o id de um papel). Devolve o texto ou None."""

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, title: str, hint: str = "", value: str = "") -> None:
        super().__init__()
        self._title, self._hint, self._value = title, hint, value

    def compose(self) -> ComposeResult:
        with Vertical(id="pf-small"):
            yield Label(self._title, classes="title")
            if self._hint:
                yield Static(Text(self._hint, style=T.C_MUTED))
            yield Input(value=self._value, id="name-input")
            yield Static("", id="name-error", classes="error")

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            self.query_one("#name-error", Static).update(Text("Informe um nome."))
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)
