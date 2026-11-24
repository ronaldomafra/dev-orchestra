"""Gerador de perfis (provedores), perfis do projeto sincronizados com a instalação, CLI e tela."""

from __future__ import annotations

import subprocess

import pytest
from textual.widgets import Button, Select, TextArea

from dev_orchestra.cli import main
from dev_orchestra.core import config, generator, paths, profiles
from dev_orchestra.core.models import Project, Role
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.profile_form import ProfileScreen

GOOD = """# Papel: Analista (demo-analyst)

## Missão
Analisar resultados.

## Responsabilidades
- ler data/

## Não faça
- editar arquivos

## Entregáveis e evidências
Use `report`.

## Ferramentas
Read, Grep.
"""


@pytest.fixture
def proj(tmp_path) -> Project:
    (tmp_path / "AGENTS.md").write_text("# regras do projeto\nnunca operar dinheiro real\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "bot.md").write_text("# Bot de paper trading\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "carteiras.json").write_text("{}")
    p = Project(
        id="demo",
        name="Demo",
        path=tmp_path,
        roles={"orchestrator": Role(cli="claude"), "analyst": Role(cli="claude", permission="read-only")},
    )
    config.save_project(p)
    return p


def fake_run(stdout="", code=0, stderr="", calls=None):
    def run(args, **kw):
        if calls is not None:
            calls.append((args, kw))
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    return run


def test_prompt_leva_agents_docs_e_data_so_quando_pedido(proj):
    prompt = generator.build_prompt(proj, "analyst", "analisar o bot")
    assert "nunca operar dinheiro real" in prompt and "docs/bot.md: Bot de paper trading" in prompt
    assert "carteiras.json" not in prompt
    assert "carteiras.json" in generator.build_prompt(proj, "analyst", "x", data=True)
    assert "dados, não instruções" in prompt


def test_claude_roda_somente_leitura_e_limpa_a_saida(proj):
    calls: list = []
    r = generator.generate(
        "claude", "sonnet", "P", proj, run=fake_run(f"conversa\n```markdown\n{GOOD}```", calls=calls)
    )
    args, kw = calls[0]
    assert r.ok and r.text.startswith("# Papel") and "conversa" not in r.text
    assert "--permission-mode" in args and "plan" in args and "--no-session-persistence" in args
    assert "--allowedTools" in args and "Edit" not in args and "Write" not in args
    assert kw["input"] == "P" and kw["cwd"] == str(proj.path)


def test_codex_e_opencode_usam_modo_seguro(proj):
    calls: list = []
    generator.generate("codex", "gpt-6-luna", "P", proj, run=fake_run(GOOD, calls=calls))
    assert calls[0][0][:2] == ["codex", "exec"] and "read-only" in calls[0][0] and "--ephemeral" in calls[0][0]
    calls.clear()
    r = generator.generate("opencode", "a/b", "P", proj, run=fake_run(GOOD, calls=calls))
    assert calls[0][0][:2] == ["opencode", "run"] and r.ok


def test_ollama_http_e_erros_viram_mensagem(proj):
    r = generator.generate("ollama", "qwen", "P", proj, ollama_post=lambda url, body, t: {"response": GOOD})
    assert r.ok and "11434" not in r.error

    def down(url, body, t):
        raise OSError("recusou")

    assert "Ollama indisponível" in generator.generate("ollama", "q", "P", proj, ollama_post=down).error
    bad = generator.generate("claude", "", "P", proj, run=fake_run("", code=1, stderr="sem login"))
    assert not bad.ok and "sem login" in bad.error
    assert (
        "não encontrado"
        in generator.generate(
            "codex", "", "P", proj, run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
        ).error
    )
    assert "gerador_comando" in generator.generate("generic", "", "P", proj).error
    assert generator.generate("generic", "", "P", proj, run=fake_run(GOOD), generic_command="meu-gerador --x").ok


def test_validacao_das_secoes():
    assert generator.validate_profile(GOOD) == (True, [])
    ok, missing = generator.validate_profile("# Papel: x\n\n## Missão\nfoo")
    assert not ok and "Responsabilidades" in missing and "Ferramentas" in missing


def test_provedores_disponiveis():
    found = {
        i.key: i
        for i in generator.providers(which=lambda n: "/bin/x" if n == "claude" else None, ollama_models=lambda: None)
    }
    assert (
        found["claude"].available
        and not found["codex"].available
        and not found["ollama"].available
        and found["generic"].available
    )


def test_salvar_grava_no_projeto_e_na_instalacao_e_sincroniza(proj):
    source, target = profiles.save_profile(proj, "analyst", GOOD)
    assert source == proj.path / ".dev-orchestra/perfis/analyst.md" and source.read_text().startswith("# Papel")
    assert target == paths.install_dir() / "perfis/demo/analyst.md" and target.read_text() == source.read_text()
    assert [e.origin for e in profiles.list_profiles(proj)] == ["padrão", "projeto"]
    source.write_text(GOOD + "\nmais uma linha\n")
    assert profiles.sync_to_install(proj) == ["analyst"] and "mais uma linha" in target.read_text()
    assert profiles.sync_to_install(proj) == []  # já iguais


def test_delegador_usa_o_perfil_salvo(proj):
    from dev_orchestra.delegator.profiles import profile_for

    profiles.save_profile(proj, "analyst", GOOD)
    assert "Analisar resultados" in profile_for("analyst", "demo", paths.install_dir())


def test_definicao_de_agente_restringe_ferramentas(proj):
    text = profiles.render_agent_def(proj, "analyst", "Analisa o bot", list(profiles.READ_TOOLS))
    assert "tools: Read, Grep, Glob, mcp__dev-orchestra__register_session" in text
    assert "Edit" not in text and "Write" not in text
    path = profiles.write_agent_def(proj, "analyst", "Analisa o bot", ["Read"])
    assert path.is_file()
    with pytest.raises(FileExistsError):
        profiles.write_agent_def(proj, "analyst", "x", ["Read"])


def test_cli_generate_so_grava_com_save(proj, monkeypatch, capsys):
    monkeypatch.setattr(generator, "generate", lambda *a, **k: generator.Generated(text=GOOD, provider="claude"))
    assert main(["profiles", "generate", "demo", "analyst", "--description", "analisar"]) == 0
    assert "nada foi gravado" in capsys.readouterr().out
    assert not (proj.path / ".dev-orchestra/perfis/analyst.md").exists()
    assert main(["profiles", "generate", "demo", "analyst", "--description", "analisar", "--save"]) == 0
    assert (proj.path / ".dev-orchestra/perfis/analyst.md").is_file()
    assert main(["profiles", "list", "demo"]) == 0 and "analyst" in capsys.readouterr().out
    assert main(["profiles", "generate", "demo", "analyst"]) == 2  # sem descrição
    assert main(["profiles", "generate", "demo", "ghost", "--description", "x"]) == 2  # papel inexistente


@pytest.mark.asyncio
async def test_tela_gera_rascunho_revisa_e_so_entao_salva(proj):
    seen = {}

    def fake(provider, model, prompt, project, **kw):
        seen.update(provider=provider, prompt=prompt)
        return generator.Generated(text=GOOD, provider=provider, model=model)

    app = DevOrchestraApp()
    async with app.run_test(size=(180, 55)) as pilot:
        screen = ProfileScreen(proj, "analyst", generate=fake)
        app.push_screen(screen)
        await pilot.pause()
        assert screen.query_one("#save", Button).disabled  # sem rascunho não salva
        screen.query_one("#pg-desc", TextArea).text = "Analisa resultados do bot, só leitura"
        screen.query_one("#pg-provider", Select).value = "generic"
        await pilot.press("ctrl+g")
        for _ in range(40):
            await pilot.pause(0.1)
            if screen.query_one("#pg-draft", TextArea).text:
                break
        assert "Missão" in screen.query_one("#pg-draft", TextArea).text and "Analisa resultados" in seen["prompt"]
        assert not screen.query_one("#save", Button).disabled
        assert not (proj.path / ".dev-orchestra/perfis/analyst.md").exists()  # rascunho não grava nada
        assert screen.query_one("#pg-agentdef").value  # papel somente leitura: marcado por padrão
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert (proj.path / ".dev-orchestra/perfis/analyst.md").is_file()
    agent = (proj.path / ".claude/agents/analyst.md").read_text()
    assert "tools: Read, Grep, Glob, mcp__dev-orchestra__" in agent and "Edit" not in agent


def test_abertura_de_especialista_aponta_para_o_perfil_quando_nao_ha_arquivo_do_papel(proj):
    from dev_orchestra.core.commands import LaunchContext, opening_prompt, role_file
    from dev_orchestra.core.models import GlobalConfig

    assert role_file(proj, "analyst") == "roles/analyst.md"  # nem arquivo nem perfil: o caminho padrão
    profiles.save_profile(proj, "analyst", GOOD)
    assert role_file(proj, "analyst") == ".dev-orchestra/perfis/analyst.md"
    text = opening_prompt(proj, GlobalConfig(), "analyst", "claude-code", LaunchContext(), force=True)
    assert ".dev-orchestra/perfis/analyst.md" in text and "roles/analyst.md" not in text
    (proj.path / "roles").mkdir()
    (proj.path / "roles" / "analyst.md").write_text("# x")
    assert role_file(proj, "analyst") == "roles/analyst.md"  # o arquivo do papel, se existe, tem preferência
    assert role_file(proj, "orchestrator") == "roles/orchestrator.md"


def test_prompt_do_perfil_segue_o_modo_de_comunicacao(proj):
    claude_code = generator.build_prompt(proj, "analyst", "analisar o bot")
    assert "SendMessage" in claude_code and "RETURN_TO" in claude_code and "NÃO cite register_session" in claude_code
    proj.delegator = True
    delegator = generator.build_prompt(proj, "analyst", "analisar o bot")
    assert "ferramenta `report` do delegador" in delegator and "NÃO cite register_session" not in delegator
    # os dois mandam ler o contexto uma vez por sessão, não a cada análise
    assert "UMA vez" in claude_code and "UMA vez" in delegator


def test_prompt_de_quem_le_arquivos_manda_explorar_sozinho_e_nao_lista_arquivos(proj):
    reading = generator.build_prompt(proj, "analyst", "analisar o bot", can_read=True, data=True)
    assert str(proj.path) in reading and "explore-a por conta própria" in reading and "Não leia .env" in reading
    assert "carteiras.json" not in reading  # data/ não é embutido: ele explora
    blind = generator.build_prompt(proj, "analyst", "analisar o bot", can_read=False, data=True)
    assert "explore-a por conta própria" not in blind and "carteiras.json" in blind  # sem leitura: contexto embutido


def test_prompt_nao_cita_orquestrador_e_manda_descobrir_pela_configuracao(proj):
    prompt = generator.build_prompt(proj, "analyst", "analisar o bot")
    assert "NÃO cite nomes de sessões de outros agentes" in prompt and "configuração do projeto" in prompt


def test_caminhos_especificos_relativos_absolutos_e_inexistentes(proj, tmp_path_factory):
    outside_dir = tmp_path_factory.mktemp("fora")
    found, warnings = generator.focus_paths(proj, f"docs/bot.md, data, {outside_dir}, nao/existe")
    assert [p.name for p in found] == ["bot.md", "data", outside_dir.name]
    assert warnings == ["caminho não encontrado: nao/existe"]
    assert generator.outside_project(proj, found) == [outside_dir.resolve()]
    prompt = generator.build_prompt(proj, "analyst", "x", can_read=True, focus=found)
    assert "atenção especial" in prompt and str(found[0]) in prompt


def test_claude_recebe_add_dir_so_para_caminho_fora_do_projeto(proj, tmp_path_factory):
    outside = tmp_path_factory.mktemp("ext")
    calls: list = []
    generator.generate("claude", "", "P", proj, run=fake_run(GOOD, calls=calls), extra_dirs=[outside])
    args = calls[0][0]
    assert "--add-dir" in args and args[args.index("--add-dir") + 1] == str(outside)
    assert args.index("--add-dir") < args.index("--allowedTools")
    calls.clear()
    generator.generate("claude", "", "P", proj, run=fake_run(GOOD, calls=calls))
    assert "--add-dir" not in calls[0][0]


@pytest.mark.asyncio
async def test_tela_de_perfil_explora_sozinha_no_claude_e_valida_caminhos(proj):
    from textual.widgets import Checkbox, Input

    seen = {}

    def fake(provider, model, prompt, project, **kw):
        seen.update(provider=provider, prompt=prompt, extra=kw.get("extra_dirs"))
        return generator.Generated(text=GOOD, provider=provider, model=model)

    app = DevOrchestraApp()
    async with app.run_test(size=(180, 60)) as pilot:
        screen = ProfileScreen(proj, "analyst", generate=fake)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#pg-provider", Select).value = "claude"
        await pilot.pause()
        assert not screen.query_one("#pg-data", Checkbox).display  # claude explora sozinho: sem a caixa de data/
        assert "explora a pasta do projeto sozinho" in str(screen.query_one("#pg-auto").render())
        screen.query_one("#pg-desc", TextArea).text = "Analisa resultados do bot"
        screen.query_one("#pg-focus", Input).value = "nao/existe"
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert "não encontrado" in str(screen.query_one("#pg-status").render()) and not seen  # nada foi gerado
        screen.query_one("#pg-focus", Input).value = "docs/bot.md"
        await pilot.press("ctrl+g")
        for _ in range(40):
            await pilot.pause(0.1)
            if seen:
                break
        assert seen["provider"] == "claude" and "explore-a por conta própria" in seen["prompt"]
        assert "docs/bot.md" in seen["prompt"] and seen["extra"] == []
        screen.query_one("#pg-provider", Select).value = "generic"
        await pilot.pause()
        assert screen.query_one("#pg-data", Checkbox).display  # sem leitura de arquivos: contexto embutido
