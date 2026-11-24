"""Identidade `[prefixo]-[papel]`, verificação do AI Memory, envio das instruções (CLI e TUI)."""

from __future__ import annotations

import io
import json

import pytest
from textual.widgets import DataTable, Input, Select

from dev_orchestra import cli
from dev_orchestra.core import config, dispatch, liveness, sender, state
from dev_orchestra.core.commands import LaunchContext, build_launch_plan, local_context
from dev_orchestra.core.liveness import (
    AiMemoryState,
    ClaudeSession,
    CodexThread,
    Services,
    Workstream,
    collect_status,
    describe_ai_memory,
)
from dev_orchestra.core.models import GlobalConfig, Project, Role
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.forms import ConfirmScreen, ProjectFormScreen
from dev_orchestra.tui.screens import ProjectScreen

SIZE = (160, 70)


def make(path, prefix=None, **roles) -> Project:
    return Project(id="demo", name="Demo", path=path, prefix=prefix, roles={k: Role(cli=v) for k, v in roles.items()})


def services(claude=None, codex=None, ws=None, projects=None) -> Services:
    return Services(
        claude_sessions=lambda: claude if claude is not None else [],
        codex_threads=lambda u, c: codex,
        workstreams=lambda n, c: ws if ws is not None else [],
        ai_memory_projects=lambda: projects if projects is not None else [("default", "demo")],
    )


# --------------------------------------------------------------- identidade


def test_nome_do_agente_usa_o_id_ou_o_prefixo(project_dir):
    assert make(project_dir, orchestrator="claude").agent_name("orchestrator") == "demo-orchestrator"
    assert make(project_dir, prefix="meu", developer="claude").agent_name("developer") == "meu-developer"


def test_prefixo_invalido(project_dir):
    with pytest.raises(ValueError, match="prefixo"):
        make(project_dir, prefix="Prefixo Ruim", orchestrator="claude")


def test_prefixo_vai_e_volta_no_toml_e_na_exportacao(project_dir):
    p = make(project_dir, prefix="meu", orchestrator="claude")
    config.save_project(p)
    assert config.load_project("demo").prefix == "meu"
    assert config.project_to_dict(p, portable=True)["projeto"]["prefixo"] == "meu"
    assert "prefixo" not in config.project_to_dict(make(project_dir, orchestrator="claude"))["projeto"]


def test_comandos_e_textos_usam_o_nome_com_prefixo(project_dir):
    p = make(project_dir, prefix="meu", orchestrator="claude", developer="claude")
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert "ai-memory run --new meu-orchestrator claude --name meu-orchestrator" in plan.blocks[0].commands[1]
    prompt = plan.blocks[0].prompt
    assert 'Seu nome de sessão e de workstream é "meu-orchestrator"' in prompt
    assert 'SendMessage para as sessões "meu-developer"; RETURN_TO é "meu-orchestrator"' in prompt
    assert 'o orquestrador é "meu-orchestrator"' in plan.blocks[1].prompt


def test_codex_orienta_a_nomear_a_thread(project_dir):
    p = make(project_dir, orchestrator="codex")
    plan = build_launch_plan(
        p, GlobalConfig(), LaunchContext(workstreams=set(), app_server_up=True, ai_memory_toml=True)
    )
    assert any("/rename demo-orchestrator" in n for n in plan.blocks[1].notes)


def test_cli_cadastro_com_prefixo(project_dir):
    out = io.StringIO()
    code = cli.main(
        [
            "projects",
            "add",
            "--id",
            "demo",
            "--path",
            str(project_dir),
            "--role",
            "developer=claude",
            "--prefix",
            "xyz",
        ],
        out=out,
    )
    assert code == 0
    show = io.StringIO()
    cli.main(["projects", "show", "demo"], out=show)
    assert "xyz-developer" in show.getvalue()


