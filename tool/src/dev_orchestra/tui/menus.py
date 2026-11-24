"""Barra de menus no topo (Arquivo, Projeto, Agentes, Tarefas, Ajuda), o menu suspenso e a tela base das telas."""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from . import theme as T


@dataclass(frozen=True)
class Entry:
    label: str
    key: str  # tecla mostrada ao lado (a ação continua disponível por ela)
    action: str  # `action_<nome>` da tela; "app.quit" fecha o aplicativo


# Cada ação só fica disponível na tela que a implementa; nas outras o item aparece apagado.
MENUS: dict[str, list[Entry | None]] = {
    "Arquivo": [
        Entry("Novo projeto", "n", "new"),
        Entry("Importar projeto existente", "m", "import_existing"),
        Entry("Exportar configuração", "e", "export"),
        None,
        Entry("Remover cadastro", "d", "remove"),
        None,
        Entry("Sair", "q", "app.quit"),
    ],
    "Projeto": [
        Entry("Abrir projeto selecionado", "Enter", "open_selected"),
        Entry("Voltar", "Esc", "back"),
        None,
        Entry("Visão geral", "1", "tab_overview"),
        Entry("Agentes", "2", "tab_agents"),
        Entry("Tarefas (kanban)", "3", "tab_tasks"),
        None,
        Entry("Subir / retomar sessões", "u", "launch"),
        Entry("Editar cadastro", "p", "edit"),
        Entry("Preparar delegador (serviço e hooks)", "l", "setup_delegator"),
        Entry("Arquivos do método", "f", "scaffold"),
        None,
        Entry("Copiar plano de subida", "c", "copy"),
        Entry("Imprimir plano e sair", "x", "print"),
        None,
        Entry("Atualizar agora", "r", "reload"),
    ],
    "Passivos": [
        Entry("Novo agente passivo", "p", "new_passive"),
        Entry("Abrir agente selecionado", "Enter", "open_selected"),
        Entry("Ligar / desligar o executor", "x", "toggle_runner"),
        None,
        Entry("Testar consulta", "t", "test_consult"),
        Entry("Editar agente", "e", "edit_passive"),
        Entry("Cancelar consulta pendente", "c", "cancel_consult"),
        Entry("Copiar resposta", "y", "copy_result"),
        Entry("Abrir arquivos de consulta", "f", "open_files"),
        None,
        Entry("Remover agente passivo…", "Shift+D", "remove_passive"),
    ],
    "Agentes": [
        Entry("Enviar instruções a todos", "i", "send"),
        Entry("Enviar instruções ao agente", "Shift+I", "send_one"),
        Entry("Reenviar instruções (já enviadas)", "Shift+R", "resend"),
        Entry("Comandos de subida", "u", "launch"),
        None,
        Entry("Novo perfil do agente (IA)", "n", "new_profile"),
        Entry("Remover agente… (apaga o que foi gerado)", "Shift+D", "remove_agent"),
    ],
    "Tarefas": [
        Entry("Nova tarefa", "t", "new_task"),
        Entry("Enviar ao orquestrador", "g", "send_task"),
        Entry("Reenviar ao orquestrador", "Shift+G", "resend_task"),
        Entry("Mudar estado", "s", "task_state"),
        Entry("Cancelar delegação (delegador)", "x", "cancel_delegation"),
        None,
        Entry("Importar cartões do Trello", "y", "import_trello"),
    ],
    "Monitor": [
        Entry("Abrir o monitor de atividade", "a", "open_monitor"),
        Entry("Pausar / continuar", "p", "toggle_pause"),
        Entry("Alternar filtros (todos, atenção, fluxo)", "f", "cycle_filter"),
        Entry("Buscar na atividade", "/", "search"),
        None,
        Entry("Copiar o evento", "c", "copy_event"),
        Entry("Ir para a tarefa do evento", "t", "goto_task"),
        Entry("Cancelar a tarefa do evento", "x", "cancel_event"),
    ],
    "Alertas": [
        Entry("Abrir a central de alertas", "F3", "open_alerts"),
        Entry("Reconhecer o alerta", "a", "acknowledge"),
        Entry("Silenciar o alerta (30 min)", "s", "snooze"),
        Entry("Silenciar / reativar este tipo", "m", "mute_kind"),
        Entry("Ir ao agente do alerta", "Enter", "goto_agent"),
        Entry("Abrir no monitor", "o", "open_in_monitor"),
        Entry("Cancelar a tarefa do alerta", "x", "cancel_task"),
        None,
        Entry("Configurar som e notificações", "c", "configure"),
    ],
    "Ajuda": [
        Entry("Teclas de atalho", "F1", "help_keys"),
        Entry("Legenda de cores", "F2", "help_legend"),
        Entry("Sobre o dev-orchestra", "", "about"),
    ],
}
MNEMONIC = {
    "Arquivo": "a",
    "Projeto": "p",
    "Passivos": "s",
    "Agentes": "g",
    "Tarefas": "t",
    "Monitor": "m",
    "Alertas": "l",
    "Ajuda": "u",
}
_POPUP_WIDTH = 44


