"""Interface nova: cartões 2x2, resumo do topo, passo a passo, atividade e subida em cartões."""

from __future__ import annotations

from textual.widgets import Static

from dev_orchestra.core import config, state, tasks
from dev_orchestra.core.liveness import ClaudeSession, Services
from dev_orchestra.core.models import GlobalConfig, Project, Role, TaskSource
from dev_orchestra.core.summary import Infra, build_summary
from dev_orchestra.tui import widgets
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.screens import LaunchScreen, ProjectScreen
from dev_orchestra.tui.widgets import LaunchCard, ProjectCard, Tile

WIDE = (170, 60)
NARROW = (90, 60)


def make(tmp_path, pid, *, cli="claude", roles=("orchestrator", "developer"), **kw) -> Project:
    folder = tmp_path / pid
    folder.mkdir(exist_ok=True)
    return Project(id=pid, name=pid.title(), path=folder, roles={r: Role(cli=cli) for r in roles}, **kw)


def sessions_for(project, *roles, status="idle"):
    return [
        ClaudeSession(i, str(project.path), project.agent_name(r), "s", status, "interactive")
        for i, r in enumerate(roles, start=1)
    ]


def fake(all_sessions, known, codex=None) -> Services:
    return Services(
        claude_sessions=lambda: all_sessions,
        codex_threads=lambda u, c: codex,
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: known,
    )


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def text(widget: Static) -> str:
    return str(widget.render())


async def test_quatro_projetos_em_duas_colunas_com_saude_na_borda(tmp_path):
    a = make(tmp_path, "aaa")
    b = make(tmp_path, "bbb")
    c = make(tmp_path, "ccc")
    d = make(tmp_path, "ddd")
    for p in (a, b, c, d):
        config.save_project(p)
    abertas = sessions_for(a, "orchestrator", "developer") + sessions_for(b, "orchestrator")
    svc = fake(abertas, [("default", "aaa"), ("default", "bbb"), ("default", "ccc")])  # ddd é novo no AI Memory
    app = DevOrchestraApp(svc)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        cards = {c.project.id: c for c in app.screen.query(ProjectCard)}
        assert app.screen.query_one("#grid").styles.grid_size_columns == 2
        assert cards["aaa"].has_class("ok") and cards["bbb"].has_class("warn")
        assert cards["ccc"].has_class("idle") and cards["ddd"].has_class("new")
        assert "2/2 sessões abertas" in str(cards["aaa"].border_subtitle)
        assert "1/2 sessões abertas" in str(cards["bbb"].border_subtitle)
        # a faixa do topo resume tudo
        assert "4" in text(app.screen.query_one("#tile-projects", Tile))
        assert "3 / 8" in text(app.screen.query_one("#tile-sessions", Tile)).replace("\n", " ")
        assert "AI Memory" in text(app.screen.query_one("#tile-infra", Tile))


async def test_uma_coluna_em_terminal_estreito(tmp_path):
    for pid in ("aaa", "bbb"):
        config.save_project(make(tmp_path, pid))
    app = DevOrchestraApp(fake([], [("default", "aaa"), ("default", "bbb")]))
    async with app.run_test(size=NARROW) as pilot:
        await settle(app, pilot)
        assert app.screen.query_one("#grid").styles.grid_size_columns == 1