async def test_tui_formulario_tem_o_prefixo(project_dir):
    app = DevOrchestraApp(services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ProjectFormScreen)
        form.query_one("#f-id", Input).value = "novo"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#f-prefix", Input).value = "pf"
        form.query_one("#r-orchestrator-cli", Select).value = "claude"
        await pilot.click("#save")
        await pilot.pause()
    assert config.load_project("novo").prefix == "pf"


# ---------------------------------------------------------------- AI Memory

LIST_PROJECTS = """WORKSPACE  PROJECT                       PROJECT ID                             PAGES  LAST UPDATED
default    LogmaisOnline                 01a110ec-c3c2-7381-8133-9fdb34ef703e       2  2026-11-14T11:47:59.581257Z
default    demo                          01a1d72a-ca23-73c1-9a81-c1649ec3178f       3  2026-11-13T23:09:17.489436Z
other      demo                          01a1d72a-ca23-73c1-9a81-c1649ec31799       0  -
"""


def test_parse_projects():
    assert liveness.parse_projects(LIST_PROJECTS) == [
        ("default", "LogmaisOnline"),
        ("default", "demo"),
        ("other", "demo"),
    ]
    assert liveness.ai_memory_projects(lambda a: (1, "")) is None
    assert liveness.ai_memory_projects(lambda a: (0, LIST_PROJECTS))[1] == ("default", "demo")


def test_projeto_novo_no_ai_memory_e_primeira_vez(project_dir):
    p = make(project_dir, orchestrator="claude")
    report = collect_status(p, GlobalConfig(), services(projects=[("default", "outro")], ws=None))
    assert report.ai_memory == AiMemoryState(reachable=True, project_exists=False, workstreams=0)
    assert "ainda não existe" in describe_ai_memory(p, report.ai_memory)
    assert report.workstream_names == set() and report.notes == []  # sem aviso de erro: é só projeto novo


def test_projeto_existente_e_retomada(project_dir):
    p = make(project_dir, orchestrator="claude")
    ws = [Workstream("demo-orchestrator", "ontem", "claude-code")]
    report = collect_status(p, GlobalConfig(), services(ws=ws))
    assert report.ai_memory.project_exists is True and report.ai_memory.workstreams == 1
    assert "já existe com 1 workstream (retomada)" in describe_ai_memory(p, report.ai_memory)


def test_ai_memory_indisponivel(project_dir):
    p = make(project_dir, orchestrator="claude")
    report = collect_status(p, GlobalConfig(), services(projects=None))
    svc = Services(
        claude_sessions=lambda: [],
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: None,
        ai_memory_projects=lambda: None,
    )
    report = collect_status(p, GlobalConfig(), svc)
    assert report.ai_memory.reachable is False
    assert "indisponível" in describe_ai_memory(p, report.ai_memory)
    assert report.notes


# ------------------------------------------------------------------ remetente


def test_pointer_message_novo_e_retomada(project_dir):
    p = make(project_dir, orchestrator="claude")
    novo = sender.pointer_message(p, "orchestrator", resume=False)
    assert "Você é demo-orchestrator" in novo and ".dev-orchestra/abertura/demo-orchestrator.md" in novo
    assert "não inicie nenhuma tarefa" in novo and "\n" not in novo
    retomada = sender.pointer_message(p, "orchestrator", resume=True)
    assert retomada.startswith("Retomada do projeto demo") and "Releia" in retomada


def test_arquivo_de_instrucoes_e_completo_mesmo_com_definicao_de_agente(project_dir):
    agents = project_dir / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.md").write_text("x", encoding="utf-8")
    p = make(project_dir, orchestrator="claude", developer="claude")
    ctx = local_context(p, workstreams=set())
    path = sender.write_instruction_file(p, GlobalConfig(), "developer", ctx)
    assert path == project_dir / ".dev-orchestra" / "abertura" / "demo-developer.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Instruções de demo-developer (Developer)")
    assert "Leia AGENTS.md, roles/developer.md" in text and 'é "demo-developer"' in text


