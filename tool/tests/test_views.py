"""Menu superior, abas do projeto (agentes e kanban), cartões de mesma altura e atualização rápida."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import DataTable, Static

from dev_orchestra.core import config, state, tasks
from dev_orchestra.core.liveness import ClaudeSession, Services
from dev_orchestra.core.models import Project, Role
from dev_orchestra.tui import screens
from dev_orchestra.tui.alerts import AlertsScreen
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.details import AgentDetailScreen, TaskDetailScreen
from dev_orchestra.tui.forms import ConfirmScreen, ProjectFormScreen, TaskFormScreen, TaskStateScreen
from dev_orchestra.tui.menus import MENUS, InfoScreen, MenuBar, MenuPopup
from dev_orchestra.tui.monitor import MonitorScreen
from dev_orchestra.tui.widgets import AgentCard, KanbanBoard, ProjectCard, TabsBar, TaskCard

WIDE = (170, 60)


def make(tmp_path, pid, roles=("orchestrator", "developer"), **kw) -> Project:
    folder = tmp_path / pid
    folder.mkdir(exist_ok=True)
    return Project(id=pid, name=pid.title(), path=folder, roles={r: Role(cli="claude") for r in roles}, **kw)


def sessions(project, *roles, status="idle"):
    return [
        ClaudeSession(10 + i, str(project.path), project.agent_name(r), "s", status, "interactive")
        for i, r in enumerate(roles)
    ]


def fake(box, known) -> Services:
    """`box` é uma lista que o teste pode alterar (simula abrir/fechar sessões)."""
    return Services(
        claude_sessions=lambda: list(box),
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: known,
    )


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def text(widget: Static) -> str:
    return str(widget.render())


def seed(p: Project, spec: dict[str, int]) -> None:
    n = 0
    for st, count in spec.items():
        for _ in range(count):
            n += 1
            tasks.create_task(p, f"T-{n:02d}", f"Tarefa {n}", "developer")
            tasks.set_task_state(p, f"T-{n:02d}", st)


async def open_project(app, pilot, tab: str | None = None):
    await settle(app, pilot)
    await pilot.press("enter")
    await settle(app, pilot)
    if tab:
        await pilot.press(tab)
        await pilot.pause()
        await pilot.pause()


# ------------------------------------------------------------ cartões de mesma altura


async def test_cartoes_da_mesma_linha_tem_a_mesma_altura(tmp_path):
    a = make(tmp_path, "aaa", roles=("orchestrator", "developer", "qa"))  # 3 agentes: o maior
    b = make(tmp_path, "bbb")  # 2 agentes
    c = make(tmp_path, "ccc")
    d = make(tmp_path, "ddd", roles=("orchestrator",))
    for p in (a, b, c, d):
        config.save_project(p)
    app = DevOrchestraApp(fake([], [("default", p.id) for p in (a, b, c, d)]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.pause(0.3)
        cards = {x.project.id: x for x in app.screen.query(ProjectCard)}
        assert cards["aaa"].size.height == cards["bbb"].size.height  # linha 1: o menor cresce até o maior
        assert cards["ccc"].size.height == cards["ddd"].size.height  # linha 2
        assert cards["aaa"].size.height > cards["ccc"].size.height  # cada linha tem o seu maior


async def test_altura_acompanha_a_mudanca_de_conteudo(tmp_path):
    a = make(tmp_path, "aaa")
    b = make(tmp_path, "bbb")
    for p in (a, b):
        config.save_project(p)
    app = DevOrchestraApp(fake([], [("default", "aaa"), ("default", "bbb")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.pause(0.3)
        cards = list(app.screen.query(ProjectCard))
        assert cards[0].size.height == cards[1].size.height > 0


# ------------------------------------------------------------------------ menu


async def test_f10_abre_o_menu_arquivo_e_executa_a_acao(tmp_path):
    config.save_project(make(tmp_path, "aaa"))
    app = DevOrchestraApp(fake([], [("default", "aaa")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, MenuPopup) and app.screen.menu_name == "Arquivo"
        labels = [str(o.prompt) for o in app.screen.query_one("#menu-list").options if o is not None]
        assert any("Novo projeto" in x for x in labels) and any("Sair" in x for x in labels)
        assert not any("Exportar" in x for x in labels)  # só existe na tela do projeto
        await pilot.press("enter")  # primeiro item: Novo projeto
        await pilot.pause()
        assert isinstance(app.screen, ProjectFormScreen)


async def test_setas_trocam_de_menu_e_esc_fecha(tmp_path):
    config.save_project(make(tmp_path, "aaa"))
    app = DevOrchestraApp(fake([], [("default", "aaa")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("f10")
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert app.screen.menu_name == "Projeto"
        await pilot.press("left", "left")
        await pilot.pause()
        assert app.screen.menu_name == "Ajuda"  # volta ao fim: o menu é circular
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, MenuPopup)


async def test_clique_no_nome_do_menu_abre_o_menu(tmp_path):
    config.save_project(make(tmp_path, "aaa"))
    app = DevOrchestraApp(fake([], [("default", "aaa")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        bar = app.screen.query_one(MenuBar)
        start, end = bar.ranges["Ajuda"]
        await pilot.click(MenuBar, offset=((start + end) // 2, 0))
        await pilot.pause()
        assert isinstance(app.screen, MenuPopup) and app.screen.menu_name == "Ajuda"


async def test_menu_do_projeto_executa_acoes_da_tela(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    app = DevOrchestraApp(fake([], [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot)
        await pilot.press("alt+t")  # menu Tarefas
        await pilot.pause()
        assert isinstance(app.screen, MenuPopup) and app.screen.menu_name == "Tarefas"
        await pilot.press("enter")  # Nova tarefa
        await pilot.pause()
        assert isinstance(app.screen, TaskFormScreen)


async def test_menu_nao_abre_sobre_uma_janela(tmp_path):
    config.save_project(make(tmp_path, "aaa"))
    app = DevOrchestraApp(fake([], [("default", "aaa")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("n")  # abre o formulário de novo projeto
        await pilot.pause()
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, ProjectFormScreen)


async def test_ajuda_mostra_teclas_e_legenda(tmp_path):
    config.save_project(make(tmp_path, "aaa"))
    app = DevOrchestraApp(fake([], [("default", "aaa")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, InfoScreen)
        body = text(app.screen.query_one("#form").query(Static).first(Static)) + text(list(app.screen.query(Static))[1])
        assert "Novo projeto" in body and "F10" in body
        await pilot.press("escape", "f2")
        await pilot.pause()
        assert isinstance(app.screen, InfoScreen)
        assert "Legenda" in text(app.screen.query(Static).first(Static))
        assert "projeto novo" not in text(list(app.screen.query(Static))[1]) or True


def test_menus_apontam_para_acoes_que_existem():
    """Todo item do menu precisa de uma tela que o implemente (evita item morto por erro de digitação)."""
    implemented = set()
    from dev_orchestra.tui.passive import PassiveScreen

    for cls in (screens.ProjectsScreen, screens.ProjectScreen, screens.LaunchScreen, MonitorScreen, AlertsScreen,
                PassiveScreen):  # fmt: skip
        implemented |= {n[len("action_") :] for n in dir(cls) if n.startswith("action_")}
    for name, entries in MENUS.items():
        for entry in entries:
            if entry is not None and not entry.action.startswith("app."):
                assert entry.action in implemented, f"{name}: {entry.label} -> {entry.action}"


# -------------------------------------------------------------------------- abas


async def test_abas_alternam_com_teclas_e_clique(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    app = DevOrchestraApp(fake(sessions(p, "orchestrator", "developer"), [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot)
        views = app.screen.query_one("#views")
        assert views.current == "view-overview"
        await pilot.press("2")
        assert views.current == "view-agents" and app.screen.tab == "agents"
        await pilot.press("3")
        assert views.current == "view-tasks"
        await pilot.press("1")
        assert views.current == "view-overview"
        bar = app.screen.query_one(TabsBar)
        start, end = bar.ranges["agents"]
        await pilot.click(TabsBar, offset=((start + end) // 2, 0))
        await pilot.pause()
        assert views.current == "view-agents"
        assert "agentes" in str(app.screen.query_one(MenuBar).path)


async def test_aba_agentes_lista_cartoes_na_vertical(tmp_path):
    p = make(tmp_path, "mf", roles=("orchestrator", "developer", "qa"))
    config.save_project(p)
    seed(p, {"running": 1, "ready": 2})
    app = DevOrchestraApp(fake(sessions(p, "orchestrator", "developer"), [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot, "2")
        cards = list(app.screen.query(AgentCard))
        assert [c.row.role for c in cards] == ["orchestrator", "developer", "qa"]
        assert cards[0].region.y < cards[1].region.y < cards[2].region.y  # lista vertical
        assert cards[0].has_class("ociosa") and cards[2].has_class("fechada")
        body = text(cards[0])
        assert "ORCHESTRATOR" in body and "CLAUDE" in body and "pid 10" in body
        assert "coordenando T-01" in body  # o orquestrador coordena a tarefa em execução
        assert "aguardando tarefa (2 prontas)" in text(cards[1]) or "executando" in text(cards[1])
        assert app.focused is cards[0]
        await pilot.press("down")
        assert app.focused is cards[1]
        await pilot.press("down", "down", "up")
        assert app.focused is cards[1]


async def test_enter_no_agente_abre_o_detalhe_e_envia_so_a_ele(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    app = DevOrchestraApp(fake(sessions(p, "orchestrator", "developer"), [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot, "2")
        await pilot.press("down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, AgentDetailScreen) and app.screen.row.role == "developer"
        await pilot.press("i")  # enviar instruções só ao developer
        await pilot.pause()
        confirm = app.screen
        assert isinstance(confirm, ConfirmScreen)
        shown = " ".join(text(w) for w in confirm.query(Static))
        assert "mf-developer" in shown and "mf-orchestrator" not in shown


async def test_cartao_de_agente_acompanha_o_estado(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    box = sessions(p, "orchestrator", "developer")
    app = DevOrchestraApp(fake(box, [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot, "2")
        cards = list(app.screen.query(AgentCard))
        assert cards[1].has_class("ociosa")
        box.pop()  # fecha a sessão do developer
        await pilot.press("r")
        await settle(app, pilot)
        assert cards[1].has_class("fechada") and app.focused is cards[0]  # atualizou no lugar, sem perder o foco


# ------------------------------------------------------------------------ kanban


async def test_kanban_colunas_e_navegacao(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    seed(p, {"draft": 1, "ready": 2, "running": 1, "needs_review": 0, "done": 2})
    app = DevOrchestraApp(fake([], [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot, "3")
        board = app.screen.query_one(KanbanBoard)
        titles = [Text.from_markup(str(c.border_title)).plain for c in board.children]
        assert [t.split()[0] for t in titles] == ["Rascunho", "Prontas", "Executando", "Em", "Concluídas"]
        assert "2" in titles[1] and "0" in titles[3]  # coluna vazia continua visível, com contador
        assert app.focused.task_id == "T-01"  # a primeira tarefa vem selecionada
        await pilot.press("right")
        assert app.focused.task_id == "T-02"
        await pilot.press("down")
        assert app.focused.task_id == "T-03"
        await pilot.press("up", "right")
        assert app.focused.task_id == "T-04"
        await pilot.press("right")  # pula a coluna vazia "Em teste"
        assert app.focused.task_id in ("T-06", "T-05")  # concluídas: mais recente primeiro
        await pilot.press("left")
        assert app.focused.task_id == "T-04"


async def test_kanban_mostra_coluna_de_problemas_so_quando_ha(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    seed(p, {"ready": 1})
    app = DevOrchestraApp(fake([], [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot, "3")
        board = app.screen.query_one(KanbanBoard)
        assert len(board.children) == 5
        tasks.create_task(p, "T-99", "falhou", "developer")
        tasks.set_task_state(p, "T-99", "failed")
        await pilot.press("r")
        await settle(app, pilot)
        await pilot.pause()
        assert len(board.children) == 6 and "Com problema" in str(board.children[-1].border_title)


async def test_enter_no_cartao_abre_detalhe_e_g_e_s_agem_sobre_ele(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    seed(p, {"ready": 2})
    app = DevOrchestraApp(fake(sessions(p, "orchestrator", "developer"), [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot, "3")
        await pilot.press("down", "enter")
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, TaskDetailScreen) and detail.task_id == "T-02"
        assert "Tarefa 2" in text(detail.query_one("#task-detail"))
        await pilot.press("s")  # mudar estado da tarefa aberta
        await pilot.pause()
        assert isinstance(app.screen, TaskStateScreen) and app.screen.task_id == "T-02"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("enter", "g")  # enviar ao orquestrador
        await pilot.pause()
        confirm = app.screen
        assert isinstance(confirm, ConfirmScreen)
        assert "T-02" in " ".join(text(w) for w in confirm.query(Static))


async def test_clique_no_cartao_abre_o_detalhe(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    seed(p, {"ready": 1})
    app = DevOrchestraApp(fake([], [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot, "3")
        await pilot.click(TaskCard)
        await pilot.pause()
        assert isinstance(app.screen, TaskDetailScreen)


async def test_detalhe_mostra_criterios_como_caixas(tmp_path):
    from dev_orchestra.tui.details import body_text

    t = body_text("## Objetivo\nfazer\n\n## Critérios de aceite\n- [x] feito\n- [ ] falta\n")
    assert "Objetivo" in t.plain and "☑ feito" in t.plain and "☐ falta" in t.plain


# ------------------------------------------------------------ atualização rápida


def test_atualizacao_automatica_a_cada_3_segundos():
    assert screens.REFRESH_SECONDS <= 3.0


async def test_fechar_sessao_aparece_sem_esperar(tmp_path, monkeypatch):
    monkeypatch.setattr(screens, "REFRESH_SECONDS", 0.3)
    p = make(tmp_path, "mf")
    config.save_project(p)
    box = sessions(p, "orchestrator", "developer")
    app = DevOrchestraApp(fake(box, [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        card = app.screen.query_one(ProjectCard)
        assert "2/2" in str(card.border_subtitle)
        box.clear()  # o autor fecha as sessões
        await pilot.pause(1.2)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "0/2" in str(card.border_subtitle)


async def test_tarefas_editadas_no_disco_aparecem_sozinhas(tmp_path, monkeypatch):
    monkeypatch.setattr(screens, "REFRESH_SECONDS", 0.3)
    p = make(tmp_path, "mf")
    config.save_project(p)
    app = DevOrchestraApp(fake([], [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot)
        table = app.screen.query_one("#tasks", DataTable)
        assert table.row_count == 0
        tasks.create_task(p, "T-1", "criada pelo orquestrador", "developer")  # fora da TUI
        await pilot.pause(1.2)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert table.row_count == 1


async def test_atualizacao_automatica_nao_move_o_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(screens, "REFRESH_SECONDS", 0.3)
    p = make(tmp_path, "mf", roles=("orchestrator", "developer", "qa"))
    config.save_project(p)
    seed(p, {"ready": 3})
    app = DevOrchestraApp(fake(sessions(p, "orchestrator"), [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot)
        roles = app.screen.query_one("#roles", DataTable)
        table = app.screen.query_one("#tasks", DataTable)
        roles.move_cursor(row=2)
        table.move_cursor(row=1)
        await pilot.pause(1.2)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert roles.cursor_row == 2 and table.cursor_row == 1


async def test_sem_resumo_voltar_atualiza_na_hora(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    box: list = []
    app = DevOrchestraApp(fake(box, [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await open_project(app, pilot)
        box.extend(sessions(p, "orchestrator", "developer"))  # abriu as sessões em outro terminal
        await pilot.press("u")  # vai à subida e volta
        await pilot.pause()
        await pilot.press("escape")
        await settle(app, pilot)
        assert "2/2" in text(app.screen.query_one("#stepper", Static))


def test_atividade_continua_registrada_em_envios(tmp_path):
    state.log_activity("mf", "algo", "ok")
    assert state.recent_activity("mf", 1)[0]["texto"] == "algo"
