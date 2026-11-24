from __future__ import annotations

import io

import pytest

from dev_orchestra import cli
from dev_orchestra.core import config, paths
from dev_orchestra.core.liveness import ClaudeSession, CodexThread, Services, Workstream


def run(*argv, services=None):
    out = io.StringIO()
    code = cli.main(list(argv), services=services, out=out)
    return code, out.getvalue()


def cadastrar(project_dir, *roles, extra=()):
    args = ["projects", "add", "--id", "demo", "--name", "Demo", "--path", str(project_dir)]
    for r in roles or ("orchestrator=claude,model=sonnet,effort=medium", "developer=codex,model=gpt-6-luna,effort=low"):
        args += ["--role", r]
    return run(*args, *extra)


def servicos(claude=None, codex=None, ws=None, projetos=None):
    return Services(
        claude_sessions=lambda: claude,
        codex_threads=lambda u, c: codex,
        workstreams=lambda n, c: ws,
        ai_memory_projects=lambda: projetos if projetos is not None else [("default", "demo")],
    )


def test_parse_role_spec():
    rid, role = cli.parse_role_spec("developer=codex,model=gpt-6-luna,effort=low,subscription=x")
    assert rid == "developer"
    assert (role.cli, role.default_model, role.default_effort, role.subscription) == ("codex", "gpt-6-luna", "low", "x")


@pytest.mark.parametrize("spec", ["developer", "developer=gemini", "developer=codex,foo=bar", "developer=codex,model"])
def test_parse_role_spec_invalido(spec):
    with pytest.raises(cli.CliError):
        cli.parse_role_spec(spec)


def test_adicionar_listar_e_mostrar(project_dir):
    code, text = cadastrar(project_dir)
    assert code == 0 and "cadastrado" in text
    assert (project_dir / ".dev-orchestra" / "tarefas").is_dir()
    code, text = run("projects", "list")
    assert code == 0 and "demo" in text and "orchestrator:claude" in text and "developer:codex" in text
    code, text = run("projects", "show", "demo")
    assert "gpt-6-luna" in text and "AI Memory: projeto demo" in text


def test_listar_vazio():
    code, text = run("projects", "list")
    assert code == 0 and "Nenhum projeto" in text


def test_adicionar_duplicado_exige_force(project_dir, capsys):
    cadastrar(project_dir)
    code, _ = cadastrar(project_dir)
    assert code == 2 and "já existe" in capsys.readouterr().err
    code, _ = cadastrar(project_dir, extra=["--force"])
    assert code == 0


def test_adicionar_pasta_inexistente(tmp_path, capsys):
    code, _ = run("projects", "add", "--id", "x", "--path", str(tmp_path / "nada"), "--role", "orchestrator=claude")
    assert code == 2 and "não existe" in capsys.readouterr().err


def test_adicionar_sem_papel(project_dir, capsys):
    code, _ = run("projects", "add", "--id", "x", "--path", str(project_dir))
    assert code == 2 and "--role" in capsys.readouterr().err


def test_adicionar_id_invalido(project_dir, capsys):
    code, _ = run("projects", "add", "--id", "Meu Projeto", "--path", str(project_dir), "--role", "orchestrator=claude")
    assert code == 2 and "id do projeto" in capsys.readouterr().err


def test_remover_exige_confirmacao(project_dir, capsys):
    cadastrar(project_dir)
    code, _ = run("projects", "remove", "demo")
    assert code == 2
    capsys.readouterr()
    code, text = run("projects", "remove", "demo", "--yes")
    assert code == 0 and not paths.project_file("demo").exists()
    assert project_dir.is_dir()  # a pasta do projeto não é tocada


def test_projeto_inexistente(capsys):
    code, _ = run("projects", "show", "nada")
    assert code == 2 and "não encontrado" in capsys.readouterr().err


def test_assinaturas():
    code, text = run("subscriptions", "list")
    assert "Nenhuma assinatura" in text
    run("subscriptions", "add", "claude-trabalho", "--cli", "claude", "--profile", "work")
    code, text = run("subscriptions", "list")
    assert "claude-trabalho" in text and "work" in text


def test_tarefas_fluxo_completo(project_dir):
    cadastrar(project_dir)
    code, text = run(
        "tasks", "new", "demo", "T-1", "--title", "Primeira", "--role", "developer", "--objective", "Fazer X"
    )
    assert code == 0 and "T-1.md" in text
    code, text = run("tasks", "list", "demo")
    assert "T-1" in text and "gpt-6-luna" in text and "ready" in text
    code, text = run("tasks", "set-state", "demo", "T-1", "running")
    assert "running" in text
    assert "running" in run("tasks", "list", "demo")[1]


def test_tarefa_erros(project_dir, capsys):
    cadastrar(project_dir)
    code, _ = run("tasks", "new", "demo", "T-1", "--title", "x", "--role", "qa")
    assert code == 2 and "papel desconhecido" in capsys.readouterr().err
    code, _ = run("tasks", "set-state", "demo", "NADA", "done")
    assert code == 2 and "não encontrada" in capsys.readouterr().err


