"""Importação de projeto existente (nomes e agentes preservados) e importação dos cartões do Trello."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from textual.widgets import Input, Select

from dev_orchestra import cli
from dev_orchestra.core import config, contract, dispatch, importer, liveness, state, trello
from dev_orchestra.core.commands import LaunchContext, build_launch_plan, local_context, opening_prompt
from dev_orchestra.core.liveness import ClaudeSession, Services, collect_status
from dev_orchestra.core.models import GlobalConfig, Project, Role, TaskSource
from dev_orchestra.core.tasks import parse_task, render_task
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.forms import ConfirmScreen, ImportFormScreen, ImportPathScreen, ProjectFormScreen
from dev_orchestra.tui.screens import ProjectScreen, ProjectsScreen

SIZE = (170, 80)

WS_TEXT = """Managed workstreams for default/app_existente:
* codex-orquestador  1 day ago  [claude-code, codex]
    id: 01a0daae-04cd-7af1-9a8c-0fa2f714b365
  codex-dev  1 day ago  [claude-code, codex]
    id: 01a0dab2-8983-72d1-900f-786cab0c7526
  codex-planner  1 month ago  [codex]
    id: 01a0dab3-e5e6-7970-ae3e-e684d88d2847
  codex-2  1 month ago  [codex]
    id: 01a0daa5-b50f-78b3-a360-369ea81e5b2c
  meuapp-developer  1 month ago  [codex]
    id: 01a0da4f-433c-7de3-937d-617d6306965d
  codex-3  1 month ago  [no linked harnesses]
    id: 01a0daa6-12cf-7b00-8938-33662c6e75ae
  meuapp-orchestrator  1 month ago  [codex]
    id: 01a0da4d-0f06-7c73-b9a1-49a1ddf2effe
  default  1 month ago  [codex]
    id: 01a0da4d-0f06-7c73-b9a1-49a1ddf2eff0
