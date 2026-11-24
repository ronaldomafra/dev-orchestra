from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from textual.widgets import Input, Select

from dev_orchestra import cli
from dev_orchestra.core import config, contract
from dev_orchestra.core.commands import LaunchContext, build_launch_plan, local_context
from dev_orchestra.core.liveness import Services
from dev_orchestra.core.models import GlobalConfig, Project, Role
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.screens import ProjectScreen

SIZE = (160, 70)


def make(path, **roles) -> Project:
    return Project(id="demo", name="Demo", path=path, roles={k: Role(cli=v) for k, v in roles.items()})


def plano(project, **ctx_kw):
    ctx = local_context(project, workstreams=set(), **ctx_kw)
    return build_launch_plan(project, GlobalConfig(), ctx)


# ----------------------------------------------------------------- scaffold


def test_pasta_vazia_recebe_todos_os_arquivos(project_dir):
    p = make(project_dir, orchestrator="claude", developer="claude")
    report = contract.scaffold(p)
    assert report.kept == [] and report.agents_md == "absent" and not report.needs_merge
    esperados = {e.target for e in contract.applicable(p)}
    assert set(report.created) == esperados
    for entry in contract.applicable(p):
        assert (project_dir / entry.target).read_text(encoding="utf-8") == contract.read_bundled(entry.source)
    assert (project_dir / "AGENTS.md").is_file()
    assert (project_dir / "roles" / "orchestrator.md").is_file()
    assert (project_dir / "docs" / "PROTOCOL.md").is_file()
    assert (project_dir / "templates" / "handoff.md").is_file()
    assert (project_dir / ".claude" / "agents" / "developer.md").is_file()
    assert contract.missing_files(p) == []


def test_segunda_execucao_nao_cria_nem_altera_nada(project_dir):
    p = make(project_dir, orchestrator="claude")
    contract.scaffold(p)
    antes = {f: f.read_bytes() for f in project_dir.rglob("*") if f.is_file()}
    report = contract.scaffold(p)
    assert report.created == [] and len(report.kept) == len(contract.applicable(p))
    assert {f: f.read_bytes() for f in project_dir.rglob("*") if f.is_file()} == antes


def test_so_codex_nao_cria_definicoes_de_agente_do_claude(project_dir):
    p = make(project_dir, orchestrator="codex", developer="codex")
    contract.scaffold(p)
    assert not (project_dir / ".claude").exists()
    assert (project_dir / "roles" / "developer.md").is_file()


def test_arquivos_existentes_nao_sao_sobrescritos_e_so_faltam_os_ausentes(project_dir):
    (project_dir / "roles").mkdir()
    (project_dir / "roles" / "qa.md").write_text("MEU QA PERSONALIZADO", encoding="utf-8")
    (project_dir / "docs").mkdir()
    (project_dir / "docs" / "PROTOCOL.md").write_text("MEU PROTOCOLO", encoding="utf-8")
    p = make(project_dir, orchestrator="claude", qa="claude")
    report = contract.scaffold(p)
    assert (project_dir / "roles" / "qa.md").read_text(encoding="utf-8") == "MEU QA PERSONALIZADO"
    assert (project_dir / "docs" / "PROTOCOL.md").read_text(encoding="utf-8") == "MEU PROTOCOLO"
    assert set(report.kept) == {"roles/qa.md", "docs/PROTOCOL.md"}
    assert "roles/qa.md" not in report.created and "roles/orchestrator.md" in report.created


def test_agents_md_proprio_nao_e_alterado_e_o_modelo_vai_para_o_dev_orchestra(project_dir):
    original = "# Meu projeto\n\nRegras minhas. Não mexa.\n"
    (project_dir / "AGENTS.md").write_text(original, encoding="utf-8")
    p = make(project_dir, orchestrator="claude")
    assert contract.agents_md_state(p) == "needs_merge"
    report = contract.scaffold(p)
    assert (project_dir / "AGENTS.md").read_text(encoding="utf-8") == original
    assert report.needs_merge and report.contract_model == ".dev-orchestra/contrato/AGENTS.md"
    assert (project_dir / ".dev-orchestra" / "contrato" / "AGENTS.md").read_text(
        encoding="utf-8"
    ) == contract.read_bundled("AGENTS.md")
    assert "AGENTS.md" in report.kept
    # segunda execução: continua pedindo a incorporação, sem recriar o modelo
    again = contract.scaffold(p)
    assert again.needs_merge and ".dev-orchestra/contrato/AGENTS.md" not in again.created