def test_send_codex_usa_queue_com_o_nome_da_thread():
    chamadas = []

    def run(args, cwd=None, timeout=None, env=None):
        chamadas.append(args)
        return 0, "Queued message 01abc for thread 01xyz.\n"

    r = sender.send_codex("ws://127.0.0.1:4500", "demo-developer", "oi", run=run)
    assert r.ok and r.via == "codex queue"
    assert chamadas == [
        ["codex", "queue", "--remote", "ws://127.0.0.1:4500", "--thread", "demo-developer", "--message", "oi"]
    ]
    assert not sender.send_codex("ws://x", "t", "m", run=lambda a, c=None, t=None, e=None: (1, "erro\n")).ok


def test_send_claude_chama_a_sessao_auxiliar_com_sendmessage():
    visto = {}

    def run(args, cwd=None, timeout=None, env=None):
        visto.update(args=args, cwd=cwd, env=env)
        return 0, json.dumps({"is_error": False, "result": "ok"})

    r = sender.send_claude("demo-orchestrator", "texto da mensagem", run=run)
    assert r.ok and r.via == "SendMessage"
    args = visto["args"]
    assert args[:2] == ["claude", "-p"] and "--no-session-persistence" in args
    assert args[args.index("--allowedTools") + 1] == "SendMessage ListAgents"
    assert 'sessão chamada "demo-orchestrator"' in args[-1] and "texto da mensagem" in args[-1]
    assert visto["env"] == {"AI_MEMORY_CAPTURE_OWNER": "dev-orchestra-relay"}  # não polui o AI Memory
    assert visto["cwd"] != ""


@pytest.mark.parametrize(
    "saida",
    [
        (0, json.dumps({"is_error": False, "result": "falhou: sessão não encontrada"})),
        (0, json.dumps({"is_error": True, "result": "ok"})),
        (1, json.dumps({"is_error": False, "result": "ok"})),
        (0, "não é json"),
    ],
)
def test_send_claude_falhas(saida):
    r = sender.send_claude("x", "m", run=lambda a, c=None, t=None, e=None: saida)
    assert not r.ok


# --------------------------------------------------------------------- estado


def test_estado_marca_e_lembra_o_envio():
    assert state.sent_info("demo", "demo-developer") is None
    assert state.describe(None) == "pendentes"
    state.mark_sent("demo", "demo-developer", via="codex queue", resume=False)
    info = state.sent_info("demo", "demo-developer")
    assert info["via"] == "codex queue" and info["retomada"] is False
    assert state.describe(info).startswith("enviadas ")
    state.clear("demo", "demo-developer")
    assert state.sent_info("demo", "demo-developer") is None


def test_estado_corrompido_vira_vazio():
    from dev_orchestra.core import paths

    paths.state_file("demo").parent.mkdir(parents=True, exist_ok=True)
    paths.state_file("demo").write_text("{ruim", encoding="utf-8")
    assert state.load("demo") == {}


# -------------------------------------------------------------------- despacho


def relatorio(project, claude=None, codex=None):
    return collect_status(project, GlobalConfig(), services(claude=claude, codex=codex))


def itens(items):
    return {i.role: (i.action, i.resume) for i in items}


def test_plano_envia_so_para_sessoes_abertas(project_dir):
    p = make(project_dir, orchestrator="claude", developer="claude")
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    items = dispatch.plan_instructions(p, relatorio(p, claude=sessions))
    assert itens(items) == {"orchestrator": ("send", False), "developer": ("skip", False)}
    assert "fechada" in [i for i in items if i.role == "developer"][0].reason


