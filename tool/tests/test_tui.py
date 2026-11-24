from __future__ import annotations

from textual.widgets import DataTable, Input, Select, Static

from dev_orchestra.core import config, paths, tasks
from dev_orchestra.core.liveness import ClaudeSession, CodexThread, Services, Workstream
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.forms import ConfirmScreen, ProjectFormScreen, TaskFormScreen, TaskStateScreen
from dev_orchestra.tui.screens import LaunchScreen, ProjectScreen, ProjectsScreen
from dev_orchestra.tui.widgets import ProjectCard

SIZE = (160, 70)


def servicos(project_dir=None) -> Services:
    sessions = (
        [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "busy", "interactive")] if project_dir else []
    )
    return Services(
        claude_sessions=lambda: sessions,
        codex_threads=lambda url, cwd: [CodexThread("abcdef12-1", "demo-developer", cwd, "idle")],
        workstreams=lambda name, cwd: [Workstream("demo-developer", "ontem", "codex")],
        ai_memory_projects=lambda: [("default", "demo")],
    )


def texto(widget: Static) -> str:
    return str(widget.render())


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_lista_vazia():
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ProjectsScreen)
        assert "Nenhum projeto" in texto(app.screen.query_one("#empty", Static))
        assert list(app.screen.query(ProjectCard)) == []


async def test_lista_mostra_projeto_cadastrado(project):
    config.save_project(project)
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        cards = list(app.screen.query(ProjectCard))
        assert [c.project.id for c in cards] == ["demo"]
        assert app.focused is cards[0]  # o primeiro cartão já nasce em foco (Enter abre)


async def test_cadastrar_projeto_pelo_formulario(project_dir):
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ProjectFormScreen)
        form.query_one("#f-id", Input).value = "novo"
        form.query_one("#f-name", Input).value = "Projeto novo"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-orchestrator-cli", Select).value = "claude"
        form.query_one("#r-orchestrator-model", Input).value = "sonnet"
        form.query_one("#r-developer-cli", Select).value = "codex"
        form.query_one("#r-developer-sub", Input).value = "codex-pessoal"
        await form.add_specialist("social-media")
        form.query_one("#r-social-media-model", Input).value = "haiku"
        await pilot.click("#save")
        await pilot.pause()
        from dev_orchestra.tui.delegator_setup import DelegatorSetupScreen

        assert isinstance(app.screen, DelegatorSetupScreen)  # padrão: delegador ligado; a preparação abre sozinha
        app.pop_screen()
        await pilot.pause()
        assert isinstance(app.screen, ProjectsScreen)
        assert len(list(app.screen.query(ProjectCard))) == 1

    saved = config.load_project("novo")
    assert saved.delegator is True
    assert saved.name == "Projeto novo"
    assert saved.roles["orchestrator"].default_model == "sonnet"
    assert saved.roles["developer"].cli == "codex" and saved.roles["developer"].subscription == "codex-pessoal"
    assert saved.roles["social-media"].default_model == "haiku"
    assert "qa" not in saved.roles
    assert saved.tasks_path.is_dir()


async def test_formulario_valida_campos(project_dir):
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#f-id", Input).value = "Id Inválido"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-orchestrator-cli", Select).value = "claude"
        await pilot.click("#save")
        await pilot.pause()
        assert isinstance(app.screen, ProjectFormScreen)  # continua aberto
        assert "id do projeto" in texto(app.screen.query_one("#f-error", Static))

        await pilot.pause(0.3)  # o Button ignora um segundo clique em menos de 0,2 s
        form.query_one("#f-id", Input).value = "ok"
        form.query_one("#f-path", Input).value = str(project_dir / "nao-existe")
        await pilot.click("#save")
        await pilot.pause()
        assert "não existe" in texto(app.screen.query_one("#f-error", Static))

        await pilot.pause(0.3)
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-orchestrator-cli", Select).value = Select.NULL
        await pilot.click("#save")
        await pilot.pause()
        assert "ao menos um papel" in texto(app.screen.query_one("#f-error", Static))
    assert not paths.project_file("ok").exists()


