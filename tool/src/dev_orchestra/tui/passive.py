"""TUI dos agentes passivos: cartão da tela inicial, tela do agente, formulário, teste de consulta e remoção."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, Static, TextArea

from ..core import catalog, config, generator, mcp_http, passive, passive_runner, sessions
from ..core.clipboard import system_copy
from ..core.models import Project, Role
from . import theme as T
from .fields import ChoiceChips, FolderPicker, ModelInput, PathInput, model_picker, session_picker, text_of
from .menus import BaseScreen
from .widgets import Tile

C_PASSIVE = "#5fd7af"
CLI_COLOR = {"claude": T.C_OWN, "codex": T.C_TRELLO, "ollama": T.C_FILES, "opencode": T.C_NEW, "comando": "#6c7a89"}
STATE_LABEL = {
    "pending": ("◷ na fila", T.C_NEW),
    "running": ("◐ executando", T.C_BUSY),
    "done": ("● respondida", T.C_OK),
    "failed": ("✗ falhou", T.C_OFF),
    "cancelled": ("○ cancelada", T.C_UNK),
}
REFRESH_SECONDS = 3.0


# ------------------------------------------------------------------ formatação


def num(n: int | float | None) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mi".replace(".", ",")
    if n >= 10_000:
        return f"{n / 1000:.0f} mil"
    if n >= 1000:
        return f"{n / 1000:.1f} mil".replace(".", ",")
    return str(n)


def money(v: float | None) -> str:
    value = float(v or 0)
    return f"US$ {value:.3f}".replace(".", ",") if 0 < value < 0.1 else f"US$ {value:.2f}".replace(".", ",")


def secs(ms: int | None) -> str:
    if ms is None:
        return "—"
    s = ms / 1000
    return (
        f"{s:.0f} s" if s >= 10 else f"{s:.1f} s".replace(".", ",") if s < 60 else f"{s / 60:.1f} min".replace(".", ",")
    )


def tokens_of(usage: dict | None) -> int:
    u = usage or {}
    return (
        int(u.get("input_tokens") or 0)
        + int(u.get("cache_read_tokens") or 0)
        + int(u.get("cache_write_tokens") or 0)
        + int(u.get("output_tokens") or 0)
    )


def local_time(iso: str | None, fmt: str = "%H:%M:%S") -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime(fmt)
    except ValueError:
        return "?"


def ago(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        delta = datetime.now().astimezone() - datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return "?"
    s = int(delta.total_seconds())
    return (
        "agora"
        if s < 10
        else f"há {s} s"
        if s < 60
        else f"há {s // 60} min"
        if s < 3600
        else f"há {s // 3600} h"
        if s < 86400
        else f"há {s // 86400} d"
    )


def day_bounds() -> tuple[str, str]:
    """Meia-noite local de hoje e de 7 dias atrás, em ISO (o delegador guarda em UTC)."""
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat(timespec="seconds"), (midnight - timedelta(days=6)).isoformat(timespec="seconds")


def delegator_url() -> str:
    try:
        return config.load_global().delegator_url
    except config.ConfigError:
        from ..delegator import DEFAULT_URL

        return DEFAULT_URL


def client():
    from ..delegator.client import DelegatorClient

    return DelegatorClient(delegator_url(), timeout=3.0)


def fetch_overview() -> dict[str, Any] | None:
    """Painel dos agentes passivos (None = delegador indisponível)."""
    from ..delegator.client import DelegatorUnavailable

    today, week = day_bounds()
    try:
        return client().passive_overview(today, week)
    except (DelegatorUnavailable, RuntimeError, ValueError):
        return None


def runner_line(overview: dict | None) -> Text:
    if overview is None:
        return Text("delegador fora do ar", style=T.C_OFF)
    runner = overview.get("runner")
    if runner and runner.get("alive"):
        return Text.assemble(
            ("executor ● ativo ", T.C_OK), (f"desde {local_time(runner.get('started_at'), '%d/%m %H:%M')}", T.C_MUTED)
        )
    return Text("executor ○ parado (x inicia)", style=T.C_BUSY)


# ------------------------------------------------------------------- cartão


class PassiveCard(Static, can_focus=True):
    """Cartão de um agente passivo na tela inicial."""

    BINDINGS = [
        ("enter", "open", "Abrir"),
        Binding("left", "move(-1, 0)", show=False),
        Binding("right", "move(1, 0)", show=False),
        Binding("up", "move(0, -1)", show=False),
        Binding("down", "move(0, 1)", show=False),
    ]

    class Open(Message):
        def __init__(self, agent_id: str) -> None:
            super().__init__()
            self.agent_id = agent_id

    def __init__(self, agent: passive.PassiveAgent) -> None:
        super().__init__()
        self.agent = agent
        self.info: dict[str, Any] | None = None
        self.loaded = False

    def on_mount(self) -> None:
        self.set_info(None, loaded=False)

    def action_open(self) -> None:
        self.post_message(self.Open(self.agent.id))

    def on_click(self) -> None:
        self.focus()

    def action_move(self, dx: int, dy: int) -> None:
        mover = getattr(self.screen, "action_move", None)
        if mover is not None:
            mover(dx, dy)

    def set_info(self, info: dict[str, Any] | None, *, loaded: bool = True, delegator_ok: bool = True) -> None:
        self.info, self.loaded = info, loaded
        a = self.agent
        running = (info or {}).get("running")
        pending = int((info or {}).get("pending") or 0)
        last = (info or {}).get("last") or {}
        status = "busy" if running else "queue" if pending else "failed" if last.get("state") == "failed" else "free"
        if not loaded or not delegator_ok:
            status = "unknown"
        for name in ("busy", "queue", "failed", "free", "unknown"):
            self.set_class(status == name, f"p-{name}")
        self.border_title = Text(f" {a.id} ", style=f"bold {T.C_STRONG}")
        sub = {
            "busy": (f" ◐ ocupado{f' · fila {pending}' if pending else ''} ", T.C_BUSY),
            "queue": (f" ◷ fila {pending} ", T.C_NEW),
            "failed": (" ✗ última falhou ", T.C_OFF),
            "free": (" ● livre ", T.C_OK),
            "unknown": (" consultando… " if not loaded else " delegador fora do ar ", T.C_DIM),
        }[status]
        self.border_subtitle = Text(sub[0], style=f"bold {sub[1]}")
        t = Text()
        t.append((a.description or "(sem descrição)")[:70] + "\n", style=T.C_MUTED)
        t.append_text(T.badge(a.cli.upper(), CLI_COLOR.get(a.cli, T.C_UNK)))
        t.append(f" {a.model or 'padrão'}{f' · {a.effort}' if a.effort else ''}  ", style=T.C_TEXT)
        for label in a.permissions.labels()[:3]:
            color = T.C_OK if label == "leitura" else T.C_OFF if label in ("escrita", "comandos: todos") else T.C_BUSY
            t.append_text(T.badge(label.upper(), color))
            t.append(" ")
        t.append("\n\n")
        if running:
            t.append(f"◐ respondendo #{running['consult_id']}", style=T.C_BUSY)
            t.append(f"  {running.get('origin') or 'sem origem'} · {ago(running.get('since'))}", style=T.C_MUTED)
            if pending:
                t.append(f"   fila {pending}", style=f"bold {T.C_BUSY}")
        elif pending:
            t.append(f"◷ {pending} na fila", style=T.C_NEW)
        elif last:
            t.append("● livre", style=T.C_OK)
            t.append(f"  última {ago(last.get('finished_at'))} · {last.get('origin') or 'sem origem'}", style=T.C_MUTED)
        else:
            t.append(
                "● livre · nenhuma consulta ainda" if loaded and delegator_ok else "…",
                style=T.C_OK if loaded else T.C_DIM,
            )
        t.append("\n")
        day = (info or {}).get("today") or {}
        if day.get("consults"):
            cost = f" · {money(day.get('cost_usd'))}" if day.get("cost_usd") else ""
            t.append("hoje  ", style=T.C_MUTED)
            t.append(
                f"{day['consults']} consultas · {day.get('failed', 0)} falhas · {num(tokens_of(day))} tokens{cost}",
                style=T.C_TEXT,
            )
        else:
            t.append("hoje  nenhuma consulta", style=T.C_DIM)
        t.append("\n")
        state = passive.load_session(a.id)
        t.append("sessão ", style=T.C_MUTED)
        if a.cli not in passive.SESSION_CLIS:
            t.append(f"— ({a.cli} não guarda sessão)", style=T.C_DIM)
        elif state.id:
            t.append(f"{state.id[:8]} · {state.calls} consultas", style=T.C_TEXT)
        else:
            t.append("criada na 1ª consulta", style=T.C_DIM)
        if last.get("state") == "failed":
            t.append(f"\n▲ última falhou: {(last.get('preview') or '')[:60]}", style=T.C_OFF)
        self.update(t)


# ---------------------------------------------------------- tela do agente


class PassiveScreen(BaseScreen):
    BREADCRUMB = "agentes passivos"

    BINDINGS = [
        ("t", "test_consult", "Testar consulta"),
        ("e", "edit_passive", "Editar"),
        ("c", "cancel_consult", "Cancelar pendente"),
        ("y", "copy_result", "Copiar resposta"),
        ("f", "open_files", "Arquivos"),
        Binding("D", "remove_passive", "Remover"),
        ("r", "reload", "Atualizar"),
        ("escape", "back", "Voltar"),
    ]

    def __init__(self, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.agent: passive.PassiveAgent | None = None
        self._consults: list[dict[str, Any]] = []
        self._detail: dict[str, Any] | None = None
        self._refreshing = False

    def compose(self) -> ComposeResult:
        yield self.menu_bar()
        with Horizontal(classes="tiles"):
            yield Tile("Estado", id="pv-state")
            yield Tile("Hoje", id="pv-today")
            yield Tile("Tokens hoje", id="pv-tokens")
            yield Tile("Custo", id="pv-cost")
            yield Tile("Tempo de resposta", id="pv-time", classes="last")
        with Horizontal(id="pv-cols"):
            with Vertical(id="pv-left"):
                yield DataTable(id="pv-table", cursor_type="row", zebra_stripes=False)
                yield VerticalScroll(Static("", id="pv-detail-body"), id="pv-detail")
            with Vertical(id="pv-right"):
                yield Static("", id="pv-config")
                yield VerticalScroll(Static("", id="pv-events-body"), id="pv-events")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#pv-table", DataTable)
        table.add_columns("#", "ORIGEM", "ESTADO", "RECEBIDA", "DURAÇÃO", "TOKENS", "CUSTO", "RESPOSTA")
        table.border_title = Text(" Consultas (mais recentes primeiro) ", style=f"bold {T.C_STRONG}")
        table.border_subtitle = Text(" Enter/↑↓ mostra · c cancela a pendente · t testa ", style=T.C_MUTED)
        self.query_one("#pv-detail").border_title = Text(" Consulta ", style=f"bold {T.C_STRONG}")
        self.query_one("#pv-config").border_title = Text(" Configuração ", style=f"bold {T.C_STRONG}")
        self.query_one("#pv-config").border_subtitle = Text(" e edita · f abre os arquivos ", style=T.C_MUTED)
        self.query_one("#pv-events").border_title = Text(" Eventos ", style=f"bold {T.C_STRONG}")
        if not self._load_agent():
            return
        self._render_config()
        self.reload()
        self.set_interval(REFRESH_SECONDS, self._tick)
        table.focus()

    def on_screen_resume(self) -> None:
        if self._load_agent():
            self._render_config()
            self.reload()

    def _load_agent(self) -> bool:
        try:
            self.agent = passive.load(self.agent_id)
        except passive.PassiveError as exc:
            self.notify(str(exc), severity="error", markup=False)
            self.app.pop_screen()
            return False
        self.query_one("#menubar").set_path(f"agentes passivos › {self.agent_id}")
        return True

    def _tick(self) -> None:
        if self.app.screen is self and not self._refreshing:
            self.reload()

    def reload(self) -> None:
        self.run_worker(self._refresh, exclusive=True, group="passive")

    def action_reload(self) -> None:
        self.reload()

    async def _refresh(self) -> None:
        from ..delegator.client import DelegatorUnavailable

        self._refreshing = True
        try:

            def gather():
                c = client()
                today, week = day_bounds()
                return (
                    c.passive_overview(today, week),
                    c.passive_consults(self.agent_id, 60),
                    c.passive_timeline(self.agent_id, 40),
                )

            try:
                overview, consults, events = await asyncio.to_thread(gather)
            except (DelegatorUnavailable, RuntimeError, ValueError) as exc:
                self._render_offline(str(exc))
                return
            self._consults = consults
            info = (overview.get("agents") or {}).get(self.agent_id) or {}
            self._render_tiles(info, overview)
            self._render_table()
            self._render_events(events)
            if self._detail is None and consults:
                await self._load_detail(int(consults[0]["consult_id"]))
            elif self._detail and self._detail.get("state") in ("pending", "running"):
                await self._load_detail(int(self._detail["consult_id"]))
        finally:
            self._refreshing = False

    def _render_offline(self, error: str) -> None:
        self.query_one("#pv-state", Tile).set_content(Text("delegador fora do ar", style=T.C_OFF), error[:60])

    def _render_tiles(self, info: dict[str, Any], overview: dict[str, Any]) -> None:
        running, pending = info.get("running"), int(info.get("pending") or 0)
        if running:
            state = Text(f"◐ respondendo #{running['consult_id']}", style=f"bold {T.C_BUSY}")
        elif pending:
            state = Text(f"◷ {pending} na fila", style=f"bold {T.C_NEW}")
        else:
            state = Text("● livre", style=f"bold {T.C_OK}")
        hint = runner_line(overview)
        if running and pending:
            hint = Text.assemble((f"fila: {pending} aguardando · ", T.C_MUTED), hint)
        self.query_one("#pv-state", Tile).set_content(state, hint)
        day = info.get("today") or {}
        self.query_one("#pv-today", Tile).set_content(
            Text(f"{day.get('consults', 0)} consultas", style=f"bold {C_PASSIVE}"),
            f"{day.get('failed', 0)} falhas · {day.get('cancelled', 0)} canceladas",
        )
        self.query_one("#pv-tokens", Tile).set_content(
            Text(num(tokens_of(day)), style=f"bold {C_PASSIVE}"),
            f"entrada {num(day.get('input_tokens'))} · cache {num(day.get('cache_read_tokens'))} · saída {num(day.get('output_tokens'))}",
        )
        self.query_one("#pv-cost", Tile).set_content(
            Text(money(day.get("cost_usd")), style=f"bold {C_PASSIVE}"), f"7 dias: {money(info.get('week_cost_usd'))}"
        )
        self.query_one("#pv-time", Tile).set_content(
            Text(secs(day.get("median_ms")), style=f"bold {T.C_NEW}"), f"mediana hoje · p90 {secs(day.get('p90_ms'))}"
        )

    def _render_table(self) -> None:
        table = self.query_one("#pv-table", DataTable)
        key = None
        if table.row_count:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear()
        for c in self._consults:
            label, color = STATE_LABEL.get(c["state"], (c["state"], T.C_UNK))
            usage = c.get("usage") or {}
            cost = usage.get("cost_usd")
            duration = c.get("duration_ms")
            if c["state"] == "running":
                duration_text = f"{ago(c.get('claimed_at')).removeprefix('há ')}…"
            else:
                duration_text = secs(duration)
            table.add_row(
                str(c["consult_id"]), (c.get("origin") or "—")[:18], Text(label, style=color), local_time(c.get("created_at")),
                duration_text, num(tokens_of(usage)) if usage else "—", money(cost).removeprefix("US$ ") if cost else "—",
                Text((c.get("preview") or "")[:60], style=T.C_MUTED), key=str(c["consult_id"]),
            )  # fmt: skip
        if key is not None:
            try:
                table.move_cursor(row=table.get_row_index(key), animate=False)
            except Exception:  # noqa: BLE001 - a linha saiu da lista
                pass

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        cid = int(event.row_key.value)
        if self._detail and self._detail.get("consult_id") == cid:
            return
        self.run_worker(self._load_detail(cid), exclusive=True, group="passive-detail")

    async def _load_detail(self, consult_id: int) -> None:
        from ..delegator.client import DelegatorUnavailable

        try:
            self._detail = await asyncio.to_thread(lambda: client().passive_consult(consult_id))
        except (DelegatorUnavailable, RuntimeError, ValueError):
            return
        self._render_detail()

    def _render_detail(self) -> None:
        d = self._detail or {}
        box = self.query_one("#pv-detail")
        body = self.query_one("#pv-detail-body", Static)
        if not d:
            body.update(Text("Nenhuma consulta ainda. Tecle t para testar.", style=T.C_DIM))
            return
        box.border_title = Text(
            f" Consulta #{d['consult_id']} · {d.get('origin') or 'sem origem'} → {d['agent']} · {d.get('cli') or '?'} {d.get('model') or ''} ",
            style=f"bold {T.C_STRONG}",
        )
        label, color = STATE_LABEL.get(d["state"], (d["state"], T.C_UNK))
        t = Text()
        t.append(f"{label}", style=f"bold {color}")
        t.append(f"   recebida {local_time(d.get('created_at'), '%d/%m %H:%M:%S')}", style=T.C_MUTED)
        if d.get("finished_at"):
            t.append(f" · terminou {local_time(d.get('finished_at'))} · {secs(d.get('duration_ms'))}", style=T.C_MUTED)
        t.append("\n\nPedido\n", style=f"bold {T.C_NEW}")
        t.append((d.get("instruction") or "").strip()[:4000] + "\n", style=T.C_TEXT)
        if d.get("result"):
            t.append("\nResposta\n", style=f"bold {T.C_OK}")
            t.append(d["result"].strip()[:8000] + "\n", style=T.C_STRONG)
        if d.get("error"):
            t.append("\nErro\n", style=f"bold {T.C_OFF}")
            t.append(d["error"].strip()[:2000] + "\n", style=T.C_OFF)
        usage = d.get("usage") or {}
        if usage:
            t.append("\nUso  ", style=T.C_MUTED)
            parts = [f"entrada {num(usage.get('input_tokens'))}"]
            if usage.get("cache_read_tokens"):
                parts.append(f"cache {num(usage.get('cache_read_tokens'))}")
            parts.append(f"saída {num(usage.get('output_tokens'))}")
            if usage.get("cost_usd"):
                parts.append(money(usage.get("cost_usd")))
            if d.get("runner"):
                parts.append(f"executor {d['runner']}")
            t.append(" · ".join(parts), style=T.C_TEXT)
        body.update(t)

    def _render_events(self, events: list[dict[str, Any]]) -> None:
        t = Text()
        icons = {"pending": ("◷", T.C_NEW), "running": ("◐", T.C_BUSY), "done": ("●", T.C_OK), "failed": ("✗", T.C_OFF),
                 "cancelled": ("○", T.C_UNK)}  # fmt: skip
        for e in events:
            icon, color = icons.get(e.get("state") or "", ("·", T.C_MUTED))
            t.append(f"{local_time(e.get('ts'))}  ", style=T.C_MUTED)
            t.append(f"{icon} ", style=color)
            t.append(f"{e.get('text', '')}\n", style=T.C_OFF if e.get("state") == "failed" else T.C_TEXT)
        if not events:
            t.append("nenhum evento ainda", style=T.C_DIM)
        self.query_one("#pv-events-body", Static).update(t)

    def _render_config(self) -> None:
        a = self.agent
        if a is None:
            return
        p = a.permissions
        state = passive.load_session(a.id)
        t = Text()
        t.append("CLI          ", style=T.C_MUTED)
        t.append_text(T.badge(a.cli.upper(), CLI_COLOR.get(a.cli, T.C_UNK)))
        t.append(f"  {a.model or 'padrão'}{f' · {a.effort}' if a.effort else ''}\n", style=T.C_STRONG)
        t.append("Sessão       ", style=T.C_MUTED)
        if a.cli not in passive.SESSION_CLIS:
            t.append("— (não guarda sessão)\n", style=T.C_DIM)
        elif state.id:
            origin = f" · cópia de {state.origin[:8]}" if state.origin else ""
            t.append(f"{state.id[:8]} · {state.calls} consultas{origin}\n", style=T.C_TEXT)
        else:
            t.append("nova, criada na 1ª consulta\n", style=T.C_TEXT)
        t.append("Permissões   ", style=T.C_MUTED)
        t.append(", ".join(p.labels()) + "\n", style=T.C_OK if not (p.write or p.any_command) else T.C_BUSY)
        if p.commands and not p.any_command:
            t.append("Comandos     ", style=T.C_MUTED)
            t.append(", ".join(p.commands) + "\n", style=T.C_BUSY)
        if p.read_dirs:
            t.append("Pode ler     ", style=T.C_MUTED)
            t.append(" · ".join(p.read_dirs) + "\n", style=T.C_TEXT)
        t.append("Fila         ", style=T.C_MUTED)
        reset = f" · recomeça a cada {a.reset_every}" if a.reset_every else ""
        t.append(f"uma por vez · tempo limite {a.timeout} s{reset}\n", style=T.C_TEXT)
        profile = passive.read_profile(a.id)
        t.append("Perfil       ", style=T.C_MUTED)
        t.append(f"{len(profile)} caracteres\n" if profile else "vazio\n", style=T.C_TEXT if profile else T.C_OFF)
        files = passive.list_files(a.id)
        t.append("Arquivos     ", style=T.C_MUTED)
        names = " · ".join(str(f.relative_to(a.folder / passive.FILES_DIR)) for f in files[:4])
        t.append((names + (f" (+{len(files) - 4})" if len(files) > 4 else "")) if files else "nenhum", style=T.C_TEXT)
        self.query_one("#pv-config", Static).update(t)

    # ------------------------------------------------------------- ações

    def _selected(self) -> dict[str, Any] | None:
        table = self.query_one("#pv-table", DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return next((c for c in self._consults if str(c["consult_id"]) == key), None)

    def action_test_consult(self) -> None:
        self.app.push_screen(PassiveTestScreen(self.agent_id), lambda _r: self.reload())

    def action_edit_passive(self) -> None:
        if self.agent is None:
            return

        def done(saved: str | None) -> None:
            if saved and self._load_agent():
                self._render_config()

        self.app.push_screen(PassiveFormScreen(self.agent), done)

    def action_cancel_consult(self) -> None:
        item = self._selected()
        if item is None or item["state"] != "pending":
            self.notify("Selecione uma consulta na fila (só as pendentes podem ser canceladas).", markup=False)
            return
        try:
            client().passive_cancel(int(item["consult_id"]))
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Não cancelei: {exc}", severity="error", markup=False)
            return
        self.notify(f"Consulta #{item['consult_id']} cancelada.", markup=False)
        self.reload()

    def action_copy_result(self) -> None:
        text = (self._detail or {}).get("result")
        if not text:
            self.notify("A consulta selecionada não tem resposta.", markup=False)
            return
        tool = system_copy(text)
        if tool is None:
            self.app.copy_to_clipboard(text)
        self.notify("Resposta copiada.", markup=False)

    def action_open_files(self) -> None:
        if self.agent is None:
            return
        folder = self.agent.folder / passive.FILES_DIR
        folder.mkdir(parents=True, exist_ok=True)
        opener = shutil.which("xdg-open") or shutil.which("open")
        if opener:
            subprocess.Popen(
                [opener, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )  # noqa: S603
        self.notify(f"Material de consulta: {folder}", markup=False)

    def action_remove_passive(self) -> None:
        if self.agent is None:
            return

        def done(result: bool | None) -> None:
            if result is None:
                return
            for line in passive.remove(self.agent_id, sessions=result):
                self.notify(line, markup=False)
            self.app.pop_screen()

        self.app.push_screen(RemovePassiveScreen(self.agent), done)

    def action_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------- teste de consulta


class PassiveTestScreen(ModalScreen[None]):
    """Envia uma consulta pelo MCP do delegador (o mesmo caminho de qualquer consumidor) e mostra a resposta."""

    BINDINGS = [("escape", "close", "Fechar"), ("ctrl+s", "send", "Enviar")]

    def __init__(self, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        with Vertical(id="pt"):
            yield Label(
                f"Testar consulta · {self.agent_id}   (vai pelo MCP do delegador · Ctrl+S envia · Esc fecha)",
                classes="title",
            )
            yield TextArea("", id="pt-text")
            with Horizontal(classes="role-row"):
                yield Label("Origem", classes="mini")
                yield Input(value="teste-tui", id="pt-origin")
                yield Button("Enviar ▸", id="pt-send", variant="primary")
            yield VerticalScroll(Static("", id="pt-result"), id="pt-result-box")

    def on_mount(self) -> None:
        self.query_one("#pt-text", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pt-send":
            self.action_send()

    def action_send(self) -> None:
        text = self.query_one("#pt-text", TextArea).text.strip()
        if not text:
            self.notify("Escreva o pedido.", markup=False)
            return
        origin = text_of(self.query_one("#pt-origin", Input)) or "teste-tui"
        self.query_one("#pt-send", Button).disabled = True
        self.run_worker(self._send(text, origin), exclusive=True)

    def _show(self, text: Text) -> None:
        self.query_one("#pt-result", Static).update(text)

    async def _send(self, text: str, origin: str) -> None:
        url = delegator_url()
        try:
            sent = await asyncio.to_thread(mcp_http.consult, url, self.agent_id, text, origin)
        except mcp_http.McpError as exc:
            self._show(Text(f"Não enviei: {exc}", style=T.C_OFF))
            self.query_one("#pt-send", Button).disabled = False
            return
        cid = int(sent["consult_id"])
        note = sent.get("note")
        while True:
            try:
                state = await asyncio.to_thread(mcp_http.consult_result, url, cid)
            except mcp_http.McpError as exc:
                self._show(Text(f"#{cid}: {exc}", style=T.C_OFF))
                break
            label, color = STATE_LABEL.get(state.get("state", ""), (state.get("state", "?"), T.C_UNK))
            t = Text.assemble((f"#{cid}  ", f"bold {T.C_STRONG}"), (label, f"bold {color}"))
            if state.get("state") in mcp_http.FINAL_STATES:
                usage = state.get("usage") or {}
                t.append(f"  ·  {secs(state.get('duration_ms'))} · {num(tokens_of(usage))} tokens", style=T.C_MUTED)
                if usage.get("cost_usd"):
                    t.append(f" · {money(usage['cost_usd'])}", style=T.C_MUTED)
                t.append("\n\n")
                t.append(
                    (state.get("result") or state.get("error") or "").strip(),
                    style=T.C_STRONG if state.get("result") else T.C_OFF,
                )
                self._show(t)
                break
            if note:
                t.append(f"\n▲ {note}", style=T.C_BUSY)
            self._show(t)
            await asyncio.sleep(1.5)
        self.query_one("#pt-send", Button).disabled = False

    def action_close(self) -> None:
        self.dismiss(None)


# ----------------------------------------------------------------- remoção


class RemovePassiveScreen(ModalScreen["bool | None"]):
    """Confirma a remoção; devolve se as sessões da CLI também vão (None = cancelou)."""

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, agent: passive.PassiveAgent) -> None:
        super().__init__()
        self.agent = agent

    def compose(self) -> ComposeResult:
        a = self.agent
        sessions_found = passive.session_files(a)
        with Vertical(id="form"):
            yield Label(f"Remover o agente passivo {a.id}?", classes="title")
            yield Static(
                Text.assemble(
                    ("Apaga ", T.C_TEXT),
                    (str(a.folder), T.C_STRONG),
                    (" (cadastro, perfil e arquivos de consulta).\n", T.C_TEXT),
                    ("O histórico de consultas no delegador fica (registro de custo).", T.C_MUTED),
                )  # fmt: skip
            )
            yield Checkbox(
                f"Apagar também as sessões da CLI ({sessions_found[0]})" if sessions_found else "Apagar também as sessões da CLI (nenhuma)",
                value=False, id="rp-sessions", disabled=not sessions_found,
            )  # fmt: skip
            with Horizontal(classes="buttons"):
                yield Button("Remover", id="rp-yes", variant="error")
                yield Button("Cancelar", id="rp-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rp-yes":
            self.dismiss(self.query_one("#rp-sessions", Checkbox).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# -------------------------------------------------------------- formulário

SESSION_MODES = ["nova", "adotar existente"]


class PassiveFormScreen(ModalScreen["str | None"]):
    """Criação e edição de agente passivo: execução, permissões, perfil e arquivos; resumo ao vivo à direita."""

    BINDINGS = [
        ("escape", "cancel", "Cancelar"),
        ("ctrl+s", "save", "Salvar"),
        ("ctrl+g", "generate", "Gerar perfil com IA"),
    ]

    def __init__(self, agent: passive.PassiveAgent | None = None) -> None:
        super().__init__()
        self.editing = agent
        self._adopt: tuple[Path, str] | None = None  # (pasta do projeto, sessão) escolhidos para copiar

    def compose(self) -> ComposeResult:
        a = self.editing
        p = a.permissions if a else passive.Permissions()
        profile = passive.read_profile(a.id) if a else ""
        with Vertical(id="pf"):
            yield Label(
                ("Novo agente passivo" if a is None else f"Editar agente passivo {a.id}")
                + "   (Ctrl+S salva · Ctrl+G gera o perfil com IA · F2 modelos · Esc cancela)",
                classes="title",
            )
            with Horizontal(id="pf-cols"):
                with VerticalScroll(id="pf-left"):
                    with Vertical(classes="card"):
                        yield Label("① Identificação", classes="card-title")
                        yield Label("Id (é o que quem consulta informa: minúsculas, números, - e _)")
                        yield Input(value=a.id if a else "", id="pa-id", disabled=a is not None)
                        yield Label("Descrição (aparece em list_agents)")
                        yield Input(value=a.description if a else "", id="pa-desc")
                    with Vertical(classes="card"):
                        yield Label("② Execução", classes="card-title")
                        with Horizontal(classes="role-row"):
                            yield Label("CLI", classes="mini")
                            yield ChoiceChips(
                                list(passive.CLIS), a.cli if a else "claude", allow_none=False, id="pa-cli"
                            )
                        with Horizontal(classes="role-row"):
                            yield Label("Modelo", classes="mini")
                            yield ModelInput(
                                value=(a.model or "") if a else "", placeholder="padrão da CLI", id="pa-model"
                            )
                            yield Button("▾", id="pa-models", classes="pick")
                        with Horizontal(classes="role-row"):
                            yield Label("Esforço", classes="mini")
                            yield ChoiceChips([], a.effort if a else None, id="pa-effort")
                        with Horizontal(classes="role-row", id="pa-command-row"):
                            yield Label("Comando", classes="mini")
                            yield Input(
                                value=(a.command or "") if a else "",
                                placeholder="programa que lê o pedido no stdin",
                                id="pa-command",
                            )
                        with Horizontal(classes="role-row", id="pa-session-row"):
                            yield Label("Sessão", classes="mini")
                            yield ChoiceChips(SESSION_MODES, "nova", allow_none=False, id="pa-session")
                        with Horizontal(classes="role-row", id="pa-adopt-row"):
                            yield Label("Projeto", classes="mini")
                            yield PathInput(
                                value="", placeholder="pasta do projeto da sessão a copiar", id="pa-adopt-dir"
                            )
                            yield Button("Escolher sessão ▾", id="pa-adopt-pick")
                        yield Static("", id="pa-session-info", classes="session-info")
                        with Horizontal(classes="role-row"):
                            yield Label("Limite (s)", classes="mini")
                            yield Input(value=str(a.timeout if a else passive.DEFAULT_TIMEOUT), id="pa-timeout")
                            yield Label("Recomeçar", classes="mini")
                            yield Input(value=str(a.reset_every if a else 0), id="pa-reset", placeholder="0 = nunca")
                    with Vertical(classes="card"):
                        yield Label(
                            "③ Permissões (viram flags da CLI; o texto do perfil não impede nada)", classes="card-title"
                        )
                        yield Checkbox("Ler arquivos", value=p.read, id="pa-read")
                        yield Checkbox("Escrever / editar arquivos", value=p.write, id="pa-write")
                        yield Checkbox("Qualquer comando", value=p.any_command, id="pa-anycmd")
                        yield Label(
                            "Comandos liberados (prefixos separados por vírgula, ex.: sqlite3 -readonly, jq, head)"
                        )
                        yield Input(value=", ".join(p.commands), id="pa-cmds")
                        yield Checkbox("Web (WebFetch / WebSearch)", value=p.web, id="pa-web")
                        yield Checkbox("MCPs do usuário", value=p.user_mcps, id="pa-mcps")
                        yield Checkbox(
                            "Configurações do usuário (hooks, plugins, ex.: ai-memory)",
                            value=p.user_settings,
                            id="pa-usercfg",
                        )
                        yield Label("Pastas que pode ler, além da própria (separe com ;  · Ctrl+O acrescenta)")
                        yield PathInput(value="; ".join(p.read_dirs), id="pa-dirs")
                    with Vertical(classes="card"):
                        yield Label(
                            "④ Perfil (instruções e formato de resposta) e arquivos de consulta", classes="card-title"
                        )
                        yield TextArea(profile, id="pa-profile")
                        with Horizontal(classes="buttons"):
                            yield Button("Gerar com IA ▸", id="pa-gen")
                            yield Button("Modelo padrão", id="pa-template")
                            yield Button("Abrir pasta de arquivos", id="pa-files")
                        yield Static("", id="pa-gen-status")
                yield Static("", id="pf-summary")
            yield Static("", id="f-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Salvar", id="save", variant="primary")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self._sync_cli()
        self._refresh_summary()
        self.query_one("#pa-desc" if self.editing else "#pa-id", Input).focus()

    # ------------------------------------------------------------ estado

    def _cli(self) -> str:
        return self.query_one("#pa-cli", ChoiceChips).value or "claude"

    def _catalog(self):
        return catalog.catalog_for(self._cli())

    def _sync_cli(self) -> None:
        cli = self._cli()
        model = text_of(self.query_one("#pa-model", ModelInput))
        efforts = self._catalog().efforts_for(model) if cli in ("claude", "codex") else []
        chips = self.query_one("#pa-effort", ChoiceChips)
        current = chips.value
        chips.set_options(efforts)
        if current in efforts:
            chips.set_value(current)
        self.query_one("#pa-command-row").display = cli == "comando"
        self.query_one("#pa-session-row").display = cli == "claude" and self.editing is None
        adopt = (
            cli == "claude"
            and self.editing is None
            and self.query_one("#pa-session", ChoiceChips).value == "adotar existente"
        )
        self.query_one("#pa-adopt-row").display = adopt
        info = self.query_one("#pa-session-info", Static)
        if self.editing is not None and cli in passive.SESSION_CLIS:
            state = passive.load_session(self.editing.id)
            info.update(
                Text(
                    f"sessão atual {state.id[:8]} · {state.calls} consultas"
                    if state.id
                    else "sessão criada na 1ª consulta",
                    style=T.C_MUTED,
                )
            )
        elif adopt:
            info.update(
                Text(f"vai copiar a sessão {self._adopt[1][:8]} (a original fica intacta)", style=T.C_OK)
                if self._adopt else Text("informe a pasta do projeto e escolha a sessão", style=T.C_BUSY)
            )  # fmt: skip
        elif cli in passive.SESSION_CLIS:
            info.update(
                Text("nova, própria do agente: criada na 1ª consulta; as seguintes usam --resume", style=T.C_MUTED)
            )
        else:
            info.update(Text(f"{cli}: sem sessão (cada consulta leva o perfil)", style=T.C_MUTED))

    def _split(self, raw: str, sep: str) -> list[str]:
        return [x.strip() for x in raw.split(sep) if x.strip()]

    def _build(self) -> passive.PassiveAgent:
        agent_id = (self.editing.id if self.editing else (text_of(self.query_one("#pa-id", Input)) or "")).strip()
        passive.validate_id(agent_id)
        try:
            timeout = int(text_of(self.query_one("#pa-timeout", Input)) or passive.DEFAULT_TIMEOUT)
            reset = int(text_of(self.query_one("#pa-reset", Input)) or 0)
        except ValueError as exc:
            raise passive.PassiveError("tempo limite e recomeçar precisam ser números") from exc
        cli = self._cli()
        agent = passive.PassiveAgent(
            id=agent_id, description=text_of(self.query_one("#pa-desc", Input)) or "", cli=cli,
            model=text_of(self.query_one("#pa-model", ModelInput)), effort=self.query_one("#pa-effort", ChoiceChips).value,
            timeout=max(10, timeout), reset_every=max(0, reset),
            command=text_of(self.query_one("#pa-command", Input)) if cli == "comando" else None,
            permissions=passive.Permissions(
                read=self.query_one("#pa-read", Checkbox).value, write=self.query_one("#pa-write", Checkbox).value,
                any_command=self.query_one("#pa-anycmd", Checkbox).value,
                commands=self._split(self.query_one("#pa-cmds", Input).value, ","),
                web=self.query_one("#pa-web", Checkbox).value, user_mcps=self.query_one("#pa-mcps", Checkbox).value,
                user_settings=self.query_one("#pa-usercfg", Checkbox).value,
                read_dirs=[str(Path(d).expanduser()) for d in self._split(self.query_one("#pa-dirs", PathInput).value, ";")],
            ),
        )  # fmt: skip
        if cli == "comando" and not agent.command:
            raise passive.PassiveError("a CLI 'comando' precisa da linha de comando")
        if cli == "ollama" and not agent.model:
            raise passive.PassiveError("o Ollama precisa do modelo (▾ lista os instalados)")
        return agent

    def _refresh_summary(self) -> None:
        t = Text()
        try:
            agent = self._build()
        except (passive.PassiveError, ValueError) as exc:
            agent = None
            if self.editing is None and not (self.query_one("#pa-id", Input).value or "").strip():
                t.append("Preencha o id para ver como cada consulta vai rodar.\n", style=T.C_MUTED)
            else:
                t.append(f"▲ {exc}\n\n", style=T.C_BUSY)
        if agent is not None:
            t.append("Como cada consulta roda\n", style=f"bold {T.C_NEW}")
            for line in passive.describe(agent):
                t.append(f"  {line}\n", style=T.C_TEXT)
            t.append("\nQuem consulta (MCP do delegador)\n", style=f"bold {T.C_NEW}")
            t.append(
                f'  consult(agent_id="{agent.id}",\n          instruction="…", origin="…")\n  consult_result(consult_id)\n',
                style=T.C_TEXT,
            )
            t.append("  não sabe qual CLI responde\n", style=T.C_DIM)
            t.append("\nSerá gravado\n", style=f"bold {T.C_NEW}")
            t.append(f"  {passive.agent_dir(agent.id)}/\n    agente.toml · perfil.md · arquivos/\n", style=T.C_TEXT)
            warnings = []
            p = agent.permissions
            if p.write:
                warnings.append("escrita liberada: o agente pode alterar arquivos")
            if p.any_command:
                warnings.append("qualquer comando liberado")
            elif p.commands:
                warnings.append("comandos liberados: confira que nenhum escreve")
            if agent.cli in passive.SESSION_CLIS:
                warnings.append(
                    "--resume: o histórico cresce a cada consulta (custo de cache)"
                    + ("" if agent.reset_every else "; 'Recomeçar' limita")
                )
            if agent.cli == "codex":
                warnings.append(
                    "Codex: comandos rodam no sandbox (somente leitura se não houver escrita); não homologado"
                )
            if agent.cli == "ollama":
                warnings.append("Ollama não lê arquivos: perfil e arquivos pequenos vão no pedido")
            if not self.query_one("#pa-profile", TextArea).text.strip():
                warnings.append("perfil vazio: defina a missão e o formato de resposta")
            if warnings:
                t.append("\nAvisos\n", style=f"bold {T.C_BUSY}")
                for w in warnings:
                    t.append(f"  ▲ {w}\n", style=T.C_BUSY)
        self.query_one("#pf-summary", Static).update(t)

    # ------------------------------------------------------------ eventos

    def on_choice_chips_changed(self, event: ChoiceChips.Changed) -> None:
        if event.chips.id in ("pa-cli", "pa-session"):
            self._sync_cli()
        self._refresh_summary()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "pa-model":
            self._sync_cli()
        self._refresh_summary()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._refresh_summary()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "pa-profile":
            self._refresh_summary()

    def on_model_input_catalog(self, message: ModelInput.Catalog) -> None:
        self._pick_model()

    def _pick_model(self) -> None:
        field = self.query_one("#pa-model", ModelInput)

        def done(value: str | None) -> None:
            if value:
                field.value = value
                self._sync_cli()
                self._refresh_summary()

        self.app.push_screen(model_picker(self._catalog(), text_of(field)), done)

    def on_path_input_browse(self, message: PathInput.Browse) -> None:
        """Ctrl+O: nas pastas de leitura acrescenta a escolhida; na adoção, escolhe a pasta do projeto."""
        widget = message.input

        def chosen(value: str | None) -> None:
            if not value:
                return
            if widget.id == "pa-dirs":
                current = self._split(widget.value, ";")
                if value not in current:
                    widget.value = "; ".join([*current, value])
            else:
                widget.value = value
            self._refresh_summary()

        self.app.push_screen(FolderPicker(widget.value.split(";")[-1].strip()), chosen)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id
        if wid == "save":
            self.action_save()
        elif wid == "cancel":
            self.action_cancel()
        elif wid == "pa-models":
            self._pick_model()
        elif wid == "pa-adopt-pick":
            self._pick_session()
        elif wid == "pa-gen":
            self.action_generate()
        elif wid == "pa-template":
            try:
                agent = self._build()
            except (passive.PassiveError, ValueError) as exc:
                self.query_one("#f-error", Static).update(str(exc))
                return
            self.query_one("#pa-profile", TextArea).text = passive.default_profile(agent)
        elif wid == "pa-files":
            if self.editing is None:
                self.notify("Salve o agente antes: a pasta arquivos/ é criada ao salvar.", markup=False)
                return
            folder = self.editing.folder / passive.FILES_DIR
            folder.mkdir(parents=True, exist_ok=True)
            opener = shutil.which("xdg-open") or shutil.which("open")
            if opener:
                subprocess.Popen(
                    [opener, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
                )  # noqa: S603
            self.notify(f"Material de consulta: {folder}", markup=False)

    def _pick_session(self) -> None:
        raw = self.query_one("#pa-adopt-dir", PathInput).value.strip()
        if not raw:
            self.app.push_screen(FolderPicker(""), lambda chosen: self._set_adopt_dir(chosen))
            return
        folder = Path(raw).expanduser()
        found = sessions.list_sessions(folder, getattr(self.app, "services", None))
        if not found:
            self.notify(f"Nenhuma sessão do Claude em {folder}.", markup=False)
            return

        def done(value: str | None) -> None:
            if value:
                self._adopt = (folder, value)
                self._sync_cli()

        self.app.push_screen(session_picker(found), done)

    def _set_adopt_dir(self, chosen: str | None) -> None:
        if chosen:
            self.query_one("#pa-adopt-dir", PathInput).value = chosen
            self._pick_session()

    def action_generate(self) -> None:
        try:
            agent = self._build()
        except (passive.PassiveError, ValueError) as exc:
            self.query_one("#f-error", Static).update(f"Antes de gerar: {exc}")
            return
        request = agent.description
        if not request.strip():
            self.query_one("#f-error", Static).update("Escreva a descrição: é dela que a IA parte.")
            return
        provider = agent.cli if agent.cli in ("claude", "codex", "opencode", "ollama") else "claude"
        model = agent.model if provider == agent.cli else ""
        self.query_one("#pa-gen-status", Static).update(Text(f"gerando com {provider} {model or ''}…", style=T.C_BUSY))
        self.query_one("#pa-gen", Button).disabled = True
        self.run_worker(self._generate(agent, provider, model or "", request), exclusive=True, group="pa-gen")

    async def _generate(self, agent: passive.PassiveAgent, provider: str, model: str, request: str) -> None:
        folder = agent.folder if agent.folder.is_dir() else Path.home()
        fake = Project(id="passivo", name=agent.id, path=folder, roles={"passivo": Role(cli="claude")})
        prompt = passive.generation_prompt(agent, request)
        result = await asyncio.to_thread(generator.generate, provider, model, prompt, fake)
        self.query_one("#pa-gen", Button).disabled = False
        if result.error or not result.text.strip():
            self.query_one("#pa-gen-status", Static).update(
                Text(f"✗ {result.error or 'resposta vazia'}", style=T.C_OFF)
            )
            return
        self.query_one("#pa-profile", TextArea).text = result.text.strip()
        self.query_one("#pa-gen-status", Static).update(
            Text(f"✓ rascunho gerado por {provider} {model}: revise antes de salvar", style=T.C_OK)
        )

    def action_save(self) -> None:
        error = self.query_one("#f-error", Static)
        try:
            agent = self._build()
        except (passive.PassiveError, ValueError) as exc:
            error.update(str(exc))
            return
        if self.editing is None and passive.exists(agent.id):
            error.update(f"já existe um agente passivo com o id {agent.id}")
            return
        adopt = (
            self.editing is None
            and agent.cli == "claude"
            and self.query_one("#pa-session", ChoiceChips).value == "adotar existente"
        )
        if adopt and self._adopt is None:
            error.update("escolha a sessão a copiar (ou marque sessão nova)")
            return
        profile = self.query_one("#pa-profile", TextArea).text
        try:
            passive.save(agent, profile if profile.strip() else passive.default_profile(agent))
            if adopt and self._adopt:
                new_id = passive.adopt_session(agent, *self._adopt)
                self.notify(f"Sessão {self._adopt[1][:8]} copiada para o agente ({new_id[:8]}).", markup=False)
        except (passive.PassiveError, sessions.SessionError, OSError) as exc:
            error.update(str(exc))
            return
        self.notify(f"Agente passivo {agent.id} salvo.", markup=False)
        self.dismiss(agent.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------- executor


def toggle_runner(screen) -> None:
    """Liga ou desliga o executor em segundo plano (`dev-orchestra passive run`)."""
    if passive_runner.background_pid():
        passive_runner.stop_background()
        screen.notify("Executor parando (termina as consultas em andamento).", markup=False)
        return
    overview = fetch_overview()
    if overview and (overview.get("runner") or {}).get("alive"):
        screen.notify("Já há um executor ativo (iniciado fora da TUI).", markup=False)
        return
    pid = passive_runner.start_background(delegator_url())
    screen.notify(f"Executor iniciado (pid {pid}) · log: {passive_runner.log_file()}", markup=False)