def is_available(screen: Screen, entry: Entry) -> bool:
    if entry.action.startswith("app."):
        return True
    return callable(getattr(screen, f"action_{entry.action}", None))


class MenuBar(Static):
    """Barra de menus: clique num nome (ou F10 / Alt+letra) para abrir o menu."""

    class Clicked(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, path: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.path = path
        self.ranges: dict[str, tuple[int, int]] = {}

    def on_mount(self) -> None:
        self.refresh_bar()

    def set_path(self, path: str) -> None:
        self.path = path
        self.refresh_bar()

    def refresh_bar(self) -> None:
        t = Text()
        t.append(" ◆ dev-orchestra ", style=f"bold {T.C_STRONG} on #1f3a5a")
        t.append("  ")
        self.ranges = {}
        for name in MENUS:
            start = len(t.plain)
            t.append(" ")
            letter = MNEMONIC[name]
            index = name.lower().index(letter)
            t.append(name[:index], style=T.C_TEXT)
            t.append(name[index], style=f"bold underline {T.C_STRONG}")
            t.append(name[index + 1 :] + " ", style=T.C_TEXT)
            self.ranges[name] = (start, len(t.plain))
        if self.path:
            t.append("      " + self.path, style=T.C_MUTED)
        center = getattr(self.app, "alerts", None)
        active = center.active if center else []
        if active:  # o sino: quantos alertas pedem a atenção da pessoa e o pior deles
            top = center.top_severity()
            color = {"crit": T.C_OFF, "warn": T.C_BUSY, "info": T.C_NEW}[top]
            t.append("   ")
            t.append(f" ▲ {len(active)} alerta{'s' if len(active) != 1 else ''} ", style=f"bold #10141a on {color}")
        self.update(t)

    def left_of(self, name: str) -> int:
        return self.ranges.get(name, (0, 0))[0]

    def on_click(self, event: Click) -> None:
        for name, (start, end) in self.ranges.items():
            if start <= event.x < end:
                self.post_message(self.Clicked(name))
                event.stop()
                return


class MenuPopup(ModalScreen["tuple[str, str] | None"]):
    """Menu suspenso. Devolve ("run", ação), ("switch", outro menu) ou None."""

    DEFAULT_CSS = """
    MenuPopup { align: left top; background: transparent; }
    MenuPopup #menu-box { width: 46; height: auto; max-height: 90%; background: #161b22; border: round #3f7fbf; }
    MenuPopup OptionList { height: auto; background: #161b22; border: none; padding: 0; }
    """
    BINDINGS = [
        Binding("escape", "close", "Fechar"),
        Binding("left", "switch(-1)", show=False),
        Binding("right", "switch(1)", show=False),
    ]

    def __init__(self, name: str, host: Screen, left: int) -> None:
        super().__init__()
        self.menu_name = name
        self.host_screen = host
        self.left = left

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-box"):
            yield OptionList(*self._options(), id="menu-list")

    def _options(self) -> list[Option | None]:
        out: list[Option | None] = []
        pending_separator = False
        for entry in MENUS[self.menu_name]:
            if entry is None:
                pending_separator = bool(out)
                continue
            if not is_available(self.host_screen, entry):
                continue  # o que não existe nesta tela não aparece (evita um menu cheio de itens mortos)
            if pending_separator:
                out.append(None)
                pending_separator = False
            label = Text()
            label.append(f" {entry.label}".ljust(_POPUP_WIDTH - 12), style=T.C_TEXT)
            label.append(f"{entry.key} ".rjust(9), style=T.C_MUTED)
            out.append(Option(label, id=entry.action))
        if not out:
            out.append(Option(Text(" indisponível nesta tela", style=T.C_DIM), disabled=True))
        return out

    def on_mount(self) -> None:
        self.query_one("#menu-box").styles.offset = (self.left, 1)
        options = self.query_one("#menu-list", OptionList)
        options.border_title = ""
        options.highlighted = next(
            (i for i in range(options.option_count) if not options.get_option_at_index(i).disabled), None
        )
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(("run", str(event.option.id)))

    def on_click(self, event: Click) -> None:
        if event.widget is self:  # clique fora do menu
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_switch(self, step: int) -> None:
        names = list(MENUS)
        self.dismiss(("switch", names[(names.index(self.menu_name) + step) % len(names)]))


class InfoScreen(ModalScreen[None]):
    """Janela de texto (teclas, legenda, sobre). Fecha com Esc ou Enter."""

    BINDINGS = [("escape", "close", "Fechar"), ("enter", "close", "Fechar"), ("q", "close", "Fechar")]

    def __init__(self, title: str, body: Text) -> None:
        super().__init__()
        self._title, self._body = title, body

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form"):
            yield Static(Text(self._title, style=f"bold {T.C_STRONG}"), classes="title")
            yield Static(self._body)
            yield Static(Text("Esc fecha", style=T.C_DIM))

    def action_close(self) -> None:
        self.dismiss(None)


def keys_text(screen: Screen) -> Text:
    """Todas as teclas do menu, agrupadas, mais a navegação (o que o menu mostra é a fonte única)."""
    t = Text()
    for name, entries in MENUS.items():
        shown = [e for e in entries if e is not None and e.key and is_available(screen, e)]
        if not shown:
            continue
        t.append(f"{name}\n", style=f"bold {T.C_NEW}")
        for entry in shown:
            t.append(f"  {entry.key:<9}", style=f"bold {T.C_STRONG}")
            t.append(f"{entry.label}\n", style=T.C_TEXT)
        t.append("\n")
    t.append("Navegação\n", style=f"bold {T.C_NEW}")
    for key, what in [
        ("F10", "abre o menu (ou Alt + letra sublinhada; clique no nome)"),
        ("← →", "entre menus, colunas do kanban e cartões da linha"),
        ("↑ ↓", "entre cartões, agentes e tarefas"),
        ("Enter", "abre o item selecionado"),
        ("Esc", "volta / fecha"),
    ]:
        t.append(f"  {key:<9}", style=f"bold {T.C_STRONG}")
        t.append(f"{what}\n", style=T.C_TEXT)
    t.rstrip()
    return t


def legend_text() -> Text:
    t = Text()
    t.append("Sessões\n", style=f"bold {T.C_NEW}")
    for dot, name, color, what in [
        ("●", "ociosa", T.C_OK, "sessão aberta, aguardando"),
        ("◐", "ocupada", T.C_BUSY, "sessão aberta, trabalhando"),
        ("○", "fechada", T.C_OFF, "nenhuma sessão aberta"),
        ("?", "desconhecida", T.C_UNK, "não foi possível consultar"),
    ]:
        t.append(f"  {dot} {name:<14}", style=color)
        t.append(f"{what}\n", style=T.C_TEXT)
    t.append("\nBorda do cartão do projeto\n", style=f"bold {T.C_NEW}")
    for name, color, what in [
        ("verde", T.HEALTH_BORDER["ok"], "todas as sessões abertas"),
        ("amarela", T.HEALTH_BORDER["warn"], "algumas sessões abertas"),
        ("cinza", T.HEALTH_BORDER["idle"], "nenhuma sessão aberta"),
        ("azul", T.HEALTH_BORDER["new"], "projeto novo no AI Memory (primeira vez)"),
    ]:
        t.append(f"  ▌ {name:<10}", style=color)
        t.append(f"{what}\n", style=T.C_TEXT)
    t.append("\nEstados das tarefas\n", style=f"bold {T.C_NEW}")
    for label, color in [(label, color) for label, color in T.TASK_STATE.values()]:
        t.append(f"  ● {label}\n", style=color)
    t.rstrip()
    return t


class BaseScreen(Screen):
    """Tela com a barra de menus: F10 e Alt+letra abrem os menus; F1 e F2 abrem a ajuda."""

    BREADCRUMB = ""
    BINDINGS = [
        Binding("f10", "open_menu('Arquivo')", "Menu", priority=True),
        Binding("alt+a", "open_menu('Arquivo')", show=False, priority=True),
        Binding("alt+p", "open_menu('Projeto')", show=False, priority=True),
        Binding("alt+g", "open_menu('Agentes')", show=False, priority=True),
        Binding("alt+s", "open_menu('Passivos')", show=False, priority=True),
        Binding("alt+t", "open_menu('Tarefas')", show=False, priority=True),
        Binding("alt+m", "open_menu('Monitor')", show=False, priority=True),
        Binding("alt+l", "open_menu('Alertas')", show=False, priority=True),
        Binding("f3", "open_alerts", "Alertas", show=False),
        Binding("alt+u", "open_menu('Ajuda')", show=False, priority=True),
        Binding("f1", "help_keys", show=False),
        Binding("f2", "help_legend", show=False),
    ]

    def menu_bar(self) -> MenuBar:
        return MenuBar(self.BREADCRUMB, id="menubar")

    def on_menu_bar_clicked(self, message: MenuBar.Clicked) -> None:
        self.action_open_menu(message.name)

    def action_open_menu(self, name: str) -> None:
        if self.app.screen is not self:
            return  # há uma janela aberta por cima (formulário, detalhe)
        left = self.query_one(MenuBar).left_of(name)

        def chosen(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            kind, value = result
            if kind == "switch":
                self.action_open_menu(value)
            else:
                self.run_menu_action(value)

        self.app.push_screen(MenuPopup(name, self, left), chosen)

    def run_menu_action(self, action: str) -> None:
        if action == "app.quit":
            self.app.exit()
            return
        handler = getattr(self, f"action_{action}", None)
        if handler is None:
            return
        outcome = handler()
        if inspect.isawaitable(outcome):
            self.run_worker(outcome)

    def action_open_alerts(self) -> None:
        """Abre a central de alertas."""
        from .alerts import AlertsScreen

        if isinstance(self, AlertsScreen) or self.app.screen is not self:
            return
        self.app.push_screen(AlertsScreen())

    def action_open_monitor(self) -> None:
        """Abre a tela Monitor (atividade de todos os projetos)."""
        from .monitor import MonitorScreen

        if isinstance(self, MonitorScreen) or self.app.screen is not self:
            return
        self.app.push_screen(MonitorScreen())

    # ---------------------------------------------------------------- ajuda

    def action_help_keys(self) -> None:
        self.app.push_screen(InfoScreen("Teclas de atalho", keys_text(self)))

    def action_help_legend(self) -> None:
        self.app.push_screen(InfoScreen("Legenda de cores", legend_text()))

    def action_about(self) -> None:
        body = Text()
        body.append("dev-orchestra\n", style=f"bold {T.C_STRONG}")
        body.append(
            "Cadastro, subida e acompanhamento de projetos com agentes de IA (Orchestrator, Developer, QA...).\n"
            "O estado operacional vive no Task Source; o conhecimento durável, na memória; o código, no Git.",
            style=T.C_TEXT,
        )
        self.app.push_screen(InfoScreen("Sobre", body))