async def test_cancelar_formulario(project_dir):
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.click("#cancel")
        await pilot.pause()
        assert isinstance(app.screen, ProjectsScreen)


async def test_abrir_projeto_mostra_status_e_tarefas(project, project_dir):
    config.save_project(project)
    tasks.create_task(project, "T-1", "Primeira", "developer")
    app = DevOrchestraApp(servicos(project_dir))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        assert isinstance(app.screen, ProjectScreen)
        roles = app.screen.query_one("#roles", DataTable)
        assert "ocupada" in roles.get_row("orchestrator")[3].plain
        assert "ociosa" in roles.get_row("developer")[3].plain
        assert roles.get_row("developer")[5].plain == "thread abcdef12"  # a tabela mostra só o resumo
        developer = next(a for a in app.screen.summary.agents if a.role == "developer")
        assert "workstream usado ontem" in developer.detail  # o detalhe completo aparece abaixo da tabela
        tabela = app.screen.query_one("#tasks", DataTable)
        assert tabela.row_count == 1 and "pronta" in tabela.get_row("T-1")[1].plain
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProjectsScreen)


async def test_status_quando_fontes_indisponiveis(project):
    config.save_project(project)
    vazio = Services(
        claude_sessions=lambda: None,
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: None,
        ai_memory_projects=lambda: None,
    )
    app = DevOrchestraApp(vazio)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        roles = app.screen.query_one("#roles", DataTable)
        assert all("desconhecida" in roles.get_row(r)[3].plain for r in ("orchestrator", "developer"))


async def test_tela_de_subida_mostra_comandos(project, project_dir):
    config.save_project(project)
    app = DevOrchestraApp(servicos(project_dir))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("u")
        await pilot.pause()
        assert isinstance(app.screen, LaunchScreen)
        plano = app.screen.plan_text
        assert "ai-memory run --project demo --new demo-orchestrator claude --name demo-orchestrator" in plano
        assert "--workstream demo-developer codex --remote ws://127.0.0.1:4500" in plano  # developer já tem workstream
        assert "reutilize" in plano  # App Server respondeu na simulação
        assert "[b]" not in plano and "Terminal 1" in plano
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)


async def test_nova_tarefa_e_mudanca_de_estado(project):
    config.save_project(project)
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, TaskFormScreen)
        form.query_one("#t-id", Input).value = "T-9"
        form.query_one("#t-title", Input).value = "Tarefa nova"
        form.query_one("#t-objective", Input).value = "Fazer algo"
        await pilot.click("#save")
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)
        tabela = app.screen.query_one("#tasks", DataTable)
        assert tabela.row_count == 1 and tabela.get_row("T-9")[2] == "orchestrator"

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, TaskStateScreen)
        app.screen.query_one("#s-state", Select).value = "running"
        await pilot.click("#save")
        await pilot.pause()
        assert "executando" in app.screen.query_one("#tasks", DataTable).get_row("T-9")[1].plain
    assert tasks.read_task(project, "T-9").state == "running"


async def test_tarefa_duplicada_mostra_erro(project):
    config.save_project(project)
    tasks.create_task(project, "T-1", "x", "developer")
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        app.screen.query_one("#t-id", Input).value = "T-1"
        app.screen.query_one("#t-title", Input).value = "dup"
        await pilot.click("#save")
        await pilot.pause()
        assert isinstance(app.screen, TaskFormScreen)
        assert "já existe" in texto(app.screen.query_one("#t-error", Static))


async def test_estado_sem_tarefa_selecionada_nao_quebra(project):
    config.save_project(project)
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)


async def test_exportar_configuracao(project, project_dir):
    config.save_project(project)
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
    assert (project_dir / ".dev-orchestra" / "projeto.toml").is_file()


async def test_remover_cadastro_com_confirmacao(project, project_dir):
    config.save_project(project)
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#no")
        await pilot.pause()
        assert paths.project_file("demo").exists()
        await pilot.press("d")
        await pilot.pause()
        await pilot.click("#yes")
        await pilot.pause()
        assert not paths.project_file("demo").exists()
        assert list(app.screen.query(ProjectCard)) == []
    assert project_dir.is_dir()


