"""Aplicativo Textual do dev-orchestra."""

from __future__ import annotations

from textual.app import App

from ..core.liveness import Services
from . import theme as T
from .alert_center import AlertCenter
from .screens import ProjectsScreen

# O CSS usa os mesmos tokens da paleta (theme.py), para todas as telas terem o mesmo visual.
_CSS = """
Screen { background: @bg; }
MenuBar { height: 1; width: 100%; background: #161b22; dock: top; }
Footer { background: #161b22; }
Footer FooterKey { background: #161b22; }

/* ---------------------------------------------------------------- projetos */
.tiles { height: 6; margin: 1 1 0 1; }
Tile {
    width: 1fr;
    height: 100%;
    border: round @border;
    padding: 0 2;
    margin-right: 1;
    background: @panel;
}
Tile.last { margin-right: 0; }
#empty { margin: 2 2; color: @muted; }
#grid { grid-size: 2; grid-gutter: 1 2; height: auto; padding: 1 1 0 1; }

ProjectCard { height: auto; border: round @border; padding: 0 1; background: @panel; }
ProjectCard.ok { border: round @border_ok; }
ProjectCard.warn { border: round @border_warn; }
ProjectCard.idle { border: round @border_idle; }
ProjectCard.new { border: round @border_new; }
ProjectCard.alert-warn { border: round #b8962e; }
ProjectCard.alert-crit { border: round #b8482e; }
ProjectCard:focus { border: heavy #58a6ff; background: @panel_focus; }

/* --------------------------------------------------------- agentes passivos */
#passive-head { height: auto; margin: 1 2 0 2; }
#pgrid { grid-size: 3; grid-gutter: 1 2; height: auto; padding: 1 1 0 1; }
PassiveCard { height: auto; border: round @border; padding: 0 1; background: @panel; }
PassiveCard.p-busy { border: round @border_warn; }
PassiveCard.p-queue { border: round @border_new; }
PassiveCard.p-free { border: round @border_ok; }
PassiveCard.p-failed { border: round #b8482e; }
PassiveCard.p-unknown { border: round @border_idle; }
PassiveCard:focus { border: heavy #58a6ff; background: @panel_focus; }
#pv-cols { height: 1fr; margin: 1 1 0 1; }
#pv-left { width: 3fr; height: 100%; }
#pv-right { width: 2fr; height: 100%; margin-left: 1; }
#pv-table { height: 12; border: round #3f7fbf; }
#pv-detail { height: 1fr; border: round #3f9b66; background: @panel; padding: 0 1; }
#pv-config { height: auto; border: round @border; background: @panel; padding: 0 1; }
#pv-events { height: 1fr; border: round #3f9b66; background: @panel; padding: 0 1; }
#pv-detail, #pv-config, #pv-events { border-title-color: @strong; border-title-style: bold; }
#pt { width: 110; height: 80%; border: round #3f7fbf; padding: 1 2; background: @panel; }
#pt-text { height: 8; }
#pt-origin { width: 30; }
#pt-result-box { height: 1fr; margin-top: 1; border: round @border; padding: 0 1; }
#pa-profile { height: 14; }
#pf #pa-model { width: 1fr; }
#pf #pa-timeout, #pf #pa-reset { width: 12; }

/* ----------------------------------------------------------------- projeto */
TabsBar { height: 1; margin: 1 2 0 2; }
#views { height: 1fr; }
#view-overview { height: 100%; }
#view-agents { height: 100%; padding: 1 0 0 0; }
#view-tasks { height: 100%; }

AgentCard { height: auto; margin: 0 1 1 1; border: round @border; padding: 0 2; background: @panel; }
AgentCard.ociosa { border: round @border_ok; }
AgentCard.ocupada { border: round @border_warn; }
AgentCard.fechada { border: round #7a3b3b; }
AgentCard.desconhecida { border: round @border_idle; }
AgentCard:focus { border: heavy #58a6ff; background: @panel_focus; }

KanbanBoard { height: 1fr; margin: 1 1 0 1; }
KanbanColumn { width: 1fr; height: 100%; border: round @border; background: #11171e; margin-right: 1; padding: 0 1; }
KanbanColumn.last { margin-right: 0; }
TaskCard { height: auto; border: round @border; background: @panel; padding: 0 1; margin-bottom: 1; }
TaskCard:focus { border: heavy #58a6ff; background: @panel_focus; }

.panel { border: round @border; background: @panel; padding: 0 1; margin: 0 1; }
#banner { height: auto; margin: 1 1 0 1; border: round @border_ok; padding: 0 2; }
#stepper { height: 4; margin: 1 1 0 1; padding: 0 1; }
#cols { height: 1fr; }
#left { width: 11fr; height: 100%; }
#right { width: 9fr; height: 100%; }
#agent-detail { height: auto; margin: 0 1; }
#activity { height: auto; max-height: 9; margin: 0 1 0 1; }
DataTable { background: @panel; height: 1fr; overflow-x: hidden; scrollbar-size: 0 0; }
DataTable > .datatable--header { background: #1b232d; color: @muted; text-style: bold; }
DataTable > .datatable--cursor { background: #1f3a5a; }
#banner, #activity, DataTable { border-title-color: @strong; border-title-style: bold; }

/* ------------------------------------------------------------------ alertas */
#al-strip { height: 4; margin: 1 1 0 1; padding: 0 1; border: round @border; background: @panel; }
#al-table {
    height: 1fr;
    margin: 0 1;
    border: round #b8482e;
    background: @panel;
    overflow-x: hidden;
    scrollbar-size: 0 0;
}
#al-table > .datatable--header { background: #1b232d; color: @muted; text-style: bold; }
#al-table > .datatable--cursor { background: #3a2230; }
#al-detail { height: 9; margin: 0 1; border: round #b8482e; background: @panel; padding: 0 1; }
#al-rules { height: 8; margin: 0 1; border: round @border; background: @panel; padding: 0 1; }

/* ------------------------------------------------------------------ monitor */
#mon-stats { height: 4; margin: 1 1 0 1; padding: 0 1; border: round @border; background: @panel; }
#mon-cols { height: 1fr; margin: 0 1; }
#mon-projects { width: 36; height: 100%; border: round @border; background: @panel; padding: 0 1; margin-right: 1; }
#mon-right { width: 1fr; height: 100%; }
#timeline { height: 1fr; border: round #3f9b66; background: @panel; overflow-x: hidden; scrollbar-size: 0 0; }
#mon-detail { height: 10; border: round #3f7fbf; background: @panel; padding: 0 1; }

/* ------------------------------------------------------------------- subida */
LaunchCard { height: auto; min-height: 9; border: round @border; padding: 0 1; background: @panel; }

/* ----------------------------------------------------------------- formulários */
#pf {
    width: 96%;
    height: 94%;
    border: round #3f7fbf;
    padding: 0 2;
    background: @panel;
}
#pf-small { width: 96; height: auto; border: round #3f7fbf; padding: 1 2; background: @panel; }
#pf-small Input { height: 1; border: none; padding: 0 1; background: #1b2733; }
#pf-small Input:focus { background: #24415f; border: none; }
#pf-cols { height: 1fr; }
#pf-left { width: 2fr; height: 100%; }
#pf-summary { width: 1fr; height: 100%; margin-left: 1; border: round @border; padding: 0 1; background: #11171e; }
.card { height: auto; border: round @border; padding: 0 2; margin-bottom: 1; background: #11171e; }
.card-title { text-style: bold; color: @strong; }
.role-card { height: auto; border-left: thick #2b3440; padding: 0 1; margin-bottom: 1; }
.role-head { height: 1; }
.role-row { height: auto; }
.role-detail { height: auto; }
.mini { width: 11; padding-top: 0; color: @muted; }
.role-row Select { width: 22; }
.role-row Input { width: 1fr; }
.session-info { padding-top: 0; margin: 0 1; width: 1fr; }
ChoiceChips { height: 1; padding-top: 0; width: 1fr; }
/* campos compactos: uma linha cada, para o formulário caber sem rolar tanto */
#pf Input { height: 1; border: none; padding: 0 1; background: #1b2733; }
#pf Input:focus { background: #24415f; border: none; }
#pf Input:disabled { background: #151c24; color: @muted; }
#pf Select { height: 1; }
#pf SelectCurrent { border: none; height: 1; padding: 0 1; background: #1b2733; }
#pf Select:focus > SelectCurrent { background: #24415f; border: none; }
#pf Checkbox { height: 1; border: none; padding: 0; background: transparent; }
#pf .role-row Button { height: 1; border: none; min-width: 28; margin: 0; }
#pf .role-row { margin-bottom: 0; }
#pf .role-row Select { width: 30; }
#pf .role-row Select#r-orchestrator-cli, #pf .role-row Select.cli { width: 22; }
#pf .role-row Button.pick { width: 5; min-width: 5; margin-left: 1; margin-right: 1; }
#pg-desc { height: 8; }
#pg-draft { height: 1fr; }
#pf #pg-model { width: 1fr; }
#pg-status { height: auto; }
ChoiceChips:focus { background: #18222e; }
#f-folder { height: auto; color: @muted; }
#picker { width: 110; height: 80%; border: round #3f7fbf; padding: 1 2; background: @panel; }
#picker .title { text-style: bold; color: @strong; }
#picker OptionList { height: 1fr; background: #11171e; border: none; }
#fp-input { margin-bottom: 1; }
#fp-info { margin-bottom: 1; }
ModalScreen { align: center middle; background: #0f1419 70%; }  /* a tela de baixo continua visível, escurecida */
MenuPopup { align: left top; background: transparent; }
MenuPopup OptionList, MenuPopup OptionList:focus {
    border: none;
    padding: 0;
    background: #161b22;
    background-tint: transparent;
}
MenuPopup OptionList > .option-list--option-highlighted { background: #1f3a5a; color: @strong; text-style: bold; }
#form {
    width: 100;
    height: auto;
    max-height: 92%;
    border: round #3f7fbf;
    padding: 1 2;
    background: @panel;
}
.title { text-style: bold; color: @strong; margin-bottom: 1; }
.error { color: @off; }
.buttons { height: auto; margin-top: 1; }
.buttons Button { margin-right: 2; }
.role-row { height: auto; }
.role-name { width: 14; padding-top: 1; color: @strong; text-style: bold; }
.role-row Select { width: 16; }
.role-row Input { width: 1fr; }
"""

