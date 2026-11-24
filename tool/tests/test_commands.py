from __future__ import annotations

import pytest

from dev_orchestra.core import commands
from dev_orchestra.core.commands import LaunchContext, build_launch_plan, format_plan, local_context
from dev_orchestra.core.models import GlobalConfig, Project, Role, Subscription


def make_project(path, roles, **kw) -> Project:
    return Project(id="demo", name="Demo", path=path, roles=roles, **kw)


def only_commands(plan):
    return [c for b in plan.blocks for c in b.commands if not c.startswith("cd ")]


def test_modo_de_transporte(project_dir):
    cc = make_project(project_dir, {"orchestrator": Role(cli="claude"), "developer": Role(cli="claude")})
    cx = make_project(project_dir, {"orchestrator": Role(cli="codex"), "developer": Role(cli="codex")})
    mx = make_project(project_dir, {"orchestrator": Role(cli="claude"), "developer": Role(cli="codex")})
    assert [commands.transport_mode(p) for p in (cc, cx, mx)] == ["claude-code", "codex", "misto"]


def test_claude_primeira_vez_comandos(project_dir):
    p = make_project(
        project_dir,
        {
            "orchestrator": Role(cli="claude"),
            "developer": Role(cli="claude", default_model="sonnet", default_effort="medium"),
        },
    )
    ctx = LaunchContext(workstreams=set(), ai_memory_toml=True)
    plan = build_launch_plan(p, GlobalConfig(), ctx)
    assert only_commands(plan) == [
        "ai-memory run --new demo-orchestrator claude --name demo-orchestrator",
        "ai-memory run --new demo-developer claude --name demo-developer --model sonnet --effort medium",
    ]
    assert plan.mode == "claude-code" and plan.warnings == []


def test_retomada_usa_workstream(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="claude"), "developer": Role(cli="claude")})
    ctx = LaunchContext(workstreams={"demo-developer"}, ai_memory_toml=True)
    cmds = only_commands(build_launch_plan(p, GlobalConfig(), ctx))
    assert cmds[0].startswith("ai-memory run --new demo-orchestrator ")
    assert cmds[1].startswith("ai-memory run --workstream demo-developer ")


def test_codex_inclui_app_server_e_remote(project_dir):
    p = make_project(
        project_dir,
        {
            "orchestrator": Role(cli="codex", default_model="gpt-6-sol", default_effort="medium"),
            "developer": Role(cli="codex", default_model="gpt-6-luna", default_effort="low"),
        },
    )
    ctx = LaunchContext(workstreams=set(), app_server_up=False, ai_memory_toml=True)
    plan = build_launch_plan(p, GlobalConfig(), ctx)
    assert plan.blocks[0].title == "App Server do Codex"
    assert plan.blocks[0].commands == ["codex app-server --listen ws://127.0.0.1:4500"]
    assert only_commands(plan)[1:] == [
        "ai-memory run --new demo-orchestrator codex --remote ws://127.0.0.1:4500 --model gpt-6-sol "
        "-c 'model_reasoning_effort=\"medium\"'",
        "ai-memory run --new demo-developer codex --remote ws://127.0.0.1:4500 --model gpt-6-luna "
        "-c 'model_reasoning_effort=\"low\"'",
    ]


def test_app_server_ja_no_ar_nao_manda_iniciar_outro(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="codex")})
    plan = build_launch_plan(
        p, GlobalConfig(), LaunchContext(workstreams=set(), app_server_up=True, ai_memory_toml=True)
    )
    assert plan.blocks[0].commands == []
    assert "reutilize" in plan.blocks[0].notes[0]


def test_app_server_nao_verificado_avisa(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="codex")})
    plan = build_launch_plan(
        p, GlobalConfig(), LaunchContext(workstreams=set(), app_server_up=None, ai_memory_toml=True)
    )
    assert "Não foi possível verificar" in plan.blocks[0].notes[0]
    assert plan.blocks[0].commands


def test_sem_app_server_quando_so_claude(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="claude")})
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert all(b.title != "App Server do Codex" for b in plan.blocks)


def test_sem_ai_memory_toml_usa_project(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="claude")}, ai_memory_project="meu-proj")
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=False))
    assert only_commands(plan)[0].startswith("ai-memory run --project meu-proj --new demo-orchestrator ")
    assert any(".ai-memory.toml" in w for w in plan.warnings)


def test_workstreams_desconhecidos_avisam(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="claude")})
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=None, ai_memory_toml=True))
    assert any("não foi possível consultar os workstreams" in w for w in plan.warnings)
    assert "--new" in only_commands(plan)[0]