async def test_editar_projeto(project, project_dir):
    config.save_project(project)
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("p")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ProjectFormScreen)
        assert form.query_one("#f-id", Input).disabled
        assert form.query_one("#r-developer-model", Input).value == "gpt-6-luna"
        form.query_one("#r-developer-model", Input).value = "gpt-6-sol"
        form.query_one("#r-qa-cli", Select).value = "claude"
        await pilot.click("#save")
        await settle(app, pilot)
        assert isinstance(app.screen, ProjectScreen)
    editado = config.load_project("demo")
    assert editado.roles["developer"].default_model == "gpt-6-sol"
    assert "qa" in editado.roles


# ------------------------------------------------------------ cópia na tela de subida


async def abrir_subida(app, pilot):
    await pilot.pause()
    await pilot.press("enter")
    await settle(app, pilot)
    await pilot.press("u")
    await pilot.pause()
    assert isinstance(app.screen, LaunchScreen)
    return app.screen


async def test_c_copia_o_plano_inteiro_quando_nao_ha_selecao(project, project_dir, monkeypatch):
    config.save_project(project)
    copiado = []
    monkeypatch.setattr("dev_orchestra.tui.screens.system_copy", lambda text: copiado.append(text) or "wl-copy")
    app = DevOrchestraApp(servicos(project_dir))
    async with app.run_test(size=SIZE) as pilot:
        tela = await abrir_subida(app, pilot)
        await pilot.press("c")
        await pilot.pause()
    assert copiado == [tela.plan_text]


async def test_c_copia_so_a_selecao_quando_existe(project, project_dir, monkeypatch):
    config.save_project(project)
    copiado = []
    monkeypatch.setattr("dev_orchestra.tui.screens.system_copy", lambda text: copiado.append(text) or "wl-copy")
    monkeypatch.setattr(LaunchScreen, "get_selected_text", lambda self: "só este trecho")
    app = DevOrchestraApp(servicos(project_dir))
    async with app.run_test(size=SIZE) as pilot:
        await abrir_subida(app, pilot)
        await pilot.press("c")
        await pilot.pause()
    assert copiado == ["só este trecho"]


async def test_sem_ferramenta_de_copia_salva_arquivo(project, project_dir, monkeypatch, isolated_home):
    config.save_project(project)
    monkeypatch.setattr("dev_orchestra.tui.screens.system_copy", lambda text: None)
    app = DevOrchestraApp(servicos(project_dir))
    async with app.run_test(size=SIZE) as pilot:
        tela = await abrir_subida(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, LaunchScreen)  # não quebra
    arquivo = isolated_home / "ultimo-plano.txt"
    assert arquivo.read_text(encoding="utf-8") == tela.plan_text


async def test_numero_copia_so_os_comandos_do_terminal(project, project_dir, monkeypatch):
    config.save_project(project)
    copiado = []
    monkeypatch.setattr("dev_orchestra.tui.screens.system_copy", lambda text: copiado.append(text) or "wl-copy")
    app = DevOrchestraApp(servicos(project_dir))
    async with app.run_test(size=SIZE) as pilot:
        tela = await abrir_subida(app, pilot)
        # com o App Server no ar o terminal 1 não tem comando; o 2 é o orquestrador
        assert tela.plan.blocks[0].title == "App Server do Codex" and not tela.plan.blocks[0].commands
        await pilot.press("1")
        await pilot.pause()
        assert copiado == []
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
    assert len(copiado) == 1
    linhas = copiado[0].splitlines()
    assert linhas[0].startswith("cd ") and linhas[1].startswith("ai-memory run ")
    assert "$ " not in copiado[0] and "Terminal" not in copiado[0]


async def test_x_sai_e_devolve_o_plano_para_imprimir(project, project_dir):
    config.save_project(project)
    app = DevOrchestraApp(servicos(project_dir))
    async with app.run_test(size=SIZE) as pilot:
        await abrir_subida(app, pilot)
        await pilot.press("x")
        await pilot.pause()
    assert isinstance(app.return_value, str)
    assert "Terminal 2 — Orchestrator (claude)" in app.return_value
    assert "$ " not in app.return_value
