"""Telas principais da TUI: projetos (cartões), projeto (painéis) e subida/retomada."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import ContentSwitcher, DataTable, Footer, Static

from ..core import config, contract, dispatch, paths, portable, removal, state, summary, tasks
from ..core.clipboard import system_copy
from ..core.commands import build_launch_plan, format_plan, local_context
from ..core.config import atomic_write_text
from ..core.liveness import StatusReport, collect_status, describe_ai_memory
from ..core.models import GlobalConfig, Project
from . import theme as T
from .details import AgentDetailScreen, TaskDetailScreen
from .forms import (
    ConfirmScreen,
    ImportFormScreen,
    ImportPathScreen,
    ProjectFormScreen,
    TaskFormScreen,
    TaskStateScreen,
)
from .menus import BaseScreen
from .passive import PassiveCard, PassiveFormScreen, PassiveScreen
from .widgets import (
    TABS,
    AgentCard,
    KanbanBoard,
    LaunchCard,
    ProjectCard,
    TabsBar,
    TaskCard,
    Tile,
    activity_text,
)

REFRESH_SECONDS = 3.0  # a consulta completa leva ~0,6 s; 3 s mantém a tela perto do que acontece nas sessões
TWO_COLUMNS_MIN_WIDTH = 110  # abaixo disso os cartões ficam um por linha


def _selected_key(table: DataTable) -> str | None:
    if table.row_count == 0:
        return None
    return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value


def _restore_cursor(table: DataTable, key: str | None) -> None:
    """Depois de recarregar a tabela, devolve o cursor à mesma linha (a atualização automática não o move)."""
    if key is None:
        return
    try:
        table.move_cursor(row=table.get_row_index(key), animate=False)
    except Exception:  # noqa: BLE001 - a linha deixou de existir
        pass


# ===================================================================== projetos


class ProjectsScreen(BaseScreen):
    BREADCRUMB = "projetos"

    BINDINGS = [
        ("n", "new", "Novo"),
        ("m", "import_existing", "Importar existente"),
        ("a", "open_monitor", "Monitor"),
        ("p", "new_passive", "Agente passivo"),
        ("x", "toggle_runner", "Executor"),
        Binding("d", "remove", "Remover cadastro", show=False),
        ("r", "reload", "Atualizar"),
        ("q", "app.quit", "Sair"),
        Binding("left", "move(-1, 0)", show=False),
        Binding("right", "move(1, 0)", show=False),
        Binding("up", "move(0, -1)", show=False),
        Binding("down", "move(0, 1)", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._projects: list[Project] = []
        self._columns = 2
        self._pcolumns = 3
        self._refreshing = False
        self._passives: list = []
        self._overview: dict | None = None

    def compose(self) -> ComposeResult:
        yield self.menu_bar()
        with Horizontal(classes="tiles"):
            yield Tile("Projetos", id="tile-projects")
            yield Tile("Sessões abertas", id="tile-sessions")
            yield Tile("Tarefas em andamento", id="tile-tasks")
            yield Tile("Agentes passivos", id="tile-passive")
            yield Tile("Infraestrutura", id="tile-infra", classes="last")
        with VerticalScroll():
            yield Static("", id="empty")
            yield Grid(id="grid")
            yield Static("", id="passive-head")
            yield Grid(id="pgrid")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_columns()
        self.reload()
        self.set_interval(REFRESH_SECONDS, self._tick_refresh)

    def on_screen_resume(self) -> None:
        self.reload()

    def on_alerts_changed(self) -> None:
        """Os cartões mostram os alertas: redesenha com o que há de novo."""
        for card in self._cards():
            card.set_summary(card.summary)
        self._equalize()

    def on_resize(self) -> None:
        self._apply_columns()
        self._equalize()

    def _apply_columns(self) -> None:
        self._columns = 2 if self.size.width >= TWO_COLUMNS_MIN_WIDTH else 1
        self._pcolumns = 3 if self.size.width >= 170 else self._columns
        try:
            self.query_one("#grid", Grid).styles.grid_size_columns = self._columns
            self.query_one("#pgrid", Grid).styles.grid_size_columns = self._pcolumns
            self.query_one(".tiles").styles.height = 6 if self._columns == 2 else 9  # estreito: textos quebram mais
        except Exception:  # noqa: BLE001 - a tela ainda não foi montada
            pass

    # ------------------------------------------------------------- dados

    def _cards(self) -> list[ProjectCard]:
        return list(self.query(ProjectCard))

    def _pcards(self) -> list[PassiveCard]:
        return list(self.query(PassiveCard))

    def _reload_passives(self) -> None:
        from ..core import passive

        agents = passive.list_agents()
        self._passives = agents
        grid = self.query_one("#pgrid", Grid)
        cards = self._pcards()
        if [c.agent.id for c in cards] != [a.id for a in agents]:
            grid.remove_children()
            if agents:
                grid.mount(*[PassiveCard(a) for a in agents])
        else:
            for card, agent in zip(cards, agents, strict=True):
                card.agent = agent
        self._update_passive(self._overview, loaded=self._overview is not None)

    def _update_passive(self, overview: dict | None, *, loaded: bool = True) -> None:
        from .passive import C_PASSIVE, money, num, runner_line, tokens_of

        agents = (overview or {}).get("agents") or {}
        for card in self._pcards():
            card.set_info(agents.get(card.agent.id), loaded=loaded, delegator_ok=overview is not None)
        head = Text.assemble(
            ("AGENTES PASSIVOS", f"bold {C_PASSIVE}"), ("   sem sessão aberta · rodam por consulta · ", T.C_DIM)
        )
        if loaded:
            head.append_text(runner_line(overview))
        head.append("   p novo · x executor", style=T.C_DIM)
        if not self._passives:
            head.append(
                "\n  nenhum agente passivo: tecle p para criar um auxiliar (análise, revisão…) chamado pelo MCP",
                style=T.C_MUTED,
            )
        self.query_one("#passive-head", Static).update(head)
        tile = self.query_one("#tile-passive", Tile)
        if not loaded:
            tile.set_content(Text(str(len(self._passives)), style=f"bold {T.C_STRONG}"), Text("…", style=T.C_DIM))
            return
        queue = sum(int(a.get("pending") or 0) + (1 if a.get("running") else 0) for a in agents.values())
        consults = sum(int((a.get("today") or {}).get("consults") or 0) for a in agents.values())
        tokens = sum(tokens_of(a.get("today")) for a in agents.values())
        cost = sum(float((a.get("today") or {}).get("cost_usd") or 0) for a in agents.values())
        value = Text.assemble(
            (str(len(self._passives)), f"bold {T.C_STRONG}"), (f"  · fila {queue}", T.C_BUSY if queue else T.C_MUTED)
        )
        hint = runner_line(overview)
        spent = f" · {money(cost)}" if cost else ""
        hint.append(f"\nhoje {consults} consultas · {num(tokens)} tokens{spent}", style=T.C_MUTED)
        tile.set_content(value, hint)

    def reload(self) -> None:
        projects = config.list_projects()
        self._projects = projects
        grid = self.query_one("#grid", Grid)
        cards = self._cards()
        if [c.project.id for c in cards] != [p.id for p in projects]:
            grid.remove_children()
            new_cards = [ProjectCard(p) for p in projects]
            if new_cards:
                grid.mount(*new_cards)
                self.call_after_refresh(new_cards[0].focus)
        else:
            for card, project in zip(cards, projects, strict=True):
                card.project = project
                card.set_summary(card.summary)
        self._equalize()
        empty = self.query_one("#empty", Static)
        empty.display = not projects  # sem projetos, mostra a orientação; com projetos, não ocupa linhas
        empty.update(Text("Nenhum projeto cadastrado. Tecle n para cadastrar ou m para importar um existente."))
        self._update_tiles({})
        self._reload_passives()
        self._start_refresh()

    def _tick_refresh(self) -> None:
        if self.app.screen is self and not self._refreshing:  # nem com janela aberta por cima, nem sobreposta
            self._start_refresh()

    def _start_refresh(self) -> None:
        self.run_worker(self._refresh_all, exclusive=True, group="home")

    async def _refresh_all(self) -> None:
        from .passive import fetch_overview

        projects = list(self._projects)
        self._refreshing = True
        try:
            if self._passives:
                self._overview = await asyncio.to_thread(fetch_overview)
                try:
                    self._update_passive(self._overview)
                    self._equalize()
                except Exception:  # noqa: BLE001 - a tela pode ter sido fechada durante a consulta
                    return
            if not projects:
                return
            cfg = config.load_global()
            services = getattr(self.app, "services", None)
            reports, infra = await asyncio.to_thread(summary.collect_all, projects, cfg, services)
            summaries = {p.id: summary.build_summary(p, reports.get(p.id)) for p in projects}
            try:
                for card in self._cards():
                    found = summaries.get(card.project.id)
                    if found is not None:
                        card.set_summary(found)
                self._update_tiles(summaries, infra)
                self._equalize()
            except Exception:  # noqa: BLE001 - a tela pode ter sido fechada durante a consulta
                return
        finally:
            self._refreshing = False

    # -------------------------------------------------- cartões de mesma altura

    def _equalize(self) -> None:
        self.call_after_refresh(self._apply_heights)

    def _needed_height(self, card: ProjectCard) -> int:
        width = card.content_size.width
        if width <= 0:
            return 0  # ainda não foi desenhado
        return card.get_content_height(self.size, self.app.size, width) + 2  # + bordas

    def _apply_heights(self) -> None:
        """Em cada linha da grade, todos os cartões ficam com a altura do maior."""
        for cards, columns in ((self._cards(), self._columns), (self._pcards(), self._pcolumns)):
            self._apply_row_heights(cards, columns)

    def _apply_row_heights(self, cards: list, columns: int) -> None:
        for start in range(0, len(cards), columns):
            row = cards[start : start + columns]
            height = max((self._needed_height(c) for c in row), default=0)
            if height <= 0:
                continue
            for card in row:
                if card.styles.height is None or card.styles.height.value != height:
                    card.styles.height = height

    def _update_tiles(self, summaries: dict[str, summary.ProjectSummary], infra: summary.Infra | None = None) -> None:
        projects = self._projects
        own = sum(p.method == "proprio" for p in projects)
        self.query_one("#tile-projects", Tile).set_content(
            Text(str(len(projects)), style=f"bold {T.C_STRONG}"),
            f"{summary.plural(own, 'importado')} · {summary.plural(len(projects) - own, 'novo')}",
        )
        if not summaries:
            for tid in ("tile-sessions", "tile-tasks", "tile-infra"):
                self.query_one(f"#{tid}", Tile).set_content(Text("…", style=T.C_DIM))
            return
        items = list(summaries.values())
        opened = sum(s.opened for s in items)
        total = sum(s.total for s in items)
        busy = sum(a.state == "ocupada" for s in items for a in s.agents)
        self.query_one("#tile-sessions", Tile).set_content(
            Text.assemble((str(opened), f"bold {T.C_OK if opened else T.C_OFF}"), (f" / {total}", T.C_MUTED)),
            f"{summary.plural(busy, 'ocupada')} · {summary.plural(opened - busy, 'ociosa')}",
        )
        running = sum(s.tasks.running for s in items)
        review = sum(s.tasks.review for s in items)
        ready = sum(s.tasks.ready for s in items)
        self.query_one("#tile-tasks", Tile).set_content(
            Text.assemble((str(running), f"bold {T.C_BUSY}"), ("  executando", T.C_MUTED)),
            f"{review} aguardando teste · {summary.plural(ready, 'pronta')}",
        )
        self.query_one("#tile-infra", Tile).set_content(*self._infra_content(infra))

    @staticmethod
    def _infra_content(infra: summary.Infra | None) -> tuple[Text, Text]:
        if infra is None:
            return Text("…", style=T.C_DIM), Text("")
        mem_ok = infra.ai_memory_ok
        first = Text.assemble(
            ("● ", T.C_OK if mem_ok else T.C_OFF),
            ("AI Memory ", T.C_TEXT),
            ("no ar" if mem_ok else "indisponível", T.C_MUTED if mem_ok else T.C_OFF),
        )
        port = infra.app_server_url.rsplit(":", 1)[-1] if infra.app_server_url else ""
        if infra.app_server is None:
            second = Text.assemble(("○ ", T.C_UNK), ("App Server ", T.C_TEXT), ("não usado", T.C_MUTED))
        elif infra.app_server:
            second = Text.assemble(("● ", T.C_OK), (f"App Server :{port} ", T.C_TEXT), ("no ar", T.C_MUTED))
        else:
            second = Text.assemble(("● ", T.C_OFF), (f"App Server :{port} ", T.C_TEXT), ("fora do ar", T.C_OFF))
        dport = infra.delegator_url.rsplit(":", 1)[-1] if infra.delegator_url else ""
        if infra.delegator is None:
            third = Text.assemble(("○ ", T.C_UNK), ("Delegador ", T.C_TEXT), ("não usado", T.C_MUTED))
        elif infra.delegator:
            third = Text.assemble(("● ", T.C_OK), (f"Delegador :{dport} ", T.C_TEXT), ("no ar", T.C_MUTED))
        else:
            third = Text.assemble(("● ", T.C_OFF), (f"Delegador :{dport} ", T.C_TEXT), ("fora do ar", T.C_OFF))
        return first, Text("\n").join([second, third])

    # ------------------------------------------------------------- ações

    def on_project_card_open(self, message: ProjectCard.Open) -> None:
        self.app.push_screen(ProjectScreen(message.project_id))

    def action_move(self, dx: int, dy: int) -> None:
        """Navega pelas grades: esquerda/direita ficam dentro da linha; cima/baixo trocam de linha (e de grade)."""
        projects, passives = self._cards(), self._pcards()
        if not projects and not passives:
            return
        focused = self.focused
        if focused not in projects and focused not in passives:
            (projects or passives)[0].focus()
            return
        cards, cols = (projects, self._columns) if focused in projects else (passives, self._pcolumns)
        row, col = divmod(cards.index(focused), cols)
        new_row, new_col = row + dy, col + dx
        if not 0 <= new_col < cols:
            return
        if new_row < 0 or new_row * cols >= len(cards):
            # passa de uma grade para a outra (projetos ↔ agentes passivos)
            if cards is projects and new_row >= 0:
                other, index = passives, col  # desce para a primeira linha dos passivos
            elif cards is passives and new_row < 0 and projects:
                other, index = projects, ((len(projects) - 1) // self._columns) * self._columns + col  # sobe
            else:
                return
            if not other:
                return
            target_card = other[min(index, len(other) - 1)]
            target_card.focus()
            target_card.scroll_visible()
            return
        target = min(new_row * cols + new_col, len(cards) - 1)
        cards[target].focus()
        cards[target].scroll_visible()

    def on_passive_card_open(self, message: PassiveCard.Open) -> None:
        self.app.push_screen(PassiveScreen(message.agent_id))

    def action_new_passive(self) -> None:
        def done(saved: str | None) -> None:
            self.reload()
            if saved:
                self.app.push_screen(PassiveScreen(saved))

        self.app.push_screen(PassiveFormScreen(), done)

    def action_toggle_runner(self) -> None:
        from .passive import toggle_runner

        toggle_runner(self)
        self.set_timer(1.5, self._start_refresh)

    def action_open_selected(self) -> None:
        focused = self.focused
        if isinstance(focused, PassiveCard):
            focused.action_open()
            return
        if isinstance(focused, ProjectCard):
            focused.action_open()
        else:
            self.notify("Selecione um projeto com as setas.", markup=False)

    def action_reload(self) -> None:
        self.reload()

    def action_new(self) -> None:
        def done(created: Project | None) -> None:
            self.reload()
            if created is not None and created.delegator:  # modo delegador recém-ligado: confere serviço e hooks
                from .delegator_setup import DelegatorSetupScreen

                self.app.push_screen(DelegatorSetupScreen(created))

        self.app.push_screen(ProjectFormScreen(), done)

    def action_import_existing(self) -> None:
        def scanned(result) -> None:
            if result is not None:
                self.app.push_screen(ImportFormScreen(result), lambda _p: self.reload())

        self.app.push_screen(ImportPathScreen(), scanned)

    def action_remove(self) -> None:
        focused = self.focused
        if not isinstance(focused, ProjectCard):
            return
        key = focused.project.id

        def done(confirmed: bool | None) -> None:
            if confirmed:
                config.remove_project(key)
                self.notify(f"Cadastro {key} removido (a pasta do projeto não foi tocada).", markup=False)
                self.reload()

        self.app.push_screen(ConfirmScreen(f"Remover o cadastro de {key}? A pasta do projeto não é tocada."), done)


# ===================================================================== projeto


class ProjectScreen(BaseScreen):
    BREADCRUMB = "projeto"

    BINDINGS = [
        ("escape", "back", "Voltar"),
        ("u", "launch", "Subir/retomar"),
        ("i", "send", "Instruções"),
        ("g", "send_task", "Enviar tarefa"),
        Binding("a", "open_monitor", "Monitor", show=False),
        Binding("I", "send_one", "Instruções do agente", show=False),
        Binding("R", "resend", "Reenviar instruções", show=False),
        Binding("D", "remove_agent", "Remover agente", show=False),
        Binding("G", "resend_task", "Reenviar tarefa", show=False),
        Binding("1", "tab_overview", "Visão geral", show=False),
        Binding("2", "tab_agents", "Agentes", show=False),
        Binding("3", "tab_tasks", "Tarefas", show=False),
        Binding("y", "import_trello", "Trello", show=False),
        Binding("t", "new_task", "Nova tarefa", show=False),
        Binding("s", "task_state", "Estado", show=False),
        Binding("x", "cancel_delegation", "Cancelar delegação", show=False),
        Binding("e", "export", "Exportar", show=False),
        Binding("f", "scaffold", "Arquivos do método", show=False),
        Binding("p", "edit", "Editar", show=False),
        Binding("n", "new_profile", "Novo perfil", show=False),
        Binding("l", "setup_delegator", "Preparar delegador", show=False),
        ("r", "reload", "Atualizar"),
    ]

    def __init__(self, project_id: str, initial_tab: str = "overview") -> None:
        super().__init__()
        self.project_id = project_id
        self.initial_tab = initial_tab
        self.project: Project | None = None
        self.cfg: GlobalConfig = GlobalConfig()
        self.report: StatusReport | None = None
        self.summary: summary.ProjectSummary | None = None
        self.tab = "overview"
        self._task_warnings: list[str] = []
        self._live: dict[str, str] = {}  # tarefa -> estado no delegador (quando ele sobrepõe o arquivo)
        self._tasks_signature: tuple | None = None
        self._refreshing = False

    def compose(self) -> ComposeResult:
        yield self.menu_bar()
        yield TabsBar(id="tabs")
        with ContentSwitcher(initial="view-overview", id="views"):
            with Vertical(id="view-overview"):
                yield Static("", id="banner", classes="panel")
                yield Static("", id="stepper")
                with Horizontal(id="cols"):
                    with Vertical(id="left"):
                        yield DataTable(id="roles", cursor_type="row", classes="panel")
                        yield Static("", id="agent-detail")
                    with Vertical(id="right"):
                        yield DataTable(id="tasks", cursor_type="row", zebra_stripes=True, classes="panel")
                yield Static("", id="activity", classes="panel")
            yield VerticalScroll(id="view-agents")
            with Vertical(id="view-tasks"):
                yield KanbanBoard(id="board")
        yield Footer()

    def on_mount(self) -> None:
        roles = self.query_one("#roles", DataTable)
        roles.border_title = "Agentes"
        roles.add_columns("AGENTE", "CLI", "MODELO", "SESSÃO", "INSTRUÇÕES", "DETALHE")
        tasks_table = self.query_one("#tasks", DataTable)
        tasks_table.border_title = "Tarefas"
        tasks_table.add_columns("ID", "ESTADO", "PAPEL", "TÍTULO", "ENVIADA")
        self.query_one("#activity", Static).border_title = "Atividade recente"
        if not self._load_project():
            return
        self.show_tab(self.initial_tab)
        self.fill_tasks()
        self._render_roles()
        self.action_refresh()
        self.set_interval(REFRESH_SECONDS, self._tick_refresh)  # acompanha as sessões enquanto você as abre

    def on_screen_resume(self) -> None:
        if self.project is not None:  # voltou de outra tela: atualiza na hora
            self.fill_tasks()
            self._start_refresh()

    def _tick_refresh(self) -> None:
        if self.app.screen is self and not self._refreshing:  # não consulta com um formulário aberto por cima
            self._start_refresh()

    def _load_project(self) -> bool:
        try:
            self.project = config.load_project(self.project_id)
            self.cfg = config.load_global()
        except config.ConfigError as exc:
            self.app.notify(str(exc), severity="error", markup=False)
            self.app.pop_screen()
            return False
        banner = self.query_one("#banner", Static)
        banner.border_title = Text.assemble(
            (f" {self.project.name} ", f"bold {T.C_STRONG}"), (f" {self.project.id} ", T.C_MUTED)
        )
        self._render_header()
        return True

    # -------------------------------------------------------------- abas

    def show_tab(self, tab: str) -> None:
        self.tab = tab
        self.query_one("#views", ContentSwitcher).current = f"view-{tab}"
        label = f"{self.project.name} · {self.project.id}" if self.project else ""
        self.query_one(TabsBar).set_active(tab, label)
        names = dict(TABS)
        self.query_one("#menubar").set_path(f"{self.BREADCRUMB} › {names[tab].lower()}")
        if tab == "agents":
            self.call_after_refresh(self._focus_first, AgentCard)
        elif tab == "tasks":
            self.call_after_refresh(self._focus_first, TaskCard)

    def _focus_first(self, kind: type) -> None:
        cards = list(self.query(kind))
        if cards and self.focused not in cards:
            cards[0].focus()

    def action_tab_overview(self) -> None:
        self.show_tab("overview")

    def action_tab_agents(self) -> None:
        self.show_tab("agents")

    def action_tab_tasks(self) -> None:
        self.show_tab("tasks")

    def on_tabs_bar_selected(self, message: TabsBar.Selected) -> None:
        self.show_tab(message.tab)

    # ------------------------------------------------------------ painéis

    def _rebuild_summary(self) -> None:
        if self.project is not None:
            self.summary = summary.build_summary(self.project, self.report)

    def _render_header(self) -> None:
        """Faixa do projeto, passo a passo e atividade (tudo o que depende do resumo)."""
        p = self.project
        if p is None:
            return
        self._rebuild_summary()
        s = self.summary
        assert s is not None
        banner = Text()
        banner.append(f"{p.path}\n", style=T.C_MUTED)
        banner.append_text(T.method_badge(p.method))
        banner.append(" ")
        banner.append_text(T.source_badge(p.task_source.kind))
        banner.append("  ")
        if p.task_source.kind == "trello" and p.task_source.trello_board:
            banner.append(f"{p.task_source.trello_board}   ", style=T.C_TEXT)
        memory = describe_ai_memory(p, self.report.ai_memory) if self.report else "AI Memory: consultando…"
        banner.append(memory, style=T.C_OK if "retomada" in memory else T.C_NEW)
        identity = (
            f"prefixo dos agentes: {p.prefix or p.id}" if p.method == "padrao" else "nomes dos agentes preservados"
        )
        banner.append(f"\n{identity}", style=T.C_DIM)
        self.query_one("#banner", Static).update(banner)
        self.query_one("#stepper", Static).update(self._stepper_text(s))
        self.query_one("#activity", Static).update(activity_text(s.recent, self._task_warnings))

    @staticmethod
    def _stepper_text(s: summary.ProjectSummary) -> Text:
        def color(done: int, total: int) -> str:
            return T.C_OK if total and done == total else (T.C_UNK if done == 0 else T.C_BUSY)

        t = Text()
        t.append("  ① Cadastro ", style=f"bold {T.C_OK}")
        t.append("✓", style=f"bold {T.C_OK}")
        t.append("  ──  ", style="#3b4452")
        c = color(s.opened, s.total) if s.report else T.C_DIM
        t.append("② Sessões ", style=f"bold {c}")
        t.append(f"{s.opened}/{s.total} abertas", style=c)
        t.append("  ──  ", style="#3b4452")
        c = color(s.sent, s.total) if s.report else T.C_DIM
        t.append("③ Instruções ", style=f"bold {c}")
        t.append(f"{s.sent}/{s.total} enviadas", style=c)
        t.append("  ──  ", style="#3b4452")
        t.append("④ Tarefas ", style=f"bold {T.C_NEW}")
        counts = s.tasks
        if counts.total:
            t.append(
                f"{counts.running} executando · {counts.ready} prontas · {counts.done}/{counts.total} concluídas",
                style=T.C_NEW,
            )
        else:
            t.append("nenhuma ainda", style=T.C_UNK)
        t.append(f"\n  próximo passo: {summary.next_step(s)}.", style=T.C_MUTED)
        return t

    def _render_roles(self) -> None:
        p = self.project
        if p is None:
            return
        self._rebuild_summary()
        s = self.summary
        assert s is not None
        table = self.query_one("#roles", DataTable)
        keep = _selected_key(table)
        table.clear()
        for row in s.agents:
            sent_style = T.C_MUTED if row.sent else T.C_BUSY
            table.add_row(
                Text(row.name, style=f"bold {T.C_STRONG}"),
                Text(row.cli, style=T.C_OWN if row.cli == "claude" else T.C_TRELLO),
                Text(row.model, style=T.C_TEXT),
                T.state_text(row.state),
                Text(row.sent_short, style=sent_style),
                Text(row.detail_short, style=T.C_MUTED),
                key=row.role,
            )
        _restore_cursor(table, keep)
        self._render_agent_cards(s.agents)

    def _render_agent_cards(self, rows: list[summary.AgentRow]) -> None:
        """Aba Agentes: um cartão por agente, atualizado no lugar (o foco não se perde)."""
        container = self.query_one("#view-agents", VerticalScroll)
        cards = list(container.query(AgentCard))
        if [c.row.role for c in cards] == [r.role for r in rows]:
            for card, row in zip(cards, rows, strict=True):
                card.set_row(row)
            return
        wanted = self.focused.row.role if isinstance(self.focused, AgentCard) else None
        container.remove_children()
        new_cards = [AgentCard(r) for r in rows]
        container.mount(*new_cards)
        if wanted:
            target = next((c for c in new_cards if c.row.role == wanted), None)
            if target is not None:
                self.call_after_refresh(target.focus)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Mostra o detalhe completo do agente destacado (a tabela só cabe o resumo)."""
        if event.data_table.id != "roles" or self.summary is None or event.row_key.value is None:
            return
        row = next((a for a in self.summary.agents if a.role == event.row_key.value), None)
        text = Text(f"  {row.name}: {row.detail or 'sem detalhes'}", style=T.C_MUTED) if row else Text("")
        self.query_one("#agent-detail", Static).update(text)

    # --------------------------------------------------------- agentes

    def agent_move(self, step: int) -> None:
        cards = list(self.query(AgentCard))
        if not cards or self.focused not in cards:
            return
        target = cards[max(0, min(len(cards) - 1, cards.index(self.focused) + step))]
        target.focus()
        target.scroll_visible()

    def selected_agent_role(self) -> str | None:
        if isinstance(self.focused, AgentCard):
            return self.focused.row.role
        if self.tab == "overview":
            return _selected_key(self.query_one("#roles", DataTable))
        return None

    def action_agent_detail(self) -> None:
        role = self.selected_agent_role()
        row = next((a for a in self.summary.agents if a.role == role), None) if self.summary else None
        if row is None:
            return

        def done(choice: str | None) -> None:
            if choice == "send_one":
                self.action_send_one()
            elif choice == "launch":
                self.action_launch()

        self.app.push_screen(AgentDetailScreen(row), done)

    # ---------------------------------------------------------- tarefas

    def selected_task_id(self) -> str | None:
        if self.tab == "tasks":
            return self.query_one(KanbanBoard).focused_task_id()
        return _selected_key(self.query_one("#tasks", DataTable))

    def board_move(self, dx: int, dy: int) -> None:
        self.query_one(KanbanBoard).move(dx, dy)

    def on_task_card_open(self, message: TaskCard.Open) -> None:
        if self.project is None:
            return

        def done(choice: str | None) -> None:
            if choice == "send":
                self.action_send_task()
            elif choice == "state":
                self.action_task_state()

        self.app.push_screen(TaskDetailScreen(self.project, message.task_id, self._live.get(message.task_id)), done)

    def fill_tasks(self) -> None:
        """Relê as tarefas do disco; só mexe nas telas se algo mudou (o orquestrador também as edita)."""
        assert self.project is not None
        items, warnings = tasks.list_tasks(self.project)
        delegated = self.report.delegator.get("tasks") if self.report and self.report.delegator else None
        items = summary.apply_live(
            items, delegated
        )  # o estado de execução do delegador vale até o arquivo ser atualizado
        self._live = {t.id: t.live for t in items if t.live}
        self._task_warnings = warnings
        sent = tuple(state.task_sent_label(state.task_sent_info(self.project.id, t.id)) for t in items)
        signature = (tuple((t.id, t.state, t.live, t.title, t.role) for t in items), sent)
        if signature != self._tasks_signature:
            self._tasks_signature = signature
            table = self.query_one("#tasks", DataTable)
            keep = _selected_key(table)
            table.clear()
            for t, label in zip(items, sent, strict=True):
                table.add_row(
                    Text(t.id, style=f"bold {T.C_STRONG}"),
                    T.task_state_text(t.state, t.live),
                    t.role,
                    t.title,
                    Text(label, style=T.C_MUTED),
                    key=t.id,
                )
            _restore_cursor(table, keep)
        self.query_one(KanbanBoard).populate(self.project.id, items)
        self._render_header()

    def action_new_task(self) -> None:
        assert self.project is not None
        self.app.push_screen(TaskFormScreen(self.project), lambda _t: self.fill_tasks())

    def action_task_state(self) -> None:
        assert self.project is not None
        key = self.selected_task_id()
        if key is None:
            self.notify("Nenhuma tarefa selecionada.")
            return
        task = tasks.read_task(self.project, key)

        def done(new_state: str | None) -> None:
            if new_state and new_state != task.state:
                tasks.set_task_state(self.project, key, new_state)
                state.log_activity(self.project.id, f"Tarefa {key}: estado {task.state} → {new_state}", "info")
                self.fill_tasks()

        self.app.push_screen(TaskStateScreen(key, task.state), done)

    def action_cancel_delegation(self) -> None:
        """Cancela, no delegador, a tarefa selecionada (a pessoa pode; o worker é avisado)."""
        if self.project is None:
            return
        if not self.project.delegator:
            self.notify("Este projeto não usa o delegador.", markup=False)
            return
        key = self.selected_task_id()
        if key is None:
            self.notify("Nenhuma tarefa selecionada.", markup=False)
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._do_cancel(key), exclusive=True, group="send")

        self.app.push_screen(
            ConfirmScreen(f"Cancelar a delegação da tarefa {key}? O worker é avisado e o orquestrador também."), done
        )

    async def _do_cancel(self, key: str) -> None:
        from ..delegator.client import DelegatorClient, DelegatorUnavailable

        assert self.project is not None
        client = DelegatorClient(self.cfg.delegator_url)
        try:
            result = await asyncio.to_thread(client.cancel, self.project.id, key, "cancelada pela pessoa na TUI")
        except (DelegatorUnavailable, RuntimeError) as exc:
            self.notify(f"Não foi possível cancelar: {exc}", severity="warning", markup=False)
            return
        state.log_activity(self.project.id, f"Delegação de {key} cancelada ({result['task_state']})", "info")
        self.notify(f"Delegação de {key} cancelada no delegador.", markup=False)
        self._start_refresh()

    # ----------------------------------------------------------- status

    def action_refresh(self) -> None:
        self._start_refresh()

    def action_reload(self) -> None:
        """Atualiza agora: relê as tarefas e consulta as sessões."""
        if self.project is not None:
            self.fill_tasks()
            self._start_refresh()

    def _start_refresh(self) -> None:
        if self.project is None:
            return
        self.run_worker(self._refresh_status, exclusive=True, group="status")

    async def _refresh_status(self) -> None:
        assert self.project is not None
        self._refreshing = True
        try:
            services = getattr(self.app, "services", None)
            report = await asyncio.to_thread(collect_status, self.project, self.cfg, services)
            self.report = report
            try:
                self.fill_tasks()
                self._render_roles()
                self._render_header()
            except Exception:  # noqa: BLE001 - a tela pode ter sido fechada durante a consulta
                return
        finally:
            self._refreshing = False

    # ------------------------------------------------------------- ações

    def action_send_one(self) -> None:
        """Envia as instruções só ao agente selecionado (aba Agentes ou tabela da visão geral)."""
        role = self.selected_agent_role()
        if role is None:
            self.notify("Selecione um agente (aba Agentes) para enviar as instruções só a ele.", markup=False)
            return
        self.action_send(only={role})

    def action_resend(self) -> None:
        """Reenvia as instruções (ao agente selecionado, ou a todos) mesmo que já tenham sido enviadas."""
        role = self.selected_agent_role()
        self.action_send(only={role} if role else None, again=True)

    def _resend_now(self, only: set[str] | None) -> None:
        """Já confirmado na pergunta de reenvio: envia sem pedir uma segunda confirmação."""
        if self.project is None or self.report is None:
            return
        items = dispatch.plan_instructions(self.project, self.report, only=only, again=True)
        if any(i.action != "skip" for i in items):
            self.run_worker(self._do_send(items), exclusive=True, group="send")
        else:
            self.notify(dispatch.format_plan(items), title="Nada a enviar", timeout=12, markup=False)

    def action_send(self, only: set[str] | None = None, again: bool = False) -> None:
        """Envia as instruções aos agentes com sessão aberta (pede confirmação)."""
        if self.project is None:
            return
        if self.report is None:
            self.notify("Aguarde a consulta de status terminar e tente de novo.", markup=False)
            return
        items = dispatch.plan_instructions(self.project, self.report, only=only, again=again)
        if not any(i.action != "skip" for i in items):
            already = [i for i in items if i.action == "skip" and i.resume and "já " in i.reason]
            if already and not again:  # nada novo a enviar, mas há quem já recebeu: oferece o reenvio
                names = ", ".join(i.name for i in already)
                text = (
                    f"As instruções já foram enviadas a: {names}.\n"
                    + "\n".join(f"  {i.name}: {i.reason.replace('; use --again para reenviar', '')}" for i in already)
                    + "\n\nReenviar? Use quando a configuração ou o modelo estava errado,\n"
                    "ou a sessão perdeu o contexto. O texto é enviado de novo como retomada."
                )
                self.app.push_screen(ConfirmScreen(text), lambda ok: self._resend_now(only) if ok else None)
                return
            self.notify(dispatch.format_plan(items), title="Nada a enviar", timeout=12, markup=False)
            return
        text = ("REENVIO\n" if again else "") + dispatch.format_plan(items) + "\n\nEnviar agora?"

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._do_send(items), exclusive=True, group="send")

        self.app.push_screen(ConfirmScreen(text), done)

    def action_resend_task(self) -> None:
        """Reenvia a tarefa selecionada ao orquestrador, mesmo que já tenha sido enviada."""
        self.action_send_task(again=True)

    def _resend_task_now(self, key: str) -> None:
        if self.project is None or self.report is None:
            return
        try:
            item = dispatch.plan_task(self.project, self.report, key, again=True)
        except tasks.TaskError as exc:
            self.notify(str(exc), severity="error", markup=False)
            return
        if item.action == "skip":
            self.notify(item.reason, title=f"Tarefa {key} não enviada", timeout=12, markup=False)
            return
        self.run_worker(self._do_send_task(key, item), exclusive=True, group="send")

    def action_send_task(self, again: bool = False) -> None:
        """Envia a tarefa selecionada ao orquestrador (pede confirmação; se já foi enviada, oferece o reenvio)."""
        if self.project is None:
            return
        key = self.selected_task_id()
        if key is None:
            self.notify("Nenhuma tarefa selecionada.", markup=False)
            return
        if self.report is None:
            self.notify("Aguarde a consulta de status terminar e tente de novo.", markup=False)
            return
        try:
            item = dispatch.plan_task(self.project, self.report, key, again=again)
        except tasks.TaskError as exc:
            self.notify(str(exc), severity="error", markup=False)
            return
        if item.action == "skip":
            if not again and item.reason.startswith("tarefa já enviada"):
                text = (
                    f"{item.reason.replace('; use --again', '')}.\n\n"
                    f"Reenviar a tarefa {key} ao orquestrador {item.name}? Use quando o envio anterior falhou, a\n"
                    "configuração ou o modelo estava errado, ou o orquestrador não a recebeu.\n"
                    "A tarefa é enviada de novo; o arquivo e o estado dela não mudam."
                )
                self.app.push_screen(ConfirmScreen(text), lambda ok: self._resend_task_now(key) if ok else None)
                return
            self.notify(item.reason, title=f"Tarefa {key} não enviada", timeout=12, markup=False)
            return
        text = ("REENVIO: " if again else "") + f"Enviar a tarefa {key} ao orquestrador {item.name}?\n{item.reason}"

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._do_send_task(key, item), exclusive=True, group="send")

        self.app.push_screen(ConfirmScreen(text), done)

    def action_import_trello(self) -> None:
        """Pede ao orquestrador que traga os cartões do Trello para arquivos de tarefa locais."""
        if self.project is None:
            return
        if self.report is None:
            self.notify("Aguarde a consulta de status terminar e tente de novo.", markup=False)
            return
        item = dispatch.plan_trello_import(self.project, self.report)
        if item.action == "skip":
            self.notify(item.reason, title="Importação do Trello não enviada", timeout=12, markup=False)
            return
        board = self.project.task_source.trello_board or "(board não informado)"
        text = (
            f"Pedir ao orquestrador {item.name} que importe os cartões do Trello ({board}) "
            f"para {self.project.tasks_dir}/?\n"
            "É só leitura do Trello: ele não altera cartões nem inicia tarefas.\n"
            f"{item.reason}"
        )

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._do_import_trello(item), exclusive=True, group="send")

        self.app.push_screen(ConfirmScreen(text), done)

    async def _do_import_trello(self, item: dispatch.Item) -> None:
        assert self.project is not None
        result = await asyncio.to_thread(dispatch.execute_trello_import, self.project, self.cfg, item)
        self.notify(
            dispatch.format_results([result]),
            title="Importação do Trello",
            severity="warning" if not result[1].ok else "information",
            timeout=15,
            markup=False,
        )
        self._render_header()

    async def _do_send_task(self, key: str, item: dispatch.Item) -> None:
        assert self.project is not None
        result = await asyncio.to_thread(dispatch.execute_task, self.project, self.cfg, key, item)
        failed = not result[1].ok
        self.notify(
            dispatch.format_results([result]),
            title=f"Tarefa {key}",
            severity="warning" if failed else "information",
            timeout=15,
            markup=False,
        )
        self.fill_tasks()

    async def _do_send(self, items: list[dispatch.Item]) -> None:
        assert self.project is not None and self.report is not None
        results = await asyncio.to_thread(dispatch.execute, self.project, self.cfg, self.report, items)
        failed = any(not r.ok for _, r in results)
        self.notify(
            dispatch.format_results(results),
            title="Instruções enviadas" if not failed else "Envio com falhas",
            severity="warning" if failed else "information",
            timeout=15,
            markup=False,
        )
        self._start_refresh()

    def action_launch(self) -> None:
        if self.project is not None:
            self.app.push_screen(LaunchScreen(self.project, self.cfg, self.report))

    def action_export(self) -> None:
        if self.project is None:
            return
        try:
            target = portable.export_project(self.project)
        except OSError as exc:
            self.notify(f"Falha ao exportar: {exc}", severity="error", markup=False)
            return
        state.log_activity(self.project.id, f"Configuração portátil exportada para {target}", "info")
        self.notify(f"Configuração portátil exportada para {target}", markup=False)
        self._render_header()

    def action_scaffold(self) -> None:
        """Cria os arquivos do método que faltam no projeto (não sobrescreve nada)."""
        if self.project is None:
            return
        try:
            report = contract.scaffold(self.project)
        except OSError as exc:
            self.notify(f"Falha ao criar os arquivos: {exc}", severity="error", markup=False)
            return
        state.log_activity(self.project.id, f"Arquivos do método: {len(report.created)} criados", "info")
        self.notify(contract.format_report(report), title="Arquivos do método", timeout=12, markup=False)
        self._render_header()

    def action_remove_agent(self) -> None:
        """Remove o agente selecionado e o que foi gerado para ele (mostra o plano e pede confirmação)."""
        if self.project is None:
            return
        role = self.selected_agent_role()
        if role is None:
            self.notify("Selecione um agente (aba Agentes) para removê-lo.", markup=False)
            return
        live = False
        if self.report is not None:
            live = any(r.role == role and r.state in ("ociosa", "ocupada") for r in self.report.roles)
        plan = removal.plan_removal(self.project, role, live_session=live)
        if plan.blocked:
            self.notify(plan.blocked, title="Agente não removido", severity="warning", markup=False)
            return

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                lines = removal.execute(plan)
            except (ValueError, OSError, config.ConfigError) as exc:
                self.notify(str(exc), severity="error", markup=False)
                return
            self.notify("\n".join(lines), title=f"Agente {plan.agent} removido", timeout=12, markup=False)
            if self._load_project():
                self.fill_tasks()
                self._render_roles()
                self.action_refresh()

        self.app.push_screen(ConfirmScreen(plan.text()), done)

    def action_new_profile(self) -> None:
        """Gera, revisa e salva o perfil do papel selecionado (provedor de IA à escolha)."""
        if self.project is None:
            return
        role = self.selected_agent_role()
        if role is None:
            self.notify("Selecione um agente (aba Agentes) para criar o perfil dele.", markup=False)
            return
        from .profile_form import ProfileScreen

        self.app.push_screen(ProfileScreen(self.project, role))

    def action_setup_delegator(self) -> None:
        """Confere o serviço e os hooks do delegador neste projeto e resolve o que faltar."""
        if self.project is None:
            return
        if not self.project.delegator:
            self.notify("O projeto não está em modo delegador: ligue em Editar cadastro (p).", markup=False)
            return
        from .delegator_setup import DelegatorSetupScreen

        self.app.push_screen(DelegatorSetupScreen(self.project))

    def action_edit(self) -> None:
        if self.project is None:
            return
        was_delegator = self.project.delegator

        def done(_p: Project | None) -> None:
            if _p is not None and self._load_project():
                self.fill_tasks()
                self._render_roles()
                self.action_refresh()
                if _p.delegator and not was_delegator:  # acabou de ligar o delegador: confere o que falta
                    self.action_setup_delegator()

        self.app.push_screen(ProjectFormScreen(self.project), done)

    def action_back(self) -> None:
        self.app.pop_screen()