def test_assinatura_vira_profile(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="claude", subscription="claude-trabalho")})
    cfg = GlobalConfig(subscriptions={"claude-trabalho": Subscription(cli="claude", profile="work")})
    plan = build_launch_plan(p, cfg, LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert (
        only_commands(plan)[0] == "ai-memory run --new demo-orchestrator --profile work claude --name demo-orchestrator"
    )


def test_assinatura_inexistente_ou_de_outra_cli_avisa(project_dir):
    p = make_project(
        project_dir,
        {
            "orchestrator": Role(cli="claude", subscription="nao-existe"),
            "developer": Role(cli="claude", subscription="codex-pessoal"),
        },
    )
    cfg = GlobalConfig(subscriptions={"codex-pessoal": Subscription(cli="codex", profile="p")})
    plan = build_launch_plan(p, cfg, LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert any("não está definida" in w for w in plan.warnings)
    assert any("é de codex" in w for w in plan.warnings)


def test_agent_def_adiciona_flag_agent(project_dir):
    agents = project_dir / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.md").write_text("---\nname: developer\n---\n", encoding="utf-8")
    p = make_project(project_dir, {"orchestrator": Role(cli="claude"), "developer": Role(cli="claude")})
    ctx = local_context(p, workstreams=set())
    assert ctx.agent_defs == frozenset({"developer"})
    cmds = only_commands(build_launch_plan(p, GlobalConfig(), ctx))
    assert "--agent developer" in cmds[1] and "--agent" not in cmds[0]
    dev_block = build_launch_plan(p, GlobalConfig(), ctx).blocks[1]
    assert dev_block.prompt is None


def test_misto_avisa_e_ainda_gera_comandos(project):
    plan = build_launch_plan(
        project, GlobalConfig(), LaunchContext(workstreams=set(), app_server_up=True, ai_memory_toml=True)
    )
    assert plan.mode == "misto"
    assert any("delegador" in w for w in plan.warnings)
    assert len(only_commands(plan)) == 2


def test_caminho_com_espaco_e_citado(tmp_path):
    pasta = tmp_path / "meu projeto"
    pasta.mkdir()
    p = make_project(pasta, {"orchestrator": Role(cli="claude")})
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert plan.blocks[0].commands[0] == f"cd '{pasta}'"


def test_prompt_do_orquestrador_claude(project_dir):
    p = make_project(
        project_dir, {"orchestrator": Role(cli="claude"), "developer": Role(cli="claude"), "qa": Role(cli="claude")}
    )
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True))
    prompt = plan.blocks[0].prompt
    assert 'SendMessage para as sessões "demo-developer", "demo-qa"' in prompt
    assert "roles/orchestrator.md" in prompt
    assert ".dev-orchestra/tarefas/" in prompt


def test_prompt_do_orquestrador_codex_cita_endpoint(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="codex"), "developer": Role(cli="codex")})
    plan = build_launch_plan(
        p, GlobalConfig(), LaunchContext(workstreams=set(), app_server_up=True, ai_memory_toml=True)
    )
    assert "codex queue no endpoint ws://127.0.0.1:4500" in plan.blocks[1].prompt


def test_prompt_trello(project_dir):
    from dev_orchestra.core.models import TaskSource

    p = make_project(
        project_dir,
        {"orchestrator": Role(cli="claude")},
        task_source=TaskSource(kind="trello", trello_board="https://trello.com/b/x"),
    )
    prompt = (
        build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True)).blocks[0].prompt
    )
    assert "Trello" in prompt and "https://trello.com/b/x" in prompt


def test_prompt_de_papel_customizado(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="claude"), "social-media": Role(cli="claude")})
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert "roles/social-media.md" in plan.blocks[1].prompt


def test_formato_texto_do_plano(project_dir):
    p = make_project(project_dir, {"orchestrator": Role(cli="claude")})
    text = format_plan(build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True)))
    assert text.startswith("Terminal 1 — Orchestrator (claude)\n")
    assert f"  cd {project_dir}\n" in text
    assert "  ai-memory run --new demo-orchestrator claude --name demo-orchestrator\n" in text
    assert "Texto de abertura (cole na sessão depois de aberta):" in text
    assert "$ " not in text and "| " not in text  # cola direto no terminal


@pytest.mark.parametrize(("ws", "http"), [("ws://h:1", "http://h:1"), ("wss://h:1", "https://h:1")])
def test_url_http(ws, http):
    assert commands._http_url(ws) == http
