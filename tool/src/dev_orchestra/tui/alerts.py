"""Central de alertas (lista, detalhe e regras) e a janela de configuração."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, Static

from ..core import alerts as al
from ..core import config
from ..core.models import AlertSettings, explain
from . import theme as T
from .forms import ConfirmScreen
from .menus import BaseScreen

SEV_COLOR = {al.CRIT: T.C_OFF, al.WARN: T.C_BUSY, al.INFO: T.C_NEW}
STATE_LABEL = {"novo": "NOVO", "visto": "VISTO", "reconhecido": "OK", "silenciado": "SILENC."}
SNOOZE_MINUTES = 30
STATE_CHIPS = [("novo", "Novos"), ("visto", "Vistos"), ("reconhecido", "Reconhecidos"), ("silenciado", "Silenciados")]
SEV_CHIPS = [(al.CRIT, "▲ críticos"), (al.WARN, "● atenção"), (al.INFO, "○ info")]


def _center(app):
    return getattr(app, "alerts", None)


class AlertSettingsScreen(ModalScreen["AlertSettings | None"]):
    BINDINGS = [("escape", "cancel", "Cancelar"), ("ctrl+s", "save", "Salvar"), ("ctrl+t", "test", "Testar")]

    FIELDS = [
        ("volume", "Volume do som (0 a 100)"),
        ("quiet_start", "Silêncio programado: início (HH:MM, vazio desliga)"),
        ("quiet_end", "Silêncio programado: fim (HH:MM)"),
        ("repeat_minutes", "Repetir alerta crítico sem resposta a cada (min)"),
        ("repeat_max", "Repetir no máximo (vezes)"),
        ("question_minutes", "Pergunta de worker sem resposta após (min)"),
        ("stalled_minutes", "Tarefa parada (sem eventos) após (min)"),
        ("not_received_minutes", "Delegada e não recebida após (min)"),
        ("done_minutes", "Concluída aguardando conferência após (min)"),
    ]

    def __init__(self, settings: AlertSettings) -> None:
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        s = self.settings
        with VerticalScroll(id="form"):
            yield Label("Configurar alertas   (Ctrl+S salva, Ctrl+T testa, Esc cancela)", classes="title")
            yield Checkbox("Som", value=s.sound, id="a-sound")
            yield Checkbox("Notificação do sistema (Linux: notify-send)", value=s.os_notify, id="a-os")
            for name, label in self.FIELDS:
                yield Label(label)
                yield Input(value="" if getattr(s, name) is None else str(getattr(s, name)), id=f"a-{name}")
            yield Static(
                Text(
                    "Críticos: som de alarme + notificação crítica. Atenção: som de mensagem + notificação normal. "
                    "Info: só aparece na TUI. O som some no horário de silêncio; a notificação continua.\n"
                    "Em segundo plano (TUI fechada): dev-orchestra alerts install-service.",
                    style=T.C_MUTED,
                ),
                id="a-help",
            )
            yield Static("", id="a-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Salvar", id="save", variant="primary")
                yield Button("Testar som e notificação", id="test")
                yield Button("Cancelar", id="cancel")

    def _build(self) -> AlertSettings:
        values: dict = {
            "sound": self.query_one("#a-sound", Checkbox).value,
            "os_notify": self.query_one("#a-os", Checkbox).value,
            "muted_kinds": self.settings.muted_kinds,
        }
        for name, _label in self.FIELDS:
            raw = self.query_one(f"#a-{name}", Input).value.strip()
            values[name] = (raw or None) if name.startswith("quiet") else int(raw)
        return AlertSettings(**values)

    def _error(self, message: str) -> None:
        self.query_one("#a-error", Static).update(Text(message, style=T.C_OFF))

    def action_save(self) -> None:
        try:
            self.dismiss(self._build())
        except (ValueError, TypeError) as exc:
            self._error(f"Erro: {explain(exc)}")

    def action_test(self) -> None:
        try:
            settings = self._build()
        except (ValueError, TypeError) as exc:
            self._error(f"Erro: {explain(exc)}")
            return
        notifier = getattr(self.app, "notifier", None) or al.Notifier(settings)
        notifier.settings = settings
        now = datetime.now().astimezone()
        for kind in ("permission", "stalled"):
            sample = al.Alert(f"teste|{kind}", kind, "dev-orchestra", "teste", now, "Isto é um teste dos alertas.")
            notifier.notify(sample)
        self._error("")
        self.app.notify("Teste enviado: veja a notificação do sistema e ouça o som.", markup=False)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {"save": self.action_save, "test": self.action_test, "cancel": self.action_cancel}[
            event.button.id or "cancel"
        ]()


class AlertsScreen(BaseScreen):
    BREADCRUMB = "alertas"
    BINDINGS = [
        ("escape", "back", "Voltar"),
        ("a", "acknowledge", "Reconhecer"),
        ("s", "snooze", "Silenciar 30 min"),
        ("m", "mute_kind", "Silenciar tipo"),
        ("o", "open_in_monitor", "Monitor"),
        ("x", "cancel_task", "Cancelar tarefa"),
        ("c", "configure", "Configurar"),
        Binding("enter", "goto_agent", "Ir ao agente", priority=False),
        Binding("r", "reload", "Atualizar", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.states: set[str] = {"novo", "visto"}
        self.severities: set[str] = {al.CRIT, al.WARN, al.INFO}
        self.rows: list[al.Alert] = []
        self._chip_ranges: dict[str, tuple[int, int]] = {}

    def compose(self) -> ComposeResult:
        yield self.menu_bar()
        yield Static("", id="al-strip")
        yield DataTable(id="al-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="al-detail")
        yield Static("", id="al-rules")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#al-table", DataTable)
        table.add_columns("", "HÁ", "PROJETO", "AGENTE", "TIPO", "TAREFA", "DETALHE", "ESTADO")
        self.query_one("#al-rules", Static).border_title = Text(" Regras ", style=f"bold {T.C_STRONG}")
        self.query_one("#al-rules", Static).border_subtitle = Text(" c configurar ", style=T.C_MUTED)
        self.query_one("#al-detail", Static).border_title = Text(" Alerta selecionado ", style=f"bold {T.C_STRONG}")
        self.set_interval(2.0, self._tick)
        self.render_all()

    def on_screen_resume(self) -> None:
        self.render_all()

    def _tick(self) -> None:
        if self.app.screen is self:
            center = _center(self.app)
            if center is not None:
                center.tick()

    def on_alerts_changed(self) -> None:
        self.render_all()

    # ----------------------------------------------------------- desenho

    def _items(self) -> list[al.Alert]:
        center = _center(self.app)
        return list(center.items) if center else []

    def render_all(self) -> None:
        items = self._items()
        keep = self._current_key()
        self.rows = [a for a in items if a.state in self.states and a.severity in self.severities]
        self._render_strip(items)
        table = self.query_one("#al-table", DataTable)
        table.clear()
        now = datetime.now().astimezone()
        for a in self.rows:
            color = SEV_COLOR[a.severity]
            table.add_row(
                Text(al.SEVERITY_ICON[a.severity], style=f"bold {color}"),
                Text(a.age_text(now), style=T.C_MUTED),
                Text(a.project, style=T.C_TEXT),
                Text(a.agent, style=T.C_OWN if "orchestr" in a.agent else T.C_TEXT),
                Text(a.label, style=f"bold {color}"),
                Text(a.task or "—", style=f"bold {T.C_STRONG}" if a.task else T.C_DIM),
                Text(a.message, style=T.C_TEXT),
                Text(
                    STATE_LABEL[a.state],
                    style=f"bold {T.C_STRONG}"
                    if a.state == "novo"
                    else (T.C_DIM if a.state == "silenciado" else T.C_MUTED),
                ),
                key=a.key,
            )
        table.border_title = Text.assemble((" Alertas ", f"bold {T.C_STRONG}"), (f" {len(self.rows)} ", T.C_MUTED))
        table.border_subtitle = Text(" ↑↓ escolher · Enter ir ao agente ", style=T.C_MUTED)
        if self.rows:
            index = next((i for i, a in enumerate(self.rows) if a.key == keep), 0)
            table.move_cursor(row=index, animate=False)
        self._render_detail()
        self._render_rules()

    def _render_strip(self, items: list[al.Alert]) -> None:
        cfg = config.load_global().alerts
        active = [a for a in items if a.state in ("novo", "visto")]
        t = Text(" ")
        for sev, label in ((al.CRIT, "críticos"), (al.WARN, "atenção"), (al.INFO, "info")):
            n = sum(a.severity == sev for a in active)
            t.append(
                f" {al.SEVERITY_ICON[sev]} {n} {label} ",
                style=f"bold #10141a on {SEV_COLOR[sev]}" if n else f"{T.C_DIM} on #1b232d",
            )
            t.append(" ")
        t.append("      Som ", style=T.C_MUTED)
        t.append("● ligado" if cfg.sound else "○ desligado", style=f"bold {T.C_OK if cfg.sound else T.C_DIM}")
        t.append("   Notificação do SO ", style=T.C_MUTED)
        t.append(
            "● ligada (notify-send)" if cfg.os_notify else "○ desligada",
            style=f"bold {T.C_OK if cfg.os_notify else T.C_DIM}",
        )
        quiet = f"{cfg.quiet_start}–{cfg.quiet_end}" if cfg.quiet_start and cfg.quiet_end else "desligado"
        t.append("   Silêncio ", style=T.C_MUTED)
        t.append(quiet, style=T.C_TEXT)
        t.append("\n ")
        t.append("mostrar ", style=T.C_MUTED)
        self._chip_ranges = {}
        for key, label, group in [(k, lbl, self.states) for k, lbl in STATE_CHIPS] + [
            (k, lbl, self.severities) for k, lbl in SEV_CHIPS
        ]:
            start = len(t.plain.split("\n")[-1])
            on = key in group
            t.append(f" {label} ", style=f"bold #10141a on {T.C_STRONG}" if on else f"{T.C_DIM} on #1b232d")
            self._chip_ranges[key] = (start, len(t.plain.split("\n")[-1]))
            t.append(" ")
        self.query_one("#al-strip", Static).update(t)

    def _current_key(self) -> str | None:
        table = self.query_one("#al-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def current(self) -> al.Alert | None:
        key = self._current_key()
        return next((a for a in self.rows if a.key == key), None)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        a = self.current()
        if a is not None and a.state == "novo":
            center = _center(self.app)
            if center is not None:
                center.store.mark_seen([a])  # olhar para o alerta é "vê-lo"
        self._render_detail()

    def _render_detail(self) -> None:
        a = self.current()
        detail = self.query_one("#al-detail", Static)
        if a is None:
            detail.update(Text(" Nenhum alerta com os filtros atuais.", style=T.C_DIM))
            detail.border_title = Text(" Alerta selecionado ", style=f"bold {T.C_STRONG}")
            return
        color = SEV_COLOR[a.severity]
        detail.border_title = Text.assemble(
            (" Alerta selecionado ", f"bold {T.C_STRONG}"),
            (f" {al.SEVERITY_ICON[a.severity]} {al.SEVERITY_LABEL[a.severity].lower()} · {a.label} ", color),
        )
        t = Text()
        t.append(a.agent, style=f"bold {T.C_STRONG}")
        t.append(f"  ·  {a.project}", style=T.C_MUTED)
        if a.task:
            t.append(f"  ·  tarefa {a.task}", style=T.C_MUTED)
        t.append(f"  ·  há {a.age_text()} ({a.since.strftime('%H:%M:%S')})\n", style=T.C_MUTED)
        t.append("O QUE ACONTECEU  ", style=f"bold {color}")
        t.append(f"{a.message}\n", style=T.C_TEXT)
        if a.detail and a.kind in ("permission", "question"):
            t.append("DETALHE          ", style=f"bold {T.C_STRONG}")
            t.append(" ".join(a.detail.split())[:240] + "\n", style=T.C_DIM)
        t.append("O QUE FAZER      ", style=f"bold {T.C_STRONG}")
        t.append(f"{a.advice}\n", style=T.C_TEXT)
        t.append("AÇÕES  ", style=f"bold {T.C_STRONG}")
        for key, what in (("Enter", "ir ao agente"), ("a", "reconhecer"), ("s", f"silenciar {SNOOZE_MINUTES} min"),
                          ("m", "silenciar este tipo"), ("o", "abrir no monitor"), ("x", "cancelar a tarefa")):  # fmt: skip
            t.append(f" {key} ", style=f"bold #10141a on {T.C_STRONG}")
            t.append(f" {what}  ", style=T.C_TEXT)
        t.append("\n(o texto vem do agente: é exibido como texto, nunca executado)", style=T.C_DIM)
        detail.update(t)

    def _render_rules(self) -> None:
        cfg = config.load_global().alerts
        rows = [
            (al.CRIT, "aguardando permissão / pergunta à pessoa", "som de alarme + SO crítico", "imediato"),
            (
                al.CRIT,
                "turno interrompido (limite, erro de API, autenticação)",
                "som de alarme + SO crítico",
                "imediato",
            ),
            (
                al.WARN,
                "pergunta de worker sem resposta",
                "som de mensagem + SO normal",
                f"> {cfg.question_minutes} min",
            ),
            (
                al.WARN,
                "tarefa parada (sem eventos) · delegada e não recebida",
                "som de mensagem + SO normal",
                f"> {cfg.stalled_minutes} / {cfg.not_received_minutes} min",
            ),
            (
                al.WARN,
                "sessão fechada com tarefa · recusa de permissão · delegador fora",
                "som de mensagem + SO normal",
                "imediato",
            ),
            (al.INFO, "concluída aguardando conferência", "só a TUI", f"> {cfg.done_minutes} min"),
        ]
        t = Text()
        for sev, what, how, limit in rows:
            t.append(f" {al.SEVERITY_ICON[sev]} ", style=f"bold {SEV_COLOR[sev]}")
            t.append(f"{what:<66}", style=T.C_TEXT)
            t.append(f"{how:<30}", style=T.C_MUTED)
            t.append(f"{limit}\n", style=T.C_DIM)
        if cfg.muted_kinds:
            t.append(
                f" silenciados: {', '.join(al.KINDS[k][1] for k in cfg.muted_kinds if k in al.KINDS)}", style=T.C_DIM
            )
        t.rstrip()
        self.query_one("#al-rules", Static).update(t)

    # ---------------------------------------------------------- navegação

    def on_click(self, event: Click) -> None:
        if getattr(event.widget, "id", None) == "al-strip" and event.y == 2:
            for key, (start, end) in self._chip_ranges.items():
                if start <= event.x - 1 < end:
                    group = self.states if key in dict(STATE_CHIPS) else self.severities
                    group ^= {key}
                    self.render_all()
                    return

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_goto_agent()

    def action_reload(self) -> None:
        center = _center(self.app)
        if center is not None:
            center.tick()
        self.render_all()

    def action_goto_agent(self) -> None:
        a = self.current()
        if a is None:
            return
        from .screens import ProjectScreen

        self.app.push_screen(ProjectScreen(a.project, initial_tab="agents"))

    def action_open_in_monitor(self) -> None:
        a = self.current()
        from .monitor import MonitorScreen

        self.app.push_screen(MonitorScreen(initial_project=a.project if a else None))

    # ------------------------------------------------------------- ações

    def action_acknowledge(self) -> None:
        a = self.current()
        center = _center(self.app)
        if a is None or center is None:
            return
        center.store.acknowledge(a)
        self.notify("Alerta reconhecido (volta se o problema reaparecer).", markup=False)
        center.tick()
        self._refresh_state(a, "reconhecido")

    def action_snooze(self) -> None:
        a = self.current()
        center = _center(self.app)
        if a is None or center is None:
            return
        center.store.snooze(a, SNOOZE_MINUTES)
        self.notify(f"Alerta silenciado por {SNOOZE_MINUTES} min.", markup=False)
        center.tick()
        self._refresh_state(a, "silenciado")

    def _refresh_state(self, alert: al.Alert, state: str) -> None:
        """Mostra o novo estado já, sem esperar o próximo ciclo."""
        center = _center(self.app)
        if center is None:
            return
        center.items = [al.replace(i, state=state) if i.key == alert.key else i for i in center.items]
        self.render_all()

    def action_mute_kind(self) -> None:
        a = self.current()
        if a is None:
            return
        cfg = config.load_global()
        muted = set(cfg.alerts.muted_kinds)
        muted ^= {a.kind}
        cfg.alerts = cfg.alerts.model_copy(update={"muted_kinds": sorted(muted)})
        config.save_global(cfg)
        self.notify(
            f"Tipo “{a.label}” {'silenciado' if a.kind in muted else 'reativado'} (Configurar ou aqui de novo desfaz).",
            markup=False,
        )
        center = _center(self.app)
        if center is not None:
            center.tick()
        self.render_all()

    def action_configure(self) -> None:
        cfg = config.load_global()

        def done(new: AlertSettings | None) -> None:
            if new is not None:
                cfg.alerts = new
                config.save_global(cfg)
                self.notify("Configuração dos alertas salva.", markup=False)
                self.render_all()

        self.app.push_screen(AlertSettingsScreen(cfg.alerts), done)

    def action_cancel_task(self) -> None:
        a = self.current()
        if a is None or not a.task:
            self.notify("Este alerta não tem tarefa.", markup=False)
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._do_cancel(a), exclusive=True, group="cancel")

        self.app.push_screen(ConfirmScreen(f"Cancelar a delegação da tarefa {a.task} ({a.project})?"), done)

    async def _do_cancel(self, a: al.Alert) -> None:
        from ..core import state
        from ..delegator.client import DelegatorClient, DelegatorUnavailable

        client = DelegatorClient(config.load_global().delegator_url)
        try:
            result = await asyncio.to_thread(
                client.cancel, a.project, a.task or "", "cancelada pela pessoa na central de alertas"
            )
        except (DelegatorUnavailable, RuntimeError) as exc:
            self.notify(f"Não foi possível cancelar: {exc}", severity="warning", markup=False)
            return
        state.log_activity(a.project, f"Delegação de {a.task} cancelada ({result['task_state']})", "info")
        self.notify(f"Delegação de {a.task} cancelada.", markup=False)