# ======================================================================= subida


class LaunchScreen(BaseScreen):
    """Plano de subida em cartões. Copiar: `c` (seleção ou tudo), `1`-`9` (só os comandos de um terminal),
    `x` (sai e imprime no terminal normal, onde a seleção do terminal funciona)."""

    BREADCRUMB = "projeto › subir ou retomar"

    BINDINGS = [
        ("escape", "back", "Voltar"),
        ("c", "copy", "Copiar seleção ou tudo"),
        ("x", "print", "Sair e imprimir"),
        *[Binding(str(n), f"copy_block({n})", f"Comandos do terminal {n}", show=False) for n in range(1, 10)],
    ]

    def __init__(self, project: Project, cfg: GlobalConfig, report: StatusReport | None) -> None:
        super().__init__()
        self.project = project
        ctx = local_context(
            project,
            workstreams=report.workstream_names if report else None,
            app_server_up=report.app_server_up if report else None,
        )
        self.plan = build_launch_plan(project, cfg, ctx)
        self._memory = describe_ai_memory(project, report.ai_memory) if report else "AI Memory: não consultado."
        header = (
            f"Subir ou retomar {project.id} — um bloco por terminal, na ordem. Você executa cada comando.\n"
            f"{self._memory}\n"
            "Depois de abrir as sessões, volte à tela do projeto e tecle i para enviar as instruções a cada agente.\n"
            "Teclas: c copia a seleção (ou tudo) | 1-9 copiam só os comandos do terminal N | "
            "x sai e imprime no terminal.\n\n"
        )
        self.plan_text = header + format_plan(self.plan)

    def compose(self) -> ComposeResult:
        yield self.menu_bar()
        banner = Static(self._banner_text(), id="banner", classes="panel")
        banner.border_title = "Plano de subida"
        yield banner
        with VerticalScroll():
            with Grid(id="grid"):
                for n, block in enumerate(self.plan.blocks, start=1):
                    subtitle = f"[{n}] copiar" if block.commands else "sem comando"
                    yield LaunchCard(f"{n} · {block.title}", subtitle, self._card(block), T.C_NEW)
                yield LaunchCard("Depois de abrir", "próximos passos", self._next_steps(), T.C_OK)
        yield Footer()

    def _banner_text(self) -> Text:
        t = Text()
        t.append("Subir ou retomar ", style=f"bold {T.C_STRONG}")
        t.append(self.project.id, style="bold #58a6ff")
        t.append("  —  um bloco por terminal; você executa cada comando.\n", style=T.C_MUTED)
        known = "retomada" in self._memory or "já existe" in self._memory
        t.append(f"{self._memory}\n", style=T.C_OK if known else T.C_NEW)
        for warning in self.plan.warnings:
            t.append(f"aviso: {warning}\n", style=T.C_BUSY)
        t.append(
            "Teclas 1–9 copiam só os comandos do terminal; c copia a seleção (ou tudo); x imprime no terminal normal.",
            style=T.C_MUTED,
        )
        return t

    @staticmethod
    def _card(block) -> Text:
        t = Text()
        for note in block.notes:
            t.append(f"{note}\n", style=T.C_MUTED)
        if block.commands:
            t.append("\n")
        for command in block.commands:
            t.append(f"{command}\n", style=T.C_TEXT)
        if block.prompt:
            t.append(
                "\nO texto de abertura é enviado pela tecla i, na tela do projeto (ou impresso com x).",
                style=T.C_DIM,
            )
        t.rstrip()
        return t

    @staticmethod
    def _next_steps() -> Text:
        t = Text()
        t.append("1. Abra os terminais acima, um por agente.\n", style=T.C_TEXT)
        t.append("2. Volte ao projeto: o status se atualiza sozinho.\n", style=T.C_TEXT)
        t.append("3. Tecle i para enviar as instruções e g para enviar uma tarefa.\n", style=T.C_TEXT)
        t.append("\nO que a ferramenta fizer aparece em Atividade recente.", style=T.C_MUTED)
        return t

    def _copy(self, text: str, what: str) -> None:
        """Copia pela ferramenta do sistema; sem ela, tenta o terminal (OSC 52) e salva um arquivo."""
        tool = system_copy(text)
        if tool:
            self.notify(f"{what} copiado ({len(text)} caracteres, via {tool}).", markup=False)
            return
        try:
            self.app.copy_to_clipboard(text)  # depende do terminal; não há como saber se funcionou
        except Exception:  # noqa: BLE001
            pass
        target = paths.install_dir() / "ultimo-plano.txt"
        try:
            atomic_write_text(target, text)
            where = f" Também salvei em {target}."
        except OSError:
            where = ""
        self.notify(
            f"Tentei copiar {what.lower()} pelo terminal, que pode não aceitar.{where} "
            "Alternativas: tecle x (imprime no terminal normal), segure Shift e arraste o mouse e use "
            "Ctrl+Shift+C, ou instale wl-clipboard (sudo apt install wl-clipboard) e use c de novo.",
            severity="warning",
            timeout=15,
            markup=False,
        )

    def action_copy(self) -> None:
        selected = self.get_selected_text()
        if selected and selected.strip():
            self._copy(selected, "Trecho selecionado")
        else:
            self._copy(self.plan_text, "Plano inteiro")

    def action_copy_block(self, number: int) -> None:
        blocks = self.plan.blocks
        if not 1 <= number <= len(blocks):
            self.notify(f"Não há terminal {number} neste plano.", markup=False)
            return
        block = blocks[number - 1]
        if not block.commands:
            self.notify(f"O terminal {number} ({block.title}) não tem comando para copiar.", markup=False)
            return
        self._copy("\n".join(block.commands) + "\n", f"Comandos do terminal {number}")

    def action_print(self) -> None:
        """Sai da TUI e imprime o plano no terminal normal (aí a seleção e a cópia do terminal funcionam)."""
        self.app.exit(format_plan(self.plan))

    def action_back(self) -> None:
        self.app.pop_screen()