"""

PROJECTS_TEXT = """WORKSPACE  PROJECT          PROJECT ID                             PAGES  LAST UPDATED
default    app_existente  01a0cadf-5402-7a63-aa6d-9ebe4b68e20f      21  2026-11-14T11:47:57.872482Z
"""


def fake_run(args, cwd=None, *rest):
    if args[:2] == ["ai-memory", "list-projects"]:
        return 0, PROJECTS_TEXT
    if args[:2] == ["ai-memory", "workstreams"]:
        return 0, WS_TEXT
    return 127, ""


@pytest.fixture
def existing(tmp_path) -> Path:
    """Projeto existente no formato do app_existente: AI Memory, agentes Claude e método próprios."""
    root = tmp_path / "app_existente"
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / "docs" / "development").mkdir(parents=True)
    (root / ".ai-memory.toml").write_text('workspace = "default"\nproject = "app_existente"\n', encoding="utf-8")
    (root / "AGENTS.md").write_text("# Repository agent instructions\n", encoding="utf-8")
    for name in ("meuapp-developer", "meuapp-planner"):
        (root / ".claude" / "agents" / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: Agente {name}\ntools: Read\n---\n\nTexto.\n", encoding="utf-8"
        )
    (root / "docs" / "development" / "dev-orchestra.md").write_text(
        "# Fluxo\n\nconfirme que o Trello lê o board **MediaFlow - Desenvolvimento** pelo nome.\n", encoding="utf-8"
    )
    return root


def scan_fake(path):
    return importer.scan(path, run=fake_run)


# ---------------------------------------------------------------- modelos


def test_age_seconds():
    assert importer.age_seconds("just now") == 0
    assert importer.age_seconds("2 minutes ago") == 120
    assert importer.age_seconds("1 day ago") == 86400
    assert importer.age_seconds("an hour ago") == 3600
    assert importer.age_seconds("1 month ago") > importer.age_seconds("3 weeks ago")
    assert importer.age_seconds("sem sentido") == float("inf")


def test_nome_explicito_tem_prioridade_sobre_o_prefixo(tmp_path):
    p = Project(
        id="mf",
        name="x",
        path=tmp_path,
        prefix="pf",
        roles={"orchestrator": Role(cli="claude", name="codex-orquestador"), "developer": Role(cli="claude")},
    )
    assert p.agent_name("orchestrator") == "codex-orquestador"
    assert p.agent_name("developer") == "pf-developer"


def test_nome_de_agente_invalido():
    with pytest.raises(ValueError, match="nome de agente inválido"):
        Role(cli="claude", name="nome com espaço")


def test_toml_do_projeto_importado_ida_e_volta(tmp_path):
    p = Project(
        id="mf",
        name="MediaFlow",
        path=tmp_path,
        method="proprio",
        references=["AGENTS.md", "docs/development/dev-orchestra.md"],
        task_source=TaskSource(
            kind="trello", trello_board="MediaFlow - Desenvolvimento", trello_lists={"Em desenvolvimento": "running"}
        ),
        roles={
            "developer": Role(cli="claude", name="codex-dev", agent="meuapp-developer", codex_resume="01a0-uuid"),
        },
    )
    config.save_project(p)
    assert config.load_project("mf") == p
    text = (config.paths.project_file("mf")).read_text(encoding="utf-8")
    assert 'metodo = "proprio"' in text and 'nome = "codex-dev"' in text and 'agente = "meuapp-developer"' in text
    portable = config.project_to_dict(p, portable=True)
    assert "codex_resume" not in portable["papeis"]["developer"]  # a thread é local desta máquina
    assert portable["papeis"]["developer"]["nome"] == "codex-dev"


def test_tarefa_guarda_origem_do_trello():
    text = (
        '+++\nid = "MF-1"\ntitulo = "Algo"\npapel = "developer"\nestado = "running"\ncriado = 2026-11-14\n'
        'trello = "https://trello.com/c/abc"\nlista = "Em desenvolvimento"\n+++\n\n## Objetivo\nx\n'
    )
    t = parse_task(text)
    assert t.trello == "https://trello.com/c/abc" and t.list_name == "Em desenvolvimento"
    again = parse_task(render_task(t))
    assert again.trello == t.trello and again.list_name == t.list_name


# --------------------------------------------------------- leitura e sugestão


def test_scan_le_ai_memory_agentes_e_trello(existing):
    r = scan_fake(existing)
    assert (r.ai_memory_workspace, r.ai_memory_project, r.project_exists) == ("default", "app_existente", True)
    assert [d.name for d in r.agent_defs] == ["meuapp-developer", "meuapp-planner"]
    assert r.agents_md and r.references == ["AGENTS.md", "docs/development/dev-orchestra.md"]
    assert r.trello_hint == "MediaFlow - Desenvolvimento"
    assert len(r.workstreams) == 8
    assert r.suggested_id() == "app_existente"
    # candidatos: sem sessão solta (codex-2/3) nem o workstream "default"
    assert [w.name for w in importer.candidate_workstreams(r)] == [
        "codex-orquestador",
        "codex-dev",
        "codex-planner",
        "meuapp-developer",
        "meuapp-orchestrator",
    ]


def test_sugestoes_batem_com_a_convencao_do_projeto(existing):
    s = importer.suggest_roles(scan_fake(existing))
    assert (s["orchestrator"].workstream, s["orchestrator"].cli, s["orchestrator"].agent) == (
        "codex-orquestador",
        "claude",
        None,
    )
    assert (s["developer"].workstream, s["developer"].cli, s["developer"].agent) == (
        "codex-dev",
        "claude",
        "meuapp-developer",
    )
    assert (s["planner"].workstream, s["planner"].agent) == ("codex-planner", "meuapp-planner")
    assert s["qa"].workstream is None  # o projeto não tem QA
    assert s["developer"].candidates[:2] == ["codex-dev", "meuapp-developer"]  # o mais recente primeiro


def test_scan_sem_ai_memory_toml(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    r = importer.scan(tmp_path, run=fake_run)
    assert r.ai_memory_project is None and r.workstreams is None and r.suggested_id() == tmp_path.name.lower()


def test_scan_com_ai_memory_indisponivel(existing):
    r = importer.scan(existing, run=lambda a, c=None, *rest: (1, ""))
    assert (
        r.project_exists is None and r.workstreams is None and importer.suggest_roles(r)["developer"].workstream is None
    )


def test_scan_de_projeto_que_nao_existe_no_ai_memory(existing):
    r = importer.scan(
        existing, run=lambda a, c=None, *rest: (0, "WORKSPACE  PROJECT  PROJECT ID  PAGES  LAST UPDATED\n")
    )
    assert r.project_exists is False and r.workstreams is None


def test_parse_lists():
    assert importer.parse_lists("Backlog=draft; Em desenvolvimento=running;; Lixo=voando; sem igual") == {
        "Backlog": "draft",
        "Em desenvolvimento": "running",
    }
    assert importer.format_lists({"A": "ready", "B": "done"}) == "A=ready; B=done"


# ------------------------------------------------- comandos de projeto importado


def projeto_importado(existing) -> Project:
    r = scan_fake(existing)
    s = importer.suggest_roles(r)
    roles = {role: Role(cli=x.cli, name=x.workstream, agent=x.agent) for role, x in s.items() if x.workstream}
    return importer.build_project(
        r,
        project_id="mf",
        name="MediaFlow",
        roles=roles,
        task_source=TaskSource(kind="trello", trello_board="MediaFlow - Desenvolvimento"),
    )


def test_comandos_preservam_nomes_workstreams_e_agentes(existing):
    p = projeto_importado(existing)
    ctx = local_context(p, workstreams={"codex-orquestador", "codex-dev", "codex-planner"})
    plan = build_launch_plan(p, GlobalConfig(), ctx)
    cmds = [c for b in plan.blocks for c in b.commands if not c.startswith("cd ")]
    assert cmds == [
        "ai-memory run --workstream codex-orquestador claude --name codex-orquestador",
        "ai-memory run --workstream codex-dev claude --name codex-dev --agent meuapp-developer",
        "ai-memory run --workstream codex-planner claude --name codex-planner --agent meuapp-planner",
    ]
    assert plan.warnings == []  # nada de "faltam arquivos do método" nem pedido de incorporar o AGENTS.md


def test_codex_retoma_a_thread_pelo_uuid(existing):
    p = projeto_importado(existing)
    p.roles["developer"] = Role(cli="codex", name="codex-dev", codex_resume="01a0dab2-uuid", default_model="gpt-6-luna")
    ctx = local_context(p, workstreams={"codex-dev"}, app_server_up=True)
    plan = build_launch_plan(p, GlobalConfig(), ctx)
    cmd = [c for b in plan.blocks for c in b.commands if "codex-dev" in c][0]
    assert cmd == (
        "ai-memory run --workstream codex-dev codex resume 01a0dab2-uuid --remote ws://127.0.0.1:4500 "
        "--model gpt-6-luna"
    )


def test_textos_de_abertura_citam_os_documentos_do_projeto(existing):
    p = projeto_importado(existing)
    p.references = ["AGENTS.md", "docs/development/dev-orchestra.md"]
    ctx = local_context(p)
    orq = opening_prompt(p, GlobalConfig(), "orchestrator", "claude-code", ctx)
    assert orq.startswith("Leia AGENTS.md e docs/development/dev-orchestra.md.")
    assert 'Seu nome de sessão e de workstream é "codex-orquestador"' in orq
    assert "roles/" not in orq and "templates/" not in orq and "PROTOCOL" not in orq
    assert (
        "board: MediaFlow - Desenvolvimento" in orq.replace("Board:", "board:") and "Cópias locais dos cartões" in orq
    )
    assert opening_prompt(p, GlobalConfig(), "developer", "claude-code", ctx) is None  # a definição de agente basta
    dev = opening_prompt(p, GlobalConfig(), "developer", "claude-code", ctx, force=True)
    assert 'definição de agente: "meuapp-developer"' in dev and 'o orquestrador é "codex-orquestador"' in dev


def test_scaffold_nao_copia_nada_em_projeto_importado(existing):
    p = projeto_importado(existing)
    report = contract.scaffold(p)
    assert report.own_method and report.created == []
    assert not (existing / "roles").exists() and not (existing / "templates").exists()
    assert "método próprio" in contract.format_report(report)
    assert contract.missing_files(p) == []


def test_status_usa_os_nomes_existentes(existing):
    p = projeto_importado(existing)
    sessions = [ClaudeSession(5, str(existing), "codex-orquestador", "s", "idle", "interactive")]
    svc = Services(
        claude_sessions=lambda: sessions,
        codex_threads=lambda u, c: [],
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [("default", "app_existente")],
    )
    by = {r.role: r for r in collect_status(p, GlobalConfig(), svc).roles}
    assert by["orchestrator"].state == "ociosa" and by["orchestrator"].name == "codex-orquestador"
    assert by["developer"].state == "fechada"


# ------------------------------------------------------------------- Trello


def test_instrucoes_de_importacao_do_trello(existing):
    p = projeto_importado(existing)
    p.task_source.trello_lists = {"Backlog": "draft", "Em desenvolvimento": "running"}
    text = trello.import_instructions(p)
    assert "board **MediaFlow - Desenvolvimento**" in text
    assert "Backlog → `draft`" in text and "Em desenvolvimento → `running`" in text
    assert "não altere cartões" in text and "não\ninicie nenhuma tarefa" in text.replace(
        "**não** inicie", "não\ninicie"
    )
    assert "`.dev-orchestra/tarefas/<ID>.md`" in text or ".dev-orchestra/tarefas/<ID>.md" in text
    assert 'trello = "<URL do cartão>"' in text and "Idempotência" in text and "não o sobrescreva" in text


def test_instrucoes_usam_o_mapa_padrao_quando_nao_ha_mapa(existing):
    p = projeto_importado(existing)
    text = trello.import_instructions(p)
    assert "Aguardando testes → `needs_review`" in text and "Finalizado → `done`" in text


def test_mensagem_de_importacao_do_trello(existing):
    p = projeto_importado(existing)
    msg = trello.import_message(p)
    assert ".dev-orchestra/abertura/importar-trello.md" in msg and "não altere o Trello" in msg and "\n" not in msg


def aberto(existing, status="idle"):
    return [ClaudeSession(5, str(existing), "codex-orquestador", "s", status, "interactive")]


def relatorio(p, sessions):
    svc = Services(
        claude_sessions=lambda: sessions,
        codex_threads=lambda u, c: [],
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [("default", "app_existente")],
    )
    return collect_status(p, GlobalConfig(), svc), svc


def test_plano_e_execucao_da_importacao_do_trello(existing):
    p = projeto_importado(existing)
    report, _ = relatorio(p, aberto(existing))
    item = dispatch.plan_trello_import(p, report)
    assert item.action == "send" and item.name == "codex-orquestador"
    vistos = []

    def run(args, cwd=None, timeout=None, env=None):
        vistos.append(args[-1])
        return 0, json.dumps({"is_error": False, "result": "ok"})

    _, result = dispatch.execute_trello_import(p, GlobalConfig(), item, run=run)
    assert result.ok and "Importação do Trello do projeto mf" in vistos[0]
    assert (existing / ".dev-orchestra" / "abertura" / "importar-trello.md").is_file()
    assert state.trello_import_info("mf")["para"] == "codex-orquestador"
    assert "importação já enviada" in dispatch.plan_trello_import(p, report).reason
    assert dispatch.plan_trello_import(p, report, again=True).action == "send"


def test_importacao_so_vale_para_fonte_trello(existing):
    p = projeto_importado(existing)
    p.task_source = TaskSource(kind="files")
    report, _ = relatorio(p, aberto(existing))
    assert "não é o Trello" in dispatch.plan_trello_import(p, report).reason


def test_importacao_com_orquestrador_fechado(existing):
    p = projeto_importado(existing)
    report, _ = relatorio(p, [])
    assert dispatch.plan_trello_import(p, report).action == "skip"


# --------------------------------------------------------------------- CLI


def run_cli(*argv, svc=None):
    out = io.StringIO()
    code = cli.main(list(argv), services=svc, out=out)
    return code, out.getvalue()


def svc_scan():
    return Services(scan_project=scan_fake)


def test_cli_scan_mostra_o_que_foi_encontrado(existing):
    code, text = run_cli("projects", "scan", str(existing), svc=svc_scan())
    assert code == 0
    assert "projeto 'app_existente' (workspace default) — existe" in text
    assert "meuapp-developer (.claude/agents/meuapp-developer.md)" in text
    assert "Trello: MediaFlow - Desenvolvimento" in text
    assert "codex-orquestador" in text and "(nenhum)" in text  # qa sem agente
    assert not config.paths.project_file("app_existente").exists()  # só leitura


def test_cli_import_existing_exige_confirmacao_e_depois_grava(existing):
    code, text = run_cli("projects", "import-existing", str(existing), svc=svc_scan())
    assert code == 0 and "Nada foi gravado" in text and not config.paths.project_file("app_existente").exists()
    code, text = run_cli("projects", "import-existing", str(existing), "--yes", svc=svc_scan())
    assert code == 0 and "Nomes e agentes preservados" in text
    p = config.load_project("app_existente")
    assert p.method == "proprio" and p.ai_memory_project == "app_existente"
    assert p.agent_name("orchestrator") == "codex-orquestador" and p.roles["developer"].agent == "meuapp-developer"
    assert p.task_source.kind == "trello" and p.task_source.trello_board == "MediaFlow - Desenvolvimento"
    assert "Em desenvolvimento" in p.task_source.trello_lists
    assert not (existing / "roles").exists() and not (existing / "templates").exists()
    assert (existing / ".dev-orchestra" / "tarefas").is_dir()  # só a pasta de tarefas é criada
    code, _ = run_cli("projects", "import-existing", str(existing), "--yes", svc=svc_scan())
    assert code == 2  # já existe


def test_cli_import_existing_pasta_inexistente(tmp_path, capsys):
    code, _ = run_cli("projects", "scan", str(tmp_path / "nada"), svc=svc_scan())
    assert code == 2 and "não existe" in capsys.readouterr().err


def test_cli_importar_trello(existing, monkeypatch):
    run_cli("projects", "import-existing", str(existing), "--yes", svc=svc_scan())
    p = config.load_project("app_existente")
    report, svc = relatorio(p, aberto(existing))
    code, text = run_cli("tasks", "import-trello", "app_existente", "--dry-run", svc=svc)
    assert code == 0 and "codex-orquestador (claude): enviar" in text
    assert not (existing / ".dev-orchestra" / "abertura").exists()
    monkeypatch.setattr(
        liveness, "run_command", lambda a, c=None, t=15.0, e=None: (0, json.dumps({"is_error": False, "result": "ok"}))
    )
    code, text = run_cli("tasks", "import-trello", "app_existente", svc=svc)
    assert code == 0 and "OK  codex-orquestador via SendMessage" in text
    code, text = run_cli("tasks", "import-trello", "app_existente", svc=svc)
    assert "importação já enviada" in text


# --------------------------------------------------------------------- TUI


def tui_services(existing, sessions=None) -> Services:
    return Services(
        claude_sessions=lambda: sessions or [],
        codex_threads=lambda u, c: [],
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [("default", "app_existente")],
        scan_project=scan_fake,
    )


async def esperar(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_tui_importar_projeto_existente(existing):
    app = DevOrchestraApp(tui_services(existing))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, ImportPathScreen)
        app.screen.query_one("#i-path", Input).value = str(existing)
        await pilot.click("#read")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ImportFormScreen)
        # sugestões já preenchidas com os agentes existentes
        assert form.query_one("#i-orchestrator-ws", Select).value == "codex-orquestador"
        assert form.query_one("#i-developer-ws", Select).value == "codex-dev"
        assert form.query_one("#i-developer-agent", Select).value == "meuapp-developer"
        assert form.query_one("#i-qa-ws", Select).value == Select.NULL
        assert form.query_one("#i-source", Select).value == "trello"
        assert form.query_one("#i-board", Input).value == "MediaFlow - Desenvolvimento"
        assert form.query_one("#i-id", Input).value == "app_existente"
        form.query_one("#i-developer-model", Input).value = "sonnet"
        form.query_one("#i-developer-effort").set_value("medium")
        await pilot.press("ctrl+s")  # o botão pode ficar abaixo da área visível em terminais pequenos
        await pilot.pause()
        assert isinstance(app.screen, ProjectsScreen)
    p = config.load_project("app_existente")
    assert p.method == "proprio" and p.agent_name("developer") == "codex-dev"
    assert p.roles["developer"].default_model == "sonnet" and p.roles["developer"].agent == "meuapp-developer"
    assert "qa" not in p.roles
    assert not (existing / "roles").exists()


async def test_tui_importar_exige_ao_menos_um_agente(existing):
    app = DevOrchestraApp(tui_services(existing))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        app.screen.query_one("#i-path", Input).value = str(existing)
        await pilot.click("#read")
        await pilot.pause()
        form = app.screen
        for role in ("orchestrator", "developer", "planner"):
            form.query_one(f"#i-{role}-ws", Select).value = Select.NULL
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, ImportFormScreen)
        assert "ao menos um papel" in str(form.query_one("#i-error").render())


async def test_tui_importar_pasta_invalida(tmp_path):
    app = DevOrchestraApp(tui_services(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        app.screen.query_one("#i-path", Input).value = str(tmp_path / "nao-existe")
        await pilot.click("#read")
        await pilot.pause()
        assert isinstance(app.screen, ImportPathScreen)
        assert "não existe" in str(app.screen.query_one("#i-error").render())


async def test_tui_editar_projeto_importado_preserva_nome_agente_e_metodo(existing):
    p = projeto_importado(existing)
    p.roles["developer"].codex_resume = "01a0-uuid"
    p.task_source.trello_lists = {"Backlog": "draft"}
    p.references = ["AGENTS.md"]
    config.save_project(p)
    app = DevOrchestraApp(tui_services(existing))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        assert isinstance(app.screen, ProjectScreen)
        await pilot.press("p")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ProjectFormScreen)
        form.query_one("#r-developer-model", Input).value = "opus"
        await pilot.click("#save")
        await esperar(app, pilot)
    edited = config.load_project("mf")
    assert edited.method == "proprio" and edited.references == ["AGENTS.md"]
    assert edited.agent_name("orchestrator") == "codex-orquestador"
    assert edited.roles["developer"].agent == "meuapp-developer"
    assert edited.roles["developer"].codex_resume == "01a0-uuid"
    assert edited.roles["developer"].default_model == "opus"
    assert edited.task_source.trello_lists == {"Backlog": "draft"}


async def test_tui_importar_cartoes_do_trello(existing, monkeypatch):
    config.save_project(projeto_importado(existing))
    enviados = []

    def fake_run(args, cwd=None, timeout=None, env=None):
        enviados.append(args[-1])
        return 0, json.dumps({"is_error": False, "result": "ok"})

    monkeypatch.setattr(liveness, "run_command", fake_run)
    app = DevOrchestraApp(tui_services(existing, aberto(existing)))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        await pilot.press("y")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await esperar(app, pilot)
        assert isinstance(app.screen, ProjectScreen)
    assert len(enviados) == 1 and "Importação do Trello do projeto mf" in enviados[0]
    assert (existing / ".dev-orchestra" / "abertura" / "importar-trello.md").is_file()


async def test_tui_importar_trello_sem_fonte_trello_nao_pede_confirmacao(existing):
    p = projeto_importado(existing)
    p.task_source = TaskSource(kind="files")
    config.save_project(p)
    app = DevOrchestraApp(tui_services(existing, aberto(existing)))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        await pilot.press("y")
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)


def test_launch_context_de_projeto_importado_nao_tem_pendencias(existing):
    p = projeto_importado(existing)
    ctx = local_context(p, workstreams=set())
    assert ctx.agents_md == "integrated" and ctx.missing_files == ()
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert plan.warnings == []
