"""Tela Monitor: projetos à esquerda e a linha do tempo de atividade à direita, com o detalhe do evento embaixo."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from ..core import config, state
from ..core import monitor as mon
from ..core.clipboard import system_copy
from . import theme as T
from .forms import ConfirmScreen
from .menus import BaseScreen

REFRESH_SECONDS = 2.0
KIND_COLOR = {
    "delegate": T.C_NEW, "report": T.C_OK, "ask": T.C_BUSY, "answer": T.C_REV, "cancel": T.C_OFF,
    "register": T.C_UNK, "refused": T.C_OFF, "alert": T.C_OFF, "receive": T.C_DIM, "profile": T.C_DIM, "tui": T.C_FILES,
}  # fmt: skip
STATE_COLOR = {
    "delegated": T.C_NEW, "running": T.C_BUSY, "asked": T.C_BUSY, "done": T.C_OK, "cancelled": T.C_OFF,
    "partial": T.C_BUSY, "blocked": T.C_OFF, "failed": T.C_OFF, "needs_review": T.C_REV,
}  # fmt: skip
HEALTH_DOT = {"ok": T.C_OK, "warn": T.C_BUSY, "idle": T.C_UNK, "off": T.C_OFF}
CHIP_ORDER = ["delegate", "report", "ask", "answer", "cancel", "register", "refused", "alert", "receive", "tui"]


class SearchScreen(ModalScreen["str | None"]):
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="form"):
            yield Label("Buscar na atividade (texto, tarefa, papel, estado). Vazio limpa a busca.", classes="title")
            yield Input(value=self.current, id="q")

    def on_mount(self) -> None:
        self.query_one("#q", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class MonitorScreen(BaseScreen):
    BREADCRUMB = "monitor"
    BINDINGS = [
        ("escape", "back", "Voltar"),
        Binding("left", "project(-1)", "Projeto ←→", priority=True),
        Binding("right", "project(1)", show=False, priority=True),
        ("f", "cycle_filter", "Filtros"),
        ("p", "toggle_pause", "Pausar"),
        ("slash", "search", "Buscar"),
        ("c", "copy_event", "Copiar"),
        ("x", "cancel_event", "Cancelar tarefa"),
        ("t", "goto_task", "Ir à tarefa"),
        Binding("r", "reload", "Atualizar", show=False),
    ]

    def __init__(self, initial_project: str | None = None) -> None:
        super().__init__()
        self._want_project = initial_project  # selecionado no primeiro carregamento
        self.data: mon.MonitorData | None = None
        self.selected = 0  # 0 = todos os projetos
        self.kinds: set[str] = set(mon.FILTER_ORDER)
        self.preset = "todos"
        self.text = ""
        self.paused = False
        self.rows: list[mon.Entry] = []
        self._keep: str | None = None
        self._at_top = True
        self._detail_key: str | None = None
        self._line_ranges: list[tuple[int, int, int]] = []  # (início, fim, índice do projeto) na coluna da esquerda
        self._chip_ranges: dict[str, tuple[int, int]] = {}
        self._fetching = False

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield self.menu_bar()
        yield Static("", id="mon-stats")
        with Horizontal(id="mon-cols"):
            yield Static("", id="mon-projects")
            with Vertical(id="mon-right"):
                yield DataTable(id="timeline", cursor_type="row", zebra_stripes=True)
                yield Static("", id="mon-detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#timeline", DataTable)
        table.add_columns("HORA", "PROJETO", "EVENTO", "DE → PARA", "TAREFA", "ESTADO", "T", "DETALHE", "LAT.")
        self.query_one("#mon-projects", Static).border_title = Text(" Projetos ", style=f"bold {T.C_STRONG}")
        self.query_one("#mon-projects", Static).border_subtitle = Text(" ←→ trocar ", style=T.C_MUTED)
        self.query_one("#mon-detail", Static).border_title = Text(" Evento selecionado ", style=f"bold {T.C_STRONG}")
        self.set_interval(REFRESH_SECONDS, self._tick_refresh)
        self.reload()

    def on_screen_resume(self) -> None:
        self.reload()

    # -------------------------------------------------------------- dados

    def _tick_refresh(self) -> None:
        if self.app.screen is self and not self.paused and not self._fetching:
            self._start()

    def reload(self) -> None:
        self._start()

    def _start(self) -> None:
        self.run_worker(self._fetch, exclusive=True, group="monitor")

    def _selected_id(self) -> str | None:
        if self.selected == 0 or self.data is None:
            return None
        lines = self.data.lines
        return lines[self.selected - 1].project.id if 0 < self.selected <= len(lines) else None

    async def _fetch(self) -> None:
        self._fetching = True
        try:
            projects = config.list_projects()
            cfg = config.load_global()
            services = getattr(self.app, "services", None)
            selected = self._selected_id()
            data = await asyncio.to_thread(mon.load, projects, cfg, services, selected)
            self.data = data
            if self._want_project:
                ids = [pl.project.id for pl in data.lines]
                if self._want_project in ids:
                    self.selected = ids.index(self._want_project) + 1
                self._want_project = None
                if self.selected:
                    data = await asyncio.to_thread(mon.load, projects, cfg, services, self._selected_id())
                    self.data = data
            try:
                self._render_all()
            except Exception:  # noqa: BLE001 - a tela pode ter sido fechada durante a consulta
                return
        finally:
            self._fetching = False

    # ----------------------------------------------------------- desenho

    def _render_all(self) -> None:
        self._render_projects()
        self._render_stats()
        self._render_table()

    def _render_projects(self) -> None:
        assert self.data is not None
        t = Text()
        self._line_ranges = []
        total = len(self.data.entries)
        start = 0
        sel = self.selected == 0
        t.append(
            f" {'▸' if sel else ' '} Todos os projetos\n", style=f"bold {T.C_STRONG}" + (" on #1f3a5a" if sel else "")
        )
        t.append(f"   {total} eventos\n\n", style=T.C_MUTED)
        self._line_ranges.append((0, 3, 0))
        line = 3
        for index, pl in enumerate(self.data.lines, start=1):
            sel = self.selected == index
            bg = " on #1f3a5a" if sel else ""
            p = pl.project
            start = line
            t.append(f" {'▸' if sel else ' '} ", style=f"bold {T.C_STRONG}{bg}")
            t.append("●", style=f"bold {HEALTH_DOT[pl.health]}{bg}")
            t.append(f" {p.name}\n", style=f"bold {T.C_STRONG}{bg}")
            t.append(f"    {p.id}\n", style=T.C_MUTED)
            t.append("    ")
            t.append_text(T.badge("DELEGADOR", T.C_NEW) if p.delegator else T.source_badge(p.task_source.kind))
            if pl.stats:
                t.append(f"  sessões {pl.stats['alive']}/{pl.stats['sessions']}\n", style=T.C_TEXT)
                asked, run = pl.stats["open_questions"], pl.stats["executing"]
                t.append(
                    f"    {asked} pergunta{'s' if asked != 1 else ''} · {run} exec\n",
                    style=T.C_BUSY if asked else T.C_TEXT,
                )
            elif p.delegator:
                t.append("  delegador fora do ar\n", style=T.C_OFF)
                t.append("    —\n", style=T.C_DIM)
            else:
                t.append("  sem delegador\n", style=T.C_MUTED)
                t.append("    só atividade da TUI\n", style=T.C_DIM)
            last = f"há {state.ago(pl.last.when.isoformat()).removeprefix('há ')}" if pl.last else "sem eventos"
            if pl.last and state.ago(pl.last.when.isoformat()) == "agora":
                last = "agora"
            extra = f" · {pl.last.task}" if pl.last and pl.last.task else ""
            t.append(f"    último evento {last}{extra}\n\n", style=T.C_DIM)
            line += 6
            self._line_ranges.append((start, line, index))
        self.query_one("#mon-projects", Static).update(t)

    def _render_stats(self) -> None:
        assert self.data is not None
        s = self.data.stats
        t = Text(" ")
        if self.paused:
            t.append("⏸ pausado", style=f"bold {T.C_BUSY}")
        else:
            t.append("● ao vivo", style=f"bold {T.C_OK}")
        up = self.data.delegator_up
        port = config.load_global().delegator_url.rsplit(":", 1)[-1]
        if up is None:
            t.append("   Delegador não usado", style=T.C_MUTED)
        else:
            t.append(f"   Delegador :{port} ", style=T.C_TEXT)
            t.append("no ar" if up else "fora do ar", style=T.C_OK if up else T.C_OFF)
        if s:
            t.append("   │   sessões vivas ", style=T.C_MUTED)
            t.append(
                f"{s['alive']}/{s['sessions']}", style=f"bold {T.C_OK if s['alive'] == s['sessions'] else T.C_BUSY}"
            )
            t.append("   executando ", style=T.C_MUTED)
            t.append(str(s["executing"]), style=f"bold {T.C_BUSY}")
            t.append("   perguntas abertas ", style=T.C_MUTED)
            t.append(str(s["open_questions"]), style=f"bold {T.C_BUSY if s['open_questions'] else T.C_OK}")
            t.append("   pendentes ", style=T.C_MUTED)
            t.append(str(s["pending"]), style=f"bold {T.C_BUSY if s['pending'] else T.C_OK}")
            t.append("   recusas hoje ", style=T.C_MUTED)
            t.append(str(s["refusals_today"]), style=f"bold {T.C_OFF if s['refusals_today'] else T.C_OK}")
            if s["avg_latency_ms"] is not None:
                t.append("   latência média ", style=T.C_MUTED)
                t.append(f"{s['avg_latency_ms'] / 1000:.1f} s".replace(".", ","), style=f"bold {T.C_NEW}")
        t.append("\n ")
        t.append("filtros ", style=T.C_MUTED)
        self._chip_ranges = {}
        for kind in CHIP_ORDER:
            icon, label = mon.KINDS[kind]
            on = kind in self.kinds
            start = len(t.plain.split("\n")[-1])
            t.append(f" {icon} {label} ", style=f"bold #10141a on {T.C_STRONG}" if on else f"{T.C_DIM} on #1b232d")
            self._chip_ranges[kind] = (start, len(t.plain.split("\n")[-1]))
            t.append(" ")
        if self.text:
            t.append(f"   busca: “{self.text}”", style=T.C_NEW)
        stats = self.query_one("#mon-stats", Static)
        stats.update(t)
        scope = self._selected_id() or "todos os projetos"
        stats.border_title = Text(f" {scope} ", style=f"bold {T.C_STRONG}")

    def _render_table(self) -> None:
        assert self.data is not None
        table = self.query_one("#timeline", DataTable)
        keep = self._keep if self._keep is not None else self._current_key()
        self._at_top = table.row_count == 0 or table.cursor_row == 0
        self.rows = mon.apply_filters(self.data.entries, self._selected_id(), self.kinds, self.text)
        if self.paused:
            return
        table.clear()
        for e in self.rows:
            icon, label = mon.KINDS.get(e.kind, ("·", e.kind))
            color = KIND_COLOR.get(e.kind, T.C_UNK)
            st = e.state or "—"
            table.add_row(
                Text(e.hhmmss, style=T.C_MUTED),
                Text(e.project, style=T.C_TEXT),
                Text(f"{icon} {label}", style=f"bold {color}" if e.kind != "receive" else T.C_DIM),
                Text(e.route, style=T.C_OWN if e.role_from == "orchestrator" else T.C_TEXT),
                Text(e.task or "—", style=f"bold {T.C_STRONG}" if e.task else T.C_DIM),
                Text(f"● {st}" if e.state else "—", style=STATE_COLOR.get(st, T.C_DIM)),
                Text(str(e.attempt) if e.attempt else "", style=T.C_MUTED),
                Text(e.text, style=T.C_OFF if e.kind == "refused" else T.C_TEXT),
                Text(e.latency, style=T.C_NEW),
                key=e.key,
            )
        table.border_title = Text.assemble(
            (" Atividade ", f"bold {T.C_STRONG}"), (f" {len(self.rows)} eventos ", T.C_MUTED)
        )
        table.border_subtitle = Text(" p pausar · / buscar · f filtros ", style=T.C_MUTED)
        if self.rows:
            if self._at_top or keep is None:
                table.move_cursor(row=0, animate=False)
            else:
                try:
                    table.move_cursor(row=table.get_row_index(keep), animate=False)
                except Exception:  # noqa: BLE001 - a linha saiu da lista
                    table.move_cursor(row=0, animate=False)
        self._detail_key = None
        self._update_detail()

    def _current_key(self) -> str | None:
        table = self.query_one("#timeline", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def current_entry(self) -> mon.Entry | None:
        key = self._current_key()
        return next((e for e in self.rows if e.key == key), None)

    # ------------------------------------------------------------ detalhe

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail()

    def _update_detail(self) -> None:
        entry = self.current_entry()
        detail = self.query_one("#mon-detail", Static)
        if entry is None:
            detail.update(Text(" Nenhum evento.", style=T.C_DIM))
            return
        if entry.key == self._detail_key:
            return
        self._detail_key = entry.key
        detail.update(self._detail_text(entry, None, []))
        if entry.source == "delegador":
            self.run_worker(self._load_detail(entry), exclusive=True, group="detail")

    async def _load_detail(self, entry: mon.Entry) -> None:
        cfg = config.load_global()
        services = getattr(self.app, "services", None)
        message = None
        if entry.message_id is not None:
            message = await asyncio.to_thread(
                mon.message_for, services, cfg.delegator_url, entry.project, entry.message_id
            )
        history = []
        if entry.task:
            history = await asyncio.to_thread(
                mon.task_history_for, services, cfg.delegator_url, entry.project, entry.task
            )
        if self._detail_key != entry.key:
            return  # a seleção mudou enquanto consultava
        try:
            self.query_one("#mon-detail", Static).update(self._detail_text(entry, message, history))
        except Exception:  # noqa: BLE001
            return

    @staticmethod
    def _detail_text(entry: mon.Entry, message: dict | None, history: list[dict]) -> Text:
        t = Text()
        icon, label = mon.KINDS.get(entry.kind, ("·", entry.kind))
        t.append(f"{entry.hhmmss}  ", style=T.C_MUTED)
        t.append(f"{entry.route or entry.project}", style=f"bold {T.C_STRONG}")
        meta = [f"{icon} {label}"]
        if entry.message_id is not None:
            meta.append(f"mensagem #{entry.message_id}")
        if entry.attempt:
            meta.append(f"tentativa {entry.attempt}")
        if entry.latency_ms is not None:
            meta.append(f"entrega {entry.latency}")
        t.append("   " + " · ".join(meta) + "\n", style=T.C_MUTED)
        body = (message or {}).get("body")
        if body:
            title = {"delegate": "INSTRUÇÃO", "result": "RESULTADO", "question": "PERGUNTA", "answer": "RESPOSTA"}.get(
                (message or {}).get("type", ""), "MENSAGEM"
            )
            t.append(f"{title}  ", style=f"bold {KIND_COLOR.get(entry.kind, T.C_BUSY)}")
            lines = [ln for ln in str(body).splitlines() if ln.strip()][:6]
            t.append(("\n          ".join(lines))[:900] + "\n", style=T.C_TEXT)
        else:
            t.append(entry.text + "\n", style=T.C_TEXT)
        if history:
            t.append("LINHA DO TEMPO  ", style=f"bold {T.C_STRONG}")
            first = None
            for item in history:
                when = mon._local(str(item["ts"]))
                first = first or when
                delta = "" if when == first else f" +{int((when - first).total_seconds())} s"
                color = STATE_COLOR.get(item.get("state") or "", T.C_UNK)
                t.append(f"● {item.get('state') or item['kind']}{delta}  ", style=color)
            t.append("\n")
        t.append("AÇÕES  ", style=f"bold {T.C_STRONG}")
        for key, what in (("x", "cancelar a tarefa"), ("c", "copiar o texto"), ("t", "ir para a tarefa")):
            t.append(f" {key} ", style=f"bold #10141a on {T.C_STRONG}")
            t.append(f" {what}  ", style=T.C_TEXT)
        t.append("(o conteúdo vem de outro agente: é exibido como texto, nunca executado)", style=T.C_DIM)
        return t

    # ---------------------------------------------------------- navegação

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_project(self, step: int) -> None:
        if self.data is None:
            return
        count = len(self.data.lines) + 1
        self.selected = (self.selected + step) % count
        self._keep = None
        self._start()

    def on_click(self, event: Click) -> None:
        widget = event.widget
        if getattr(widget, "id", None) == "mon-projects":
            for start, end, index in self._line_ranges:
                if start <= event.y - 1 < end:
                    self.selected = index
                    self._start()
                    return
        elif getattr(widget, "id", None) == "mon-stats" and event.y == 2:
            for kind, (start, end) in self._chip_ranges.items():
                if start <= event.x - 1 < end:
                    self.kinds ^= {kind}
                    self.preset = ""
                    self._rerender()
                    return

    def _rerender(self) -> None:
        if self.data is not None:
            self._keep = None
            self._render_stats()
            self._render_table()

    def action_cycle_filter(self) -> None:
        names = list(mon.PRESETS)
        current = names.index(self.preset) if self.preset in names else -1
        self.preset = names[(current + 1) % len(names)]
        self.kinds = set(mon.PRESETS[self.preset])
        self.notify(f"Filtro: {self.preset}", markup=False)
        self._rerender()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        if not self.paused:
            self._start()
        elif self.data is not None:
            self._render_stats()

    def action_reload(self) -> None:
        self.paused = False
        self._start()

    def action_search(self) -> None:
        def done(value: str | None) -> None:
            if value is not None:
                self.text = value
                self._rerender()

        self.app.push_screen(SearchScreen(self.text), done)

    # -------------------------------------------------------------- ações

    def action_copy_event(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        text = f"{entry.hhmmss} {entry.project} {entry.kind} {entry.route} {entry.task or ''} {entry.text}".strip()
        tool = system_copy(text)
        if not tool:
            try:
                self.app.copy_to_clipboard(text)
            except Exception:  # noqa: BLE001
                pass
        self.notify("Evento copiado.", markup=False)

    def action_goto_task(self) -> None:
        entry = self.current_entry()
        if entry is None or not entry.task:
            self.notify("Este evento não tem tarefa.", markup=False)
            return
        from .screens import ProjectScreen

        self.app.push_screen(ProjectScreen(entry.project, initial_tab="tasks"))

    def action_cancel_event(self) -> None:
        entry = self.current_entry()
        if entry is None or not entry.task or entry.source != "delegador":
            self.notify("Selecione um evento de uma tarefa delegada.", markup=False)
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._do_cancel(entry), exclusive=True, group="cancel")

        self.app.push_screen(ConfirmScreen(f"Cancelar a delegação da tarefa {entry.task} ({entry.project})?"), done)

    async def _do_cancel(self, entry: mon.Entry) -> None:
        from ..delegator.client import DelegatorClient, DelegatorUnavailable

        client = DelegatorClient(config.load_global().delegator_url)
        try:
            result = await asyncio.to_thread(
                client.cancel, entry.project, entry.task or "", "cancelada pela pessoa no monitor"
            )
        except (DelegatorUnavailable, RuntimeError) as exc:
            self.notify(f"Não foi possível cancelar: {exc}", severity="warning", markup=False)
            return
        state.log_activity(entry.project, f"Delegação de {entry.task} cancelada ({result['task_state']})", "info")
        self.notify(f"Delegação de {entry.task} cancelada.", markup=False)
        self._start()