def test_plano_nao_reenvia_sem_again_e_marca_retomada(project_dir):
    p = make(project_dir, orchestrator="claude")
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "busy", "interactive")]
    state.mark_sent("demo", "demo-orchestrator", via="SendMessage", resume=False)
    skip = dispatch.plan_instructions(p, relatorio(p, claude=sessions))
    assert itens(skip) == {"orchestrator": ("skip", True)} and "--again" in skip[0].reason
    again = dispatch.plan_instructions(p, relatorio(p, claude=sessions), again=True)
    assert itens(again) == {"orchestrator": ("send", True)}
    assert "ocupada" in again[0].reason


def test_plano_filtra_por_papel(project_dir):
    p = make(project_dir, orchestrator="claude", developer="claude")
    sessions = [
        ClaudeSession(1, str(project_dir), "demo-orchestrator", "s", "idle", "interactive"),
        ClaudeSession(2, str(project_dir), "demo-developer", "s", "idle", "interactive"),
    ]
    items = dispatch.plan_instructions(p, relatorio(p, claude=sessions), only={"developer"})
    assert list(itens(items)) == ["developer"]


def test_codex_com_uma_thread_sem_nome_sera_nomeada(project_dir):
    p = make(project_dir, developer="codex")
    threads = [CodexThread("aaaaaaaa-1111", None, str(project_dir), "idle")]
    items = dispatch.plan_instructions(p, relatorio(p, codex=threads))
    assert items[0].action == "rename+send" and items[0].thread_id == "aaaaaaaa-1111"
    assert "será nomeada 'demo-developer'" in items[0].reason


def test_codex_com_varias_threads_sem_nome_nao_adivinha(project_dir):
    p = make(project_dir, developer="codex")
    threads = [CodexThread("a1", None, str(project_dir), "idle"), CodexThread("b2", "", str(project_dir), "active")]
    items = dispatch.plan_instructions(p, relatorio(p, codex=threads))
    assert items[0].action == "skip" and "mais de uma thread sem nome" in items[0].reason


def test_codex_thread_sem_nome_nao_carregada_nao_conta(project_dir):
    p = make(project_dir, developer="codex")
    threads = [CodexThread("a1", None, str(project_dir), "notLoaded")]
    items = dispatch.plan_instructions(p, relatorio(p, codex=threads))
    assert items[0].action == "skip"


def test_codex_duas_funcoes_sem_nome_nao_atribui_a_unica_thread(project_dir):
    p = make(project_dir, orchestrator="codex", developer="codex")
    threads = [CodexThread("a1", None, str(project_dir), "idle")]
    items = dispatch.plan_instructions(p, relatorio(p, codex=threads))
    assert all(i.action == "skip" for i in items)


def test_executar_grava_arquivo_envia_e_registra(project_dir):
    p = make(project_dir, orchestrator="claude", developer="codex")
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    threads = [CodexThread("aaaaaaaa-1111", None, str(project_dir), "idle")]
    report = relatorio(p, claude=sessions, codex=threads)
    items = dispatch.plan_instructions(p, report)
    renomeadas, chamadas = [], []

    def run(args, cwd=None, timeout=None, env=None):
        chamadas.append(args[:2])
        if args[0] == "codex":
            return 0, "Queued message 01abc for thread 01xyz.\n"
        return 0, json.dumps({"is_error": False, "result": "ok"})

    results = dispatch.execute(
        p, GlobalConfig(), report, items, run=run, rename=lambda url, tid, name: renomeadas.append((tid, name)) or True
    )
    assert [r.ok for _, r in results] == [True, True]
    assert renomeadas == [("aaaaaaaa-1111", "demo-developer")]
    assert ["claude", "-p"] in chamadas and ["codex", "queue"] in chamadas
    assert (project_dir / ".dev-orchestra" / "abertura" / "demo-orchestrator.md").is_file()
    assert (project_dir / ".dev-orchestra" / "abertura" / "demo-developer.md").is_file()
    assert state.sent_info("demo", "demo-orchestrator")["via"] == "SendMessage"
    assert state.sent_info("demo", "demo-developer")["via"] == "codex queue"
    # depois do envio, o plano pula os dois
    assert {i.action for i in dispatch.plan_instructions(p, report)} == {"skip"}