_TOKENS = {
    "@bg": T.C_BG,
    "@panel_focus": T.C_PANEL_FOCUS,
    "@panel": T.C_PANEL,
    "@border_ok": T.HEALTH_BORDER["ok"],
    "@border_warn": T.HEALTH_BORDER["warn"],
    "@border_idle": T.HEALTH_BORDER["idle"],
    "@border_new": T.HEALTH_BORDER["new"],
    "@border": T.C_BORDER,
    "@strong": T.C_STRONG,
    "@muted": T.C_MUTED,
    "@off": T.C_OFF,
}


def _build_css() -> str:
    css = _CSS
    for token, value in _TOKENS.items():  # os tokens mais longos primeiro (ex.: @panel_focus antes de @panel)
        css = css.replace(token, value)
    return css


class DevOrchestraApp(App):
    TITLE = "dev-orchestra"
    SUB_TITLE = "cadastro, subida e retomada de projetos"
    ENABLE_COMMAND_PALETTE = False

    CSS = _build_css()

    def __init__(self, services: Services | None = None, notifier=None) -> None:
        super().__init__()
        self.services = services
        self.notifier = notifier  # None = o notificador real (som e notify-send)

    def on_mount(self) -> None:
        self.push_screen(ProjectsScreen())
        self.alerts = AlertCenter(self)  # alertas: o que parou o trabalho dos agentes e precisa da pessoa
        self.alerts.start()


def run_app(services: Services | None = None) -> None:
    result = DevOrchestraApp(services).run()
    if isinstance(result, str) and result:  # `x` na tela de subida: imprime no terminal normal
        print(result)