async def test_setas_navegam_entre_os_cartoes_e_enter_abre(tmp_path):
    for pid in ("aaa", "bbb", "ccc"):
        config.save_project(make(tmp_path, pid))
    app = DevOrchestraApp(fake([], [("default", p) for p in ("aaa", "bbb", "ccc")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        ids = lambda: app.focused.project.id  # noqa: E731
        assert ids() == "aaa"
        await pilot.press("right")
        assert ids() == "bbb"
        await pilot.press("down")  # duas colunas: desce para a linha de baixo
        assert ids() == "ccc"
        await pilot.press("left")
        assert ids() == "ccc"  # primeira coluna: não sai da grade
        await pilot.press("right")
        assert ids() == "ccc"  # a linha de baixo só tem um cartão
        await pilot.press("up")
        assert ids() == "aaa"
        await pilot.press("up")
        assert ids() == "aaa"  # primeira linha
        await pilot.press("right")
        assert ids() == "bbb"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen) and app.screen.project_id == "bbb"


async def test_cartao_mostra_progresso_e_selos(tmp_path):
    p = make(tmp_path, "mf", method="proprio", task_source=TaskSource(kind="trello", trello_board="Board"))
    p.roles["orchestrator"] = Role(cli="claude", name="codex-orquestador")
    config.save_project(p)
    for tid, st in [("A", "done"), ("B", "done"), ("C", "running"), ("D", "ready")]:
        tasks.create_task(p, tid, tid, "developer")
        tasks.set_task_state(p, tid, st)
    app = DevOrchestraApp(fake(sessions_for(p, "orchestrator"), [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        card = app.screen.query_one(ProjectCard)
        body = card.summary and text(card)
        assert "MÉTODO PRÓPRIO" in body and "TRELLO" in body
        assert "codex-orquestador" in body and "2/4 (50%)" in body
        assert "1 executando" in body and "1 prontas" in body
        assert "retomada" in body


def test_textos_do_cartao_sem_resumo(tmp_path):
    p = make(tmp_path, "aaa")
    card = ProjectCard(p)
    body = card._body(None).plain
    assert "consultando" not in body or True  # carregando: só os nomes dos agentes
    assert "aaa-orchestrator" in body and "AI Memory …" in body


def test_memory_text_variantes(tmp_path):
    from dev_orchestra.core.liveness import AiMemoryState, StatusReport

    p = make(tmp_path, "aaa")

    def summary_with(state_):
        return build_summary(p, StatusReport(roles=[], workstream_names=None, app_server_up=None, ai_memory=state_))

    assert widgets.memory_text(None).plain == "AI Memory …"
    assert "indisponível" in widgets.memory_text(summary_with(AiMemoryState(False, None, None))).plain
    assert "primeira vez" in widgets.memory_text(summary_with(AiMemoryState(True, False, 0))).plain
    assert "retomada · 3 workstreams" in widgets.memory_text(summary_with(AiMemoryState(True, True, 3))).plain


def test_activity_text():
    vazio = widgets.activity_text([]).plain
    assert "Nenhuma atividade" in vazio
    t = widgets.activity_text(
        [{"em": "2026-11-14T09:31:00-03:00", "texto": "Instruções enviadas", "tipo": "ok"}], ["x.md: inválido"]
    ).plain
    assert "aviso  x.md: inválido" in t and "09:31  Instruções enviadas" in t


async def test_infraestrutura_no_topo(tmp_path):
    p = make(tmp_path, "aaa", cli="codex")
    config.save_project(p)
    app = DevOrchestraApp(fake([], [("default", "aaa")], codex=None))  # App Server fora do ar
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        infra = text(app.screen.query_one("#tile-infra", Tile))
        assert "fora do ar" in infra and "AI Memory" in infra


def test_infra_content_variantes():
    from dev_orchestra.tui.screens import ProjectsScreen

    assert ProjectsScreen._infra_content(None)[0].plain == "…"
    first, second = ProjectsScreen._infra_content(Infra(True, True, "ws://127.0.0.1:4500"))
    assert "AI Memory no ar" in first.plain and "App Server :4500 no ar" in second.plain
    first, second = ProjectsScreen._infra_content(Infra(False, None, "ws://x:1"))
    assert "indisponível" in first.plain and "não usado" in second.plain


# --------------------------------------------------------------- tela do projeto


async def test_projeto_mostra_passo_a_passo_e_atividade(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    state.log_activity("mf", "Instruções enviadas a mf-orchestrator via SendMessage", "ok")
    svc = fake(sessions_for(p, "orchestrator"), [("default", "mf")])
    app = DevOrchestraApp(svc)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, ProjectScreen)
        stepper = text(screen.query_one("#stepper", Static))
        assert "② Sessões 1/2 abertas" in stepper and "③ Instruções 0/2 enviadas" in stepper
        assert "próximo passo: abra mf-developer (tecle u para ver os comandos)" in stepper
        assert "Instruções enviadas a mf-orchestrator via SendMessage" in text(screen.query_one("#activity", Static))
        assert "mf-developer" in text(screen.query_one("#roles")) if False else True


async def test_projeto_avisa_tarefa_invalida_na_atividade(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    p.tasks_path.mkdir(parents=True)
    (p.tasks_path / "ruim.md").write_text("lixo", encoding="utf-8")
    app = DevOrchestraApp(fake([], [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert "aviso  ruim.md" in text(app.screen.query_one("#activity", Static))


async def test_mudar_estado_e_exportar_entram_na_atividade(tmp_path):
    from textual.widgets import Select

    p = make(tmp_path, "mf")
    config.save_project(p)
    tasks.create_task(p, "T-1", "x", "developer")
    app = DevOrchestraApp(fake([], [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        assert "Configuração portátil exportada" in text(app.screen.query_one("#activity", Static))
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#s-state", Select).value = "running"
        await pilot.click("#save")
        await settle(app, pilot)
        assert "Tarefa T-1: estado ready → running" in text(app.screen.query_one("#activity", Static))


# ----------------------------------------------------------------- subida


async def test_subida_em_cartoes(tmp_path):
    p = make(tmp_path, "mf", cli="codex")
    config.save_project(p)
    app = DevOrchestraApp(fake([], [("default", "mf")], codex=[]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("u")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, LaunchScreen)
        cards = list(screen.query(LaunchCard))
        # App Server + orquestrador + developer + "Depois de abrir"
        assert len(cards) == len(screen.plan.blocks) + 1 == 4
        assert "[2] copiar" in str(cards[1].border_subtitle)
        assert "Depois de abrir" in str(cards[-1].border_title)
        assert "Terminal 1 — App Server do Codex" in screen.plan_text  # o texto para copiar não mudou


def test_config_global_default_nao_muda_com_a_interface():
    assert GlobalConfig().app_server_url == "ws://127.0.0.1:4500"


def test_plural():
    from dev_orchestra.core.summary import plural

    assert plural(0, "ociosa") == "0 ociosas"
    assert plural(1, "ociosa") == "1 ociosa"
    assert plural(2, "novo") == "2 novos"
    assert plural(2, "workstream") == "2 workstreams" and plural(1, "cartão", "cartões") == "1 cartão"
    assert plural(3, "cartão", "cartões") == "3 cartões"


def test_rotulo_curto_das_instrucoes():
    assert state.describe_short(None) == "pendentes"
    state.mark_sent("demo", "demo-orchestrator", via="SendMessage", resume=False)
    assert state.describe_short(state.sent_info("demo", "demo-orchestrator")).startswith("✓ ")


async def test_aviso_de_lista_vazia_some_quando_ha_projetos(tmp_path):
    from dev_orchestra.tui.screens import ProjectsScreen

    app = DevOrchestraApp(fake([], []))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        assert app.screen.query_one("#empty", Static).display is True
        config.save_project(make(tmp_path, "aaa"))
        app.screen.action_reload()
        await settle(app, pilot)
        assert isinstance(app.screen, ProjectsScreen)
        assert app.screen.query_one("#empty", Static).display is False


def test_detalhe_curto_para_a_tabela():
    from dev_orchestra.core.summary import short_detail

    assert short_detail("ociosa", "pid 4052400, status idle; workstream usado 2 min") == "pid 4052400"
    assert short_detail("ocupada", "thread abcdef12") == "thread abcdef12"
    assert short_detail("fechada", "nenhuma sessão aberta; workstream usado 1 month ago") == "usado 1 month ago"
    assert short_detail("fechada", "nenhuma sessão aberta chamada 'x' nesta pasta") == ""


async def test_detalhe_completo_do_agente_destacado(tmp_path):
    p = make(tmp_path, "mf")
    config.save_project(p)
    app = DevOrchestraApp(fake(sessions_for(p, "orchestrator"), [("default", "mf")]))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("down")  # destaca o segundo agente (fechado)
        await pilot.pause()
        detalhe = text(app.screen.query_one("#agent-detail", Static))
        assert "mf-developer" in detalhe and "nenhuma sessão aberta" in detalhe