def test_falha_no_envio_nao_marca_como_enviado(project_dir):
    p = make(project_dir, orchestrator="claude")
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    report = relatorio(p, claude=sessions)
    items = dispatch.plan_instructions(p, report)
    results = dispatch.execute(p, GlobalConfig(), report, items, run=lambda a, c=None, t=None, e=None: (1, ""))
    assert not results[0][1].ok
    assert state.sent_info("demo", "demo-orchestrator") is None
    assert "FALHOU" in dispatch.format_results(results)


def test_falha_ao_nomear_thread_aborta_o_envio(project_dir):
    p = make(project_dir, developer="codex")
    report = relatorio(p, codex=[CodexThread("a1", None, str(project_dir), "idle")])
    items = dispatch.plan_instructions(p, report)
    chamadas = []
    results = dispatch.execute(
        p,
        GlobalConfig(),
        report,
        items,
        run=lambda a, c=None, t=None, e=None: chamadas.append(a) or (0, "Queued message x"),
        rename=lambda *a: False,
    )
    assert not results[0][1].ok and chamadas == []


# ------------------------------------------------------------------------ CLI


def rodar(*argv, svc=None):
    out = io.StringIO()
    code = cli.main(list(argv), services=svc, out=out)
    return code, out.getvalue()


def cadastrar(project_dir, *roles):
    rodar(
        "projects",
        "add",
        "--id",
        "demo",
        "--path",
        str(project_dir),
        "--no-scaffold",
        *[x for r in roles for x in ("--role", r)],
    )


def test_cli_send_dry_run(project_dir):
    cadastrar(project_dir, "orchestrator=claude", "developer=claude")
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    code, text = rodar("send", "demo", "--dry-run", svc=services(claude=sessions))
    assert code == 0
    assert "demo-orchestrator (claude): enviar" in text and "demo-developer (claude): pular" in text
    assert not (project_dir / ".dev-orchestra" / "abertura").exists()


def test_cli_send_executa_e_status_mostra_as_instrucoes(project_dir, monkeypatch):
    cadastrar(project_dir, "orchestrator=claude")
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    monkeypatch.setattr(
        liveness, "run_command", lambda a, c=None, t=15.0, e=None: (0, json.dumps({"is_error": False, "result": "ok"}))
    )
    code, text = rodar("send", "demo", svc=services(claude=sessions))
    assert code == 0 and "OK  demo-orchestrator via SendMessage" in text
    code, text = rodar("status", "demo", svc=services(claude=sessions))
    assert "enviadas " in text and "AI Memory: o projeto 'demo' já existe" in text
    code, text = rodar("send", "demo", svc=services(claude=sessions))
    assert "já enviadas" in text and "--again" in text


def test_cli_send_falha_devolve_codigo_1(project_dir, monkeypatch):
    cadastrar(project_dir, "orchestrator=claude")
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    monkeypatch.setattr(liveness, "run_command", lambda a, c=None, t=15.0, e=None: (1, ""))
    code, text = rodar("send", "demo", svc=services(claude=sessions))
    assert code == 1 and "FALHOU" in text


def test_cli_send_papel_desconhecido(project_dir, capsys):
    cadastrar(project_dir, "orchestrator=claude")
    code, _ = rodar("send", "demo", "--role", "qa", svc=services())
    assert code == 2 and "papel desconhecido" in capsys.readouterr().err


def test_cli_resume_mostra_a_situacao_do_ai_memory(project_dir):
    cadastrar(project_dir, "orchestrator=claude")
    code, text = rodar("resume", "demo", svc=services(projects=[]))
    assert "ainda não existe" in text and "primeira vez" in text
    assert "ai-memory run --project demo --new demo-orchestrator" in text