def test_agents_md_que_ja_incorpora_o_contrato_nao_pede_nada(project_dir):
    (project_dir / "AGENTS.md").write_text("# X\n- roles/orchestrator.md\n- roles/developer.md\n", encoding="utf-8")
    p = make(project_dir, orchestrator="claude")
    assert contract.agents_md_state(p) == "integrated"
    report = contract.scaffold(p)
    assert not report.needs_merge and report.contract_model is None
    assert not (project_dir / ".dev-orchestra" / "contrato").exists()


def test_simulacao_nao_grava_nada(project_dir):
    p = make(project_dir, orchestrator="claude")
    report = contract.scaffold(p, dry_run=True)
    assert report.created and list(project_dir.iterdir()) == []


def test_pasta_inexistente(tmp_path):
    p = make(tmp_path / "nao-existe", orchestrator="claude")
    with pytest.raises(FileNotFoundError):
        contract.scaffold(p)


def test_arquivos_faltando_sao_listados(project_dir):
    p = make(project_dir, orchestrator="claude")
    assert "roles/orchestrator.md" in contract.missing_files(p) and "AGENTS.md" in contract.missing_files(p)
    contract.scaffold(p)
    assert contract.missing_files(p) == []


def test_relatorio_em_texto(project_dir):
    (project_dir / "AGENTS.md").write_text("# meu\n", encoding="utf-8")
    text = contract.format_report(contract.scaffold(make(project_dir, orchestrator="claude")))
    assert "criados" in text and "NÃO foi alterado" in text and ".dev-orchestra/contrato/AGENTS.md" in text


# ------------------------------------------------------- texto de abertura


def test_prompt_do_orquestrador_lista_os_caminhos_de_referencia(project_dir):
    p = make(project_dir, orchestrator="claude", developer="claude", qa="claude")
    contract.scaffold(p)
    prompt = plano(p).blocks[0].prompt
    esperado = "Arquivos de referência do método neste projeto: AGENTS.md; "
    assert esperado + "roles/orchestrator.md, roles/developer.md, roles/qa.md;" in prompt
    assert "docs/PROTOCOL.md" in prompt and "docs/MODEL-SELECTION.md" in prompt
    assert "templates/handoff.md" in prompt and "templates/result.md" in prompt
    assert "ATENÇÃO" not in prompt


def test_prompt_pede_a_incorporacao_quando_ha_agents_md_proprio(project_dir):
    (project_dir / "AGENTS.md").write_text("# meu\n", encoding="utf-8")
    p = make(project_dir, orchestrator="claude", developer="codex")
    contract.scaffold(p)
    plan = plano(p)
    orquestrador, developer = plan.blocks[1], plan.blocks[2]  # o bloco 0 é o App Server
    prompt = orquestrador.prompt
    assert prompt.startswith("ATENÇÃO — AGENTS.md")
    assert "NÃO foi alterado" in prompt and ".dev-orchestra/contrato/AGENTS.md" in prompt
    assert "roles/orchestrator.md" in prompt and "templates/result.md" in prompt
    assert "preservando todo o conteúdo atual" in prompt
    assert "só grave depois da minha aprovação" in prompt
    assert ".dev-orchestra/contrato/AGENTS.md" in developer.prompt  # o worker é avisado


def test_worker_claude_com_definicao_de_agente_so_recebe_o_aviso_quando_necessario(project_dir):
    (project_dir / "AGENTS.md").write_text("# meu\n", encoding="utf-8")
    p = make(project_dir, orchestrator="claude", developer="claude")
    contract.scaffold(p)
    developer = plano(p).blocks[1]
    assert "--agent developer" in developer.commands[1]
    assert developer.prompt and ".dev-orchestra/contrato/AGENTS.md" in developer.prompt
    # com o contrato já incorporado, o handoff basta (sem texto de abertura)
    (project_dir / "AGENTS.md").write_text("# meu\n- roles/orchestrator.md\n", encoding="utf-8")
    assert plano(p).blocks[1].prompt is None


def test_avisos_do_plano_para_arquivos_faltando_e_agents_md(project_dir):
    (project_dir / "AGENTS.md").write_text("# meu\n", encoding="utf-8")
    p = make(project_dir, orchestrator="claude")
    avisos = plano(p).warnings
    assert any("faltam" in w and "dev-orchestra scaffold demo" in w for w in avisos)
    assert any("não incorpora o contrato" in w for w in avisos)
    contract.scaffold(p)
    avisos = plano(p).warnings
    assert not any("faltam" in w for w in avisos) and any("não incorpora o contrato" in w for w in avisos)