def test_status_com_servicos_falsos(project_dir):
    cadastrar(project_dir)
    sessions = [ClaudeSession(10, str(project_dir), "demo-orchestrator", "s", "busy", "interactive")]
    threads = [CodexThread("abcdef12-1", "demo-developer", str(project_dir), "idle")]
    code, text = run(
        "status",
        "demo",
        services=servicos(claude=sessions, codex=threads, ws=[Workstream("demo-orchestrator", "agora", "claude-code")]),
    )
    assert code == 0
    assert "ocupada" in text and "ociosa" in text and "workstream usado agora" in text


def test_status_fontes_indisponiveis(project_dir):
    cadastrar(project_dir)
    code, text = run("status", "demo", services=servicos())
    assert code == 0 and "desconhecida" in text and "nota:" in text


def test_up_offline_nao_consulta_nada(project_dir):
    cadastrar(project_dir)
    code, text = run("up", "demo", "--offline")
    assert code == 0
    assert "Terminal 1 — App Server do Codex" in text
    assert "ai-memory run --project demo --new demo-orchestrator claude --name demo-orchestrator" in text
    assert "--model sonnet --effort medium" in text
    assert (
        "ai-memory run --project demo --new demo-developer codex --remote ws://127.0.0.1:4500 --model gpt-6-luna"
        in text
    )


def test_up_com_workstream_existente_e_app_server_no_ar(project_dir):
    cadastrar(project_dir)
    (project_dir / ".ai-memory.toml").write_text('workspace = "default"\nproject = "demo"\n', encoding="utf-8")
    svc = servicos(claude=[], codex=[], ws=[Workstream("demo-developer", "ontem", "codex")])
    code, text = run("up", "demo", services=svc)
    assert "ai-memory run --new demo-orchestrator claude" in text
    assert "ai-memory run --workstream demo-developer codex" in text
    assert "reutilize" in text  # App Server já responde
    assert "--project" not in text  # .ai-memory.toml presente


def test_resume_mostra_andamento_resultados_e_comandos(project_dir):
    cadastrar(project_dir)
    run("tasks", "new", "demo", "T-1", "--title", "Em curso", "--role", "developer")
    run("tasks", "set-state", "demo", "T-1", "running")
    run("tasks", "new", "demo", "T-2", "--title", "Pronta", "--role", "developer")
    run("tasks", "new", "demo", "T-3", "--title", "Concluída", "--role", "developer")
    project = config.load_project("demo")
    arquivo = project.tasks_path / "T-3.md"
    arquivo.write_text(
        arquivo.read_text(encoding="utf-8").replace(
            "(preenchido pelo orquestrador ao concluir)", "STATUS: DONE\nEVIDENCE: tudo certo"
        ),
        encoding="utf-8",
    )
    run("tasks", "set-state", "demo", "T-3", "done")
    code, text = run("resume", "demo", "--offline")
    assert code == 0
    assert "T-1 [running] Em curso" in text
    assert "Prontas/rascunho: 1" in text
    assert "T-3 [done] STATUS: DONE EVIDENCE: tudo certo" in text
    assert "Comandos para subir ou retomar:" in text and "Terminal 1" in text


def test_resume_sem_tarefas(project_dir):
    cadastrar(project_dir)
    code, text = run("resume", "demo", "--offline")
    assert "(nenhuma)" in text


def test_exportar_e_importar(project_dir, tmp_path):
    cadastrar(project_dir)
    code, text = run("export", "demo")
    assert code == 0 and (project_dir / ".dev-orchestra" / "projeto.toml").is_file()
    run("projects", "remove", "demo", "--yes")

    outra = tmp_path / "outra"
    outra.mkdir()
    (outra / ".dev-orchestra").mkdir()
    (outra / ".dev-orchestra" / "projeto.toml").write_text(
        (project_dir / ".dev-orchestra" / "projeto.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    code, text = run("import", "--path", str(outra), "--subscription", "developer=codex-trabalho")
    assert code == 0 and "importado" in text and "orchestrator" in text  # orquestrador ainda sem assinatura
    imported = config.load_project("demo")
    assert imported.path == outra and imported.roles["developer"].subscription == "codex-trabalho"


def test_importar_sem_arquivo(tmp_path, capsys):
    code, _ = run("import", "--path", str(tmp_path))
    assert code == 2 and "não encontrado" in capsys.readouterr().err


def test_importar_duplicado_exige_force(project_dir, tmp_path, capsys):
    cadastrar(project_dir)
    run("export", "demo")
    code, _ = run("import", "--path", str(project_dir))
    assert code == 2 and "já existe" in capsys.readouterr().err
    code, _ = run("import", "--path", str(project_dir), "--force")
    assert code == 0


def test_render_table_vazia():
    assert cli.render_table(["A", "B"], []) == "A  B"