# ------------------------------------------------------------------------ TUI


async def esperar(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_tui_enviar_instrucoes_com_confirmacao(project_dir, monkeypatch):
    p = make(project_dir, orchestrator="claude", developer="claude")
    config.save_project(p)
    sessions = [
        ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive"),
        ClaudeSession(11, str(project_dir), "demo-developer", "s", "idle", "interactive"),
    ]
    enviados = []

    def fake_run(args, cwd=None, timeout=None, env=None):
        enviados.append(args[-1])
        return 0, json.dumps({"is_error": False, "result": "ok"})

    monkeypatch.setattr(liveness, "run_command", fake_run)
    app = DevOrchestraApp(services(claude=sessions))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        roles = app.screen.query_one("#roles", DataTable)
        assert roles.get_row("orchestrator")[0].plain == "demo-orchestrator"
        assert roles.get_row("orchestrator")[4].plain == "pendentes"
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await esperar(app, pilot)
        assert isinstance(app.screen, ProjectScreen)
        await esperar(app, pilot)
        assert app.screen.query_one("#roles", DataTable).get_row("orchestrator")[4].plain.startswith("✓")
    assert len(enviados) == 2
    assert any("demo-orchestrator" in m and "Você é demo-orchestrator" in m for m in enviados)
    assert state.sent_info("demo", "demo-developer") is not None


async def test_tui_enviar_pode_ser_cancelado(project_dir, monkeypatch):
    config.save_project(make(project_dir, orchestrator="claude"))
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    monkeypatch.setattr(liveness, "run_command", lambda *a, **k: pytest.fail("não deveria enviar"))
    app = DevOrchestraApp(services(claude=sessions))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        await pilot.press("i")
        await pilot.pause()
        await pilot.click("#no")
        await esperar(app, pilot)
    assert state.sent_info("demo", "demo-orchestrator") is None


async def test_tui_sem_sessao_aberta_nao_pede_confirmacao(project_dir):
    config.save_project(make(project_dir, orchestrator="claude"))
    app = DevOrchestraApp(services(claude=[]))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)


async def test_tui_mostra_o_ai_memory_na_tela_do_projeto(project_dir):
    config.save_project(make(project_dir, orchestrator="claude"))
    app = DevOrchestraApp(services(projects=[]))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        texto = str(app.screen.query_one("#banner").render())
        assert "ainda não existe" in texto and "prefixo dos agentes: demo" in texto


# ------------------------------------------------- envio de tarefa ao orquestrador