def test_contexto_manual_nao_gera_avisos_de_arquivos(project_dir):
    p = make(project_dir, orchestrator="claude")
    plan = build_launch_plan(p, GlobalConfig(), LaunchContext(workstreams=set(), ai_memory_toml=True))
    assert plan.warnings == []


# ------------------------------------------------------------- cópia embutida


def test_copia_embutida_acompanha_o_repositorio():
    """Se este teste falhar: `cd tool && uv run python scripts/sync_contract.py`."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "AGENTS.md").is_file() or not (repo / "roles").is_dir():
        pytest.skip("fora do repositório dev-orchestra")
    sources = {
        "AGENTS.md": "AGENTS.md",
        **{f"roles/{r}.md": f"roles/{r}.md" for r in contract.ROLE_FILES},
        "docs/PROTOCOL.md": "docs/PROTOCOL.md",
        "docs/MODEL-SELECTION.md": "docs/MODEL-SELECTION.md",
        "docs/ADAPTER-CLAUDE-CODE.md": "docs/ADAPTER-CLAUDE-CODE.md",
        "docs/ADAPTER-CODEX.md": "docs/ADAPTER-CODEX.md",
        "docs/DELEGATOR.md": "docs/DELEGATOR.md",
        "templates/handoff.md": "templates/handoff.md",
        "templates/result.md": "templates/result.md",
        **{f"claude-agents/{a}.md": f"adapters/claude-code/agents/{a}.md" for a in ("developer", "planner", "qa")},
    }
    assert {e.source for e in contract.ENTRIES} == set(sources)
    for bundled, original in sources.items():
        assert contract.read_bundled(bundled) == (repo / original).read_text(encoding="utf-8"), bundled


# ---------------------------------------------------------------------- CLI


def run(*argv):
    out = io.StringIO()
    code = cli.main(list(argv), out=out)
    return code, out.getvalue()


def test_cli_cadastrar_cria_os_arquivos(project_dir):
    code, text = run("projects", "add", "--id", "demo", "--path", str(project_dir), "--role", "orchestrator=claude")
    assert code == 0 and "Arquivos do método" in text
    assert (project_dir / "AGENTS.md").is_file() and (project_dir / "roles" / "orchestrator.md").is_file()


def test_cli_cadastrar_sem_scaffold(project_dir):
    code, _ = run(
        "projects", "add", "--id", "demo", "--path", str(project_dir), "--role", "orchestrator=claude", "--no-scaffold"
    )
    assert code == 0 and not (project_dir / "AGENTS.md").exists()


def test_cli_scaffold_completa_e_simula(project_dir):
    run("projects", "add", "--id", "demo", "--path", str(project_dir), "--role", "orchestrator=claude", "--no-scaffold")
    code, text = run("scaffold", "demo", "--dry-run")
    assert "simulação" in text and not (project_dir / "AGENTS.md").exists()
    (project_dir / "roles").mkdir()
    (project_dir / "roles" / "qa.md").write_text("meu", encoding="utf-8")
    code, text = run("scaffold", "demo")
    assert code == 0 and "+ AGENTS.md" in text and "+ roles/qa.md" not in text
    assert (project_dir / "roles" / "qa.md").read_text(encoding="utf-8") == "meu"


def test_cli_scaffold_em_projeto_sem_pasta(project_dir, capsys):
    run("projects", "add", "--id", "demo", "--path", str(project_dir), "--role", "orchestrator=claude", "--no-scaffold")
    shutil.rmtree(project_dir)
    code, _ = run("scaffold", "demo")
    assert code == 2 and "não existe" in capsys.readouterr().err


def test_cli_up_avisa_quando_faltam_arquivos(project_dir):
    run("projects", "add", "--id", "demo", "--path", str(project_dir), "--role", "orchestrator=claude", "--no-scaffold",
        "--no-delegator")  # fmt: skip
    code, text = run("up", "demo", "--offline")
    assert "dev-orchestra scaffold demo" in text
    run("scaffold", "demo")
    code, text = run("up", "demo", "--offline")
    assert "dev-orchestra scaffold" not in text and "Arquivos de referência do método" in text


def test_cli_importar_tambem_cria_os_arquivos(project, project_dir, tmp_path):
    config.save_project(project)
    run("export", "demo")
    config.remove_project("demo")
    outra = tmp_path / "outra"
    outra.mkdir()
    (outra / ".dev-orchestra").mkdir()
    (outra / ".dev-orchestra" / "projeto.toml").write_text(
        (project_dir / ".dev-orchestra" / "projeto.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    code, text = run("import", "--path", str(outra))
    assert code == 0 and "Arquivos do método" in text and (outra / "AGENTS.md").is_file()


# ---------------------------------------------------------------------- TUI


def sem_fontes() -> Services:
    return Services(
        claude_sessions=lambda: [],
        codex_threads=lambda u, c: [],
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [],
    )


async def test_tui_salvar_projeto_cria_os_arquivos(project_dir):
    app = DevOrchestraApp(sem_fontes())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#f-id", Input).value = "novo"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-orchestrator-cli", Select).value = "claude"
        await pilot.click("#save")
        await pilot.pause()
    assert (project_dir / "AGENTS.md").is_file()
    assert (project_dir / "roles" / "orchestrator.md").is_file()
    assert (project_dir / "docs" / "PROTOCOL.md").is_file()
    assert not (project_dir / "roles" / "developer.md").exists()  # só os papéis em uso são copiados


async def test_tui_tecla_f_completa_so_o_que_falta(project, project_dir):
    config.save_project(project)
    contract.scaffold(project)
    (project_dir / "roles" / "developer.md").unlink()
    (project_dir / "roles" / "orchestrator.md").write_text("EDITADO", encoding="utf-8")
    app = DevOrchestraApp(sem_fontes())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, ProjectScreen)
        await pilot.press("f")
        await pilot.pause()
    assert (project_dir / "roles" / "developer.md").is_file()
    assert (project_dir / "roles" / "orchestrator.md").read_text(encoding="utf-8") == "EDITADO"


def test_so_copia_os_papeis_e_adapters_em_uso(project_dir):
    p = make(project_dir, orchestrator="claude", developer="codex", analyst="claude")
    report = contract.scaffold(p, dry_run=True)
    created = set(report.created)
    assert {
        "roles/orchestrator.md",
        "roles/developer.md",
        "docs/ADAPTER-CLAUDE-CODE.md",
        "docs/ADAPTER-CODEX.md",
    } <= created
    assert not {"roles/qa.md", "roles/planner.md", "roles/social-media.md"} & created
    assert ".claude/agents/developer.md" not in created  # developer é Codex: não há definição do Claude
    only_claude = contract.scaffold(make(project_dir, orchestrator="claude", developer="claude"), dry_run=True)
    assert "docs/ADAPTER-CODEX.md" not in only_claude.created and ".claude/agents/developer.md" in only_claude.created
    delegator = Project(id="d", name="d", path=project_dir, roles={"orchestrator": Role(cli="claude")}, delegator=True)
    assert "docs/DELEGATOR.md" in contract.scaffold(delegator, dry_run=True).created


def test_metodo_proprio_com_delegador_recebe_so_o_contrato_do_delegador(project, project_dir):
    from dev_orchestra.core import commands

    (project_dir / "AGENTS.md").write_text("# Meu método\nHandoffs por SendMessage.\n", encoding="utf-8")
    own = project.model_copy(update={"method": "proprio", "delegator": True})
    assert contract.missing_files(own) == ["docs/DELEGATOR.md"]
    report = contract.scaffold(own)
    assert report.own_method and report.created == ["docs/DELEGATOR.md"]
    assert (project_dir / "docs/DELEGATOR.md").is_file()
    assert "contrato do delegador" in contract.format_report(report)
    assert not (project_dir / "roles").exists()  # o resto do método não é copiado
    assert (project_dir / "AGENTS.md").read_text(encoding="utf-8").startswith("# Meu método")  # nada sobrescrito
    ctx = commands.local_context(own)
    assert ctx.own_docs_delegator is False and ctx.missing_files == ()
    text = commands.opening_prompt(own, GlobalConfig(), "orchestrator", commands.MODE_DELEGATOR, ctx)
    assert "documentos do projeto" in text and "docs/DELEGATOR.md tem" in text and "aprovação" in text
    (project_dir / "AGENTS.md").write_text("# Meu método\nComunicação: docs/DELEGATOR.md.\n", encoding="utf-8")
    assert commands.local_context(own).own_docs_delegator is True
    sem = project.model_copy(update={"method": "proprio", "delegator": False})
    assert contract.missing_files(sem) == [] and contract.scaffold(sem).created == []


def test_cli_cria_e_importa_com_delegador_por_padrao(project_dir):
    run("projects", "add", "--id", "demo", "--path", str(project_dir), "--role", "orchestrator=claude", "--no-scaffold")
    assert config.load_project("demo").delegator is True
    args = ["--id", "outro", "--path", str(project_dir), "--role", "orchestrator=claude", "--no-scaffold"]
    run("projects", "add", *args, "--no-delegator")
    assert config.load_project("outro").delegator is False