def abrir_orquestrador(project_dir, status="idle"):
    return [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", status, "interactive")]


def test_mensagem_da_tarefa_para_um_worker(project_dir):
    from dev_orchestra.core import tasks

    p = make(project_dir, orchestrator="claude", developer="claude")
    t = tasks.create_task(p, "T-1", "Primeira", "developer")
    msg = sender.task_message(p, t)
    assert "\n" not in msg
    assert "Nova tarefa do projeto demo: T-1 — Primeira. Leia .dev-orchestra/tarefas/T-1.md." in msg
    assert "Papel executor indicado: developer (demo-developer)" in msg
    assert "recomende MODEL, EFFORT e MODEL_REASON" in msg and "só então delegue" in msg
    assert "Não implemente a tarefa você mesmo" in msg


def test_mensagem_da_tarefa_do_proprio_orquestrador(project_dir):
    from dev_orchestra.core import tasks

    p = make(project_dir, orchestrator="claude")
    t = tasks.create_task(p, "T-2", "Minha", "orchestrator")
    msg = sender.task_message(p, t)
    assert "Esta tarefa é sua" in msg and "Papel executor" not in msg


def test_plano_da_tarefa(project_dir):
    from dev_orchestra.core import tasks

    p = make(project_dir, orchestrator="claude", developer="claude")
    tasks.create_task(p, "T-1", "x", "developer")
    aberto = relatorio(p, claude=abrir_orquestrador(project_dir))
    item = dispatch.plan_task(p, aberto, "T-1")
    assert (
        item.action == "send" and item.name == "demo-orchestrator" and "ainda não recebeu as instruções" in item.reason
    )
    fechado = relatorio(p, claude=[])
    assert dispatch.plan_task(p, fechado, "T-1").action == "skip"
    ocupado = relatorio(p, claude=abrir_orquestrador(project_dir, "busy"))
    assert "fim do turno" in dispatch.plan_task(p, ocupado, "T-1").reason
    with pytest.raises(tasks.TaskError):
        dispatch.plan_task(p, aberto, "NAO-EXISTE")


def test_plano_da_tarefa_sem_papel_orquestrador(project_dir):
    from dev_orchestra.core import tasks

    p = make(project_dir, developer="claude")
    tasks.create_task(p, "T-1", "x", "developer")
    item = dispatch.plan_task(p, relatorio(p, claude=[]), "T-1")
    assert item.action == "skip" and "não tem o papel orchestrator" in item.reason


def test_executar_tarefa_registra_e_nao_repete(project_dir):
    from dev_orchestra.core import tasks

    p = make(project_dir, orchestrator="claude", developer="claude")
    tasks.create_task(p, "T-1", "x", "developer")
    report = relatorio(p, claude=abrir_orquestrador(project_dir))
    item = dispatch.plan_task(p, report, "T-1")
    vistos = []

    def run(args, cwd=None, timeout=None, env=None):
        vistos.append(args[-1])
        return 0, json.dumps({"is_error": False, "result": "ok"})

    _, result = dispatch.execute_task(p, GlobalConfig(), "T-1", item, run=run)
    assert result.ok and "Nova tarefa do projeto demo: T-1" in vistos[0]
    assert state.task_sent_info("demo", "T-1")["para"] == "demo-orchestrator"
    assert "tarefa já enviada" in dispatch.plan_task(p, report, "T-1").reason
    assert dispatch.plan_task(p, report, "T-1", again=True).action == "send"


def test_executar_tarefa_com_falha_nao_marca(project_dir):
    from dev_orchestra.core import tasks

    p = make(project_dir, orchestrator="claude")
    tasks.create_task(p, "T-1", "x", "orchestrator")
    report = relatorio(p, claude=abrir_orquestrador(project_dir))
    item = dispatch.plan_task(p, report, "T-1")
    _, result = dispatch.execute_task(p, GlobalConfig(), "T-1", item, run=lambda a, c=None, t=None, e=None: (1, ""))
    assert not result.ok and state.task_sent_info("demo", "T-1") is None


def test_cli_tarefa_enviar(project_dir, monkeypatch):
    cadastrar(project_dir, "orchestrator=claude", "developer=claude")
    rodar("tasks", "new", "demo", "T-1", "--title", "Primeira", "--role", "developer")
    svc = services(claude=abrir_orquestrador(project_dir))
    code, text = rodar("tasks", "send", "demo", "T-1", "--dry-run", svc=svc)
    assert code == 0 and "demo-orchestrator (claude): enviar" in text and state.task_sent_info("demo", "T-1") is None
    monkeypatch.setattr(
        liveness, "run_command", lambda a, c=None, t=15.0, e=None: (0, json.dumps({"is_error": False, "result": "ok"}))
    )
    code, text = rodar("tasks", "send", "demo", "T-1", svc=svc)
    assert code == 0 and "OK  demo-orchestrator via SendMessage" in text
    code, text = rodar("tasks", "send", "demo", "T-1", svc=svc)
    assert "tarefa já enviada" in text
    code, text = rodar("tasks", "list", "demo")
    assert "T-1" in text


def test_cli_tarefa_enviar_inexistente(project_dir, capsys):
    cadastrar(project_dir, "orchestrator=claude")
    code, _ = rodar("tasks", "send", "demo", "NADA", svc=services(claude=abrir_orquestrador(project_dir)))
    assert code == 2 and "não encontrada" in capsys.readouterr().err


async def test_tui_enviar_tarefa(project_dir, monkeypatch):
    from dev_orchestra.core import tasks

    p = make(project_dir, orchestrator="claude", developer="claude")
    config.save_project(p)
    tasks.create_task(p, "T-1", "Primeira", "developer")
    enviados = []

    def fake_run(args, cwd=None, timeout=None, env=None):
        enviados.append(args[-1])
        return 0, json.dumps({"is_error": False, "result": "ok"})

    monkeypatch.setattr(liveness, "run_command", fake_run)
    app = DevOrchestraApp(services(claude=abrir_orquestrador(project_dir)))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        tabela = app.screen.query_one("#tasks", DataTable)
        assert tabela.get_row("T-1")[4].plain == "-"
        await pilot.press("g")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await esperar(app, pilot)
        assert isinstance(app.screen, ProjectScreen)
        assert "→ demo-orchestrator" in app.screen.query_one("#tasks", DataTable).get_row("T-1")[4].plain
    assert len(enviados) == 1 and "T-1" in enviados[0]


async def test_tui_enviar_tarefa_sem_selecao_ou_sem_sessao(project_dir):
    config.save_project(make(project_dir, orchestrator="claude"))
    app = DevOrchestraApp(services(claude=[]))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        await pilot.press("g")  # sem tarefa
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)


async def test_tui_reenviar_instrucoes_ja_enviadas_pede_confirmacao(project_dir, monkeypatch):
    config.save_project(make(project_dir, orchestrator="claude"))
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "idle", "interactive")]
    enviados = []

    def fake_run(args, cwd=None, timeout=None, env=None):
        enviados.append(args[-1])
        return 0, json.dumps({"is_error": False, "result": "ok"})

    monkeypatch.setattr(liveness, "run_command", fake_run)
    state.mark_sent("demo", "demo-orchestrator", via="claude -p", resume=False)
    app = DevOrchestraApp(services(claude=sessions))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        await pilot.press("i")  # já enviadas: em vez de "nada a enviar", oferece reenviar
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#no")
        await pilot.pause()
        assert enviados == []  # recusou: nada foi enviado
        await pilot.press("i")
        await pilot.pause()
        await pilot.click("#yes")  # confirma o reenvio (uma só confirmação)
        await esperar(app, pilot)
    assert len(enviados) == 1 and "demo-orchestrator" in enviados[0]


async def test_tui_reenviar_tarefa_ja_enviada_pede_confirmacao(project_dir, monkeypatch):
    from dev_orchestra.core import tasks

    p = make(project_dir, orchestrator="claude", developer="claude")
    config.save_project(p)
    tasks.create_task(p, "T-1", "Primeira", "developer")
    enviados = []

    def fake_run(args, cwd=None, timeout=None, env=None):
        enviados.append(args[-1])
        return 0, json.dumps({"is_error": False, "result": "ok"})

    monkeypatch.setattr(liveness, "run_command", fake_run)
    app = DevOrchestraApp(services(claude=abrir_orquestrador(project_dir)))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await esperar(app, pilot)
        await pilot.press("g")
        await pilot.pause()
        await pilot.click("#yes")
        await esperar(app, pilot)
        assert len(enviados) == 1
        await pilot.press("g")  # de novo: já enviada -> oferece o reenvio
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#no")
        await pilot.pause()
        assert len(enviados) == 1
        await pilot.press("G")  # atalho direto de reenvio
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await esperar(app, pilot)
        assert len(enviados) == 2
        await pilot.press("g")  # pelo aviso "já enviada": confirmar envia direto
        await pilot.pause()
        await pilot.click("#yes")
        await esperar(app, pilot)
    assert len(enviados) == 3 and "T-1" in enviados[1]
