"""Camadas de dados e widgets dos formulários: catálogos das CLIs, completar caminho, sessões do Claude, chips."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from textual.app import App
from textual.widgets import Button, Input, Select, Static

from dev_orchestra.core import catalog, config, pathhints, sessions
from dev_orchestra.core.liveness import ClaudeSession, Services
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.fields import ChoiceChips, FolderPicker, ModelInput, PathInput, session_picker
from dev_orchestra.tui.forms import ProjectFormScreen

SIZE = (200, 70)

CODEX_JSON = json.dumps(
    {
        "models": [
            {"slug": "gpt-6-luna", "description": "rápido", "visibility": "list", "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "ultra"}]},
            {"slug": "interno", "visibility": "hide", "supported_reasoning_levels": []},
        ]
    }
)  # fmt: skip


ORIGINAL_CATALOG_FOR = catalog.catalog_for


@pytest.fixture(autouse=True)
def real_catalogs(monkeypatch):
    """Este arquivo testa os catálogos de verdade (o conftest os fixa nos demais testes).

    Não usar `monkeypatch.undo()`: desfaria também o isolamento da pasta de configuração."""
    monkeypatch.setattr(catalog, "catalog_for", ORIGINAL_CATALOG_FOR)
    catalog.clear_cache()


def runner(table):
    def run(args, timeout=10.0):
        for key, value in table.items():
            if key in " ".join(args):
                return value
        return 1, ""

    return run


# ------------------------------------------------------------------- catálogo


def test_esforcos_do_claude_vem_do_help_com_reserva():
    help_text = "  --effort <level>   Effort level for the session (low, medium, high, xhigh, max)\n"
    assert catalog.claude_efforts(runner({"claude --help": (0, help_text)})) == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert catalog.claude_efforts(runner({"claude --help": (0, "sem nada")})) == catalog.CLAUDE_EFFORTS
    assert catalog.claude_efforts(runner({})) == catalog.CLAUDE_EFFORTS


def test_catalogo_do_codex_filtra_ocultos_e_mostra_esforcos_do_modelo():
    cat = catalog.codex_catalog(runner({"codex debug models": (0, CODEX_JSON)}))
    assert cat.slugs() == ["gpt-6-luna"]
    assert cat.efforts_for("gpt-6-luna") == ["low", "medium", "ultra"]
    assert cat.efforts_for("modelo-que-nao-existe") == ["low", "medium", "ultra"]  # gerais da CLI
    assert cat.default_effort("gpt-6-luna") == "medium"


def test_catalogo_do_codex_ilegivel_ou_indisponivel_nao_quebra():
    assert catalog.codex_catalog(runner({"codex": (1, "")})).models == []
    bad = catalog.codex_catalog(runner({"codex debug models": (0, "{nao e json")}))
    assert bad.models == [] and "ilegível" in bad.note


def test_catalogo_do_opencode_e_cache():
    calls = []

    def run(args, timeout=10.0):
        calls.append(args)
        return 0, "anthropic/claude-x\nopenai/gpt-y\nlixo sem barra\n"

    assert catalog.catalog_for("opencode", run=run).slugs() == ["anthropic/claude-x", "openai/gpt-y"]
    catalog.catalog_for("opencode", run=run)
    assert len(calls) == 1
    catalog.catalog_for("opencode", refresh=True, run=run)
    assert len(calls) == 2
    assert catalog.catalog_for("kimi").models == []


# -------------------------------------------------------------------- caminhos


def test_completar_caminho_so_pastas_com_marcadores(tmp_path):
    (tmp_path / "alfa").mkdir()
    (tmp_path / "alfa" / ".git").mkdir()
    (tmp_path / "alfa2").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / ".oculta").mkdir()
    (tmp_path / "arquivo.txt").write_text("x")
    found = pathhints.complete(f"{tmp_path}/al")
    assert [s.name for s in found] == ["alfa", "alfa2"] and found[0].markers == ("GIT",)
    assert [s.name for s in pathhints.complete(f"{tmp_path}/")] == ["alfa", "alfa2", "beta"] or len(
        pathhints.complete(f"{tmp_path}/")
    ) == 3
    assert ".oculta" in [s.name for s in pathhints.complete(f"{tmp_path}/", hidden=True)]
    assert pathhints.complete(f"{tmp_path}/zzz") == [] and pathhints.complete("/nao/existe/aqui") == []


def test_tab_completa_ate_onde_concordam(tmp_path):
    for name in ("projeto-a", "projeto-b", "outro"):
        (tmp_path / name).mkdir()
    typed = f"{tmp_path}/pro"
    assert pathhints.common_completion(typed, pathhints.complete(typed)) == f"{tmp_path}/projeto-"
    typed = f"{tmp_path}/out"
    assert pathhints.common_completion(typed, pathhints.complete(typed)) == f"{tmp_path}/outro/"
    assert pathhints.common_completion("/nada", []) == "/nada"


def test_descrever_pasta(tmp_path):
    assert pathhints.describe_folder(tmp_path / "nao-existe") == {"exists": False}
    (tmp_path / "AGENTS.md").write_text("a\nb\n")
    (tmp_path / ".ai-memory.toml").write_text("")
    info = pathhints.describe_folder(tmp_path)
    assert (
        info["exists"]
        and info["agents_md"]
        and info["ai_memory_toml"]
        and not info["git"]
        and info["agents_lines"] == 2
    )


# ---------------------------------------------------------------------- sessões


def write_session(root, project, sid, lines):
    folder = root / sessions.encode_path(project)
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{sid}.jsonl"
    file.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return file


def test_ler_sessao_pega_titulo_modelo_e_mensagens(tmp_path):
    root = tmp_path / "claude"
    project = tmp_path / "meu proj"
    project.mkdir()
    write_session(root, project, "aaa111", [
        {"type": "user", "timestamp": "2026-10-22T10:00:00Z", "message": {"content": "<x>oi</x> primeira mensagem"}},
        {"type": "assistant", "timestamp": "2026-10-22T10:01:00Z", "message": {"model": "claude-opus-5-5", "content": []}},
        {"customTitle": "meu-bot-dev", "type": "custom-title"},
        {"type": "user", "timestamp": "2026-10-23T09:00:00Z", "message": {"content": [{"type": "text", "text": "última pergunta"}]}},
    ])  # fmt: skip
    (info,) = sessions.list_sessions(project, Services(claude_sessions=lambda: []), root=root)
    assert info.id == "aaa111" and info.title == "meu-bot-dev" and info.model == "opus-5-5"
    assert info.first_message == "oi primeira mensagem" and info.last_message == "última pergunta"
    assert info.last and info.last.date() == datetime(2026, 10, 23).date() and info.open_pid is None


def test_sessao_aberta_e_marcada_e_ordem_do_mais_recente(tmp_path):
    root = tmp_path / "claude"
    project = tmp_path / "p"
    project.mkdir()
    write_session(
        root, project, "velha", [{"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "a"}}]
    )
    write_session(
        root, project, "nova", [{"type": "user", "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "b"}}]
    )
    live = [ClaudeSession(4242, str(project), "x", "velha", "idle", "interactive")]
    found = sessions.list_sessions(project, Services(claude_sessions=lambda: live), root=root)
    assert [s.id for s in found] == ["nova", "velha"] and [s.open_pid for s in found] == [None, 4242]
    assert sessions.list_sessions(tmp_path / "outro", None, root=root) == []


def test_tamanho_legivel():
    f = lambda n: sessions.SessionInfo("i", "t", n, None, None, None).size_text  # noqa: E731
    assert f(2048) == "2 KB" and f(5 * 1_048_576) == "5,0 MB" and f(62 * 1_048_576) == "62 MB"


# ----------------------------------------------------------------------- widgets


class Host(App):
    def compose(self):
        yield ChoiceChips(["low", "medium", "high"], "medium", id="chips")
        yield PathInput(id="path")
        yield ModelInput(id="model")

    def on_choice_chips_changed(self, event):
        self.changed = event.value


async def test_chips_setas_limpar_e_troca_de_opcoes():
    app = Host()
    async with app.run_test() as pilot:
        chips = app.query_one("#chips", ChoiceChips)
        chips.focus()
        await pilot.press("right")
        assert chips.value == "high" and app.changed == "high"
        await pilot.press("right")
        assert chips.value == "high"  # no limite
        await pilot.press("backspace")
        assert chips.value is None
        chips.set_options(["low", "xhigh"])
        chips.set_value("xhigh")
        assert chips.value == "xhigh"
        chips.set_options(["low"])  # o valor deixou de existir
        assert chips.value is None


async def test_tab_completa_o_caminho_e_ctrl_o_pede_o_navegador(tmp_path):
    (tmp_path / "meu-projeto").mkdir()
    app = Host()
    async with app.run_test() as pilot:
        field = app.query_one("#path", PathInput)
        field.focus()
        field.value = f"{tmp_path}/meu"
        await pilot.press("tab")
        assert field.value == f"{tmp_path}/meu-projeto/"
        assert await field.suggester.get_suggestion(f"{tmp_path}/meu") == f"{tmp_path}/meu-projeto/"


async def test_navegador_de_pastas_lista_e_devolve_o_caminho(tmp_path):
    (tmp_path / "um").mkdir()
    (tmp_path / "dois").mkdir()
    (tmp_path / "dois" / ".git").mkdir()
    app = DevOrchestraApp()
    result = []
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(FolderPicker(f"{tmp_path}/"), result.append)
        await pilot.pause()
        picker = app.screen
        assert picker.query_one("#fp-list").option_count == 2
        assert "existe" in str(picker.query_one("#fp-info", Static).render())
        picker.query_one("#fp-input", Input).value = f"{tmp_path}/d"
        await pilot.press("tab")
        assert picker.query_one("#fp-input", Input).value == f"{tmp_path}/dois/"
        await pilot.press("enter")
        await pilot.pause()
    assert result == [f"{tmp_path}/dois"]


# ----------------------------------------------------------- formulário de projeto


def form_services(live=None):
    return Services(
        claude_sessions=lambda: live or [],
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [],
    )


async def test_formulario_esforco_segue_o_catalogo_da_cli(project_dir, monkeypatch):
    catalog.clear_cache()
    fake = catalog.Catalog(
        "codex", [catalog.ModelInfo("gpt-6-luna", "", "medium", ("low", "medium", "ultra"))], ["low", "ultra"]
    )
    monkeypatch.setattr(catalog, "codex_catalog", lambda run=None: fake)
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ProjectFormScreen)
        chips = form.query_one("#r-developer-effort", ChoiceChips)
        assert chips.options == []  # sem CLI não há esforço
        form.query_one("#r-developer-cli", Select).value = "codex"
        await pilot.pause()
        assert chips.options == ["low", "ultra"]  # sem modelo: os esforços gerais da CLI
        form.query_one("#r-developer-model", Input).value = "gpt-6-luna"
        await pilot.pause()
        assert chips.options == ["low", "medium", "ultra"]  # com modelo: os dele
        form.query_one("#r-developer-cli", Select).value = "claude"
        await pilot.pause()
        assert chips.options == catalog.CLAUDE_EFFORTS


async def test_formulario_adota_sessao_e_guarda_permissao(project_dir, tmp_path, monkeypatch):
    root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    (root / "projects").mkdir(parents=True)
    write_session(root / "projects", project_dir, "20500d68-aaaa", [
        {"type": "user", "timestamp": "2026-10-22T10:00:00Z", "message": {"content": "olá"}},
        {"customTitle": "meu-bot-dev", "type": "custom-title"},
    ])  # fmt: skip
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#f-id", Input).value = "trade"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-orchestrator-cli", Select).value = "claude"
        form.query_one("#r-developer-cli", Select).value = "claude"
        form.query_one("#r-qa-cli", Select).value = "claude"
        form.query_one("#r-qa-perm", Select).value = "read-only"
        await pilot.pause()
        form.query_one("#r-developer-session", Button).press()
        await pilot.pause()
        assert app.screen.query_one("#picker-list").option_count == 1
        await pilot.press("enter")
        await pilot.pause()
        assert "20500d68" in str(form.query_one("#r-developer-session-info", Static).render())
        await pilot.click("#save")
        await pilot.pause()
    p = config.load_project("trade")
    dev = p.roles["developer"]
    assert dev.resume_origin == "20500d68-aaaa" and dev.resume_fork is True
    assert (
        dev.resume != "20500d68-aaaa"
        and (root / "projects" / sessions.encode_path(project_dir) / f"{dev.resume}.jsonl").is_file()
    )
    assert p.roles["qa"].permission == "read-only" and p.roles["orchestrator"].resume is None
    assert p.roles["orchestrator"].permission is None


async def test_formulario_recusa_sessao_aberta(project_dir, tmp_path, monkeypatch):
    root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    write_session(
        root / "projects",
        project_dir,
        "abc",
        [{"type": "user", "timestamp": "2026-10-22T10:00:00Z", "message": {"content": "x"}}],
    )
    live = [ClaudeSession(77, str(project_dir), "n", "abc", "idle", "interactive")]
    app = DevOrchestraApp(form_services(live))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-developer-cli", Select).value = "claude"
        await pilot.pause()
        form.query_one("#r-developer-session", Button).press()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert form._resume["developer"] is None  # aberta em outro terminal: não adota


async def test_editar_preserva_sessao_e_permissao(project_dir):
    from dev_orchestra.core.models import Project, Role

    p = Project(id="ed", name="Ed", path=project_dir, roles={"orchestrator": Role(cli="claude"),
                "developer": Role(cli="claude", resume="uuid-1", resume_fork=False, permission="plan")})  # fmt: skip
    config.save_project(p)
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(ProjectFormScreen(p))
        await pilot.pause()
        form = app.screen
        assert form.query_one("#r-developer-perm", Select).value == "plan"
        await pilot.click("#save")
        await pilot.pause()
    saved = config.load_project("ed").roles["developer"]
    assert saved.resume == "uuid-1" and saved.resume_fork is False and saved.permission == "plan"


def test_seletor_de_sessao_mostra_aberta_e_tamanho():
    info = sessions.SessionInfo("abcdef123456", "meu-bot-dev", 62 * 1_048_576, None, None, "opus-5-5", "oi", open_pid=9)
    screen = session_picker([info])
    assert "aberta" in screen._options[0][0].plain and "62 MB" in screen._options[0][0].plain


async def test_modelo_f2_abre_catalogo_e_preenche(project_dir):
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#r-developer-cli", Select).value = "claude"
        await pilot.pause()
        model = form.query_one("#r-developer-model", ModelInput)
        model.focus()
        await pilot.press("f2")
        await pilot.pause()
        assert app.screen.query_one("#picker-list").option_count == len(catalog.CLAUDE_MODELS)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert model.value in catalog.CLAUDE_MODELS[1][0:1] + ("opus",)


async def test_botao_de_modelos_abre_o_catalogo_sem_precisar_de_f2(project_dir):
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#r-developer-cli", Select).value = "claude"
        await pilot.pause()
        form.query_one("#r-developer-models", Button).press()
        await pilot.pause()
        assert app.screen.query_one("#picker-list").option_count == len(catalog.CLAUDE_MODELS)
        await pilot.press("enter")
        await pilot.pause()
        assert form.query_one("#r-developer-model", Input).value == catalog.CLAUDE_MODELS[0][0]


# ------------------------------------------------- especialistas, perfil e delegador


async def test_especialista_tem_permissao_sessao_e_vira_cadastro(project_dir):
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#f-id", Input).value = "trade"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-orchestrator-cli", Select).value = "claude"
        form.query_one("#add-specialist", Button).press()  # botão -> pergunta o nome
        await pilot.pause()
        await pilot.press(*"analyst", "enter")
        await pilot.pause(0.5)
        assert "analyst" in form.role_ids
        form.query_one("#r-analyst-model", Input).value = "sonnet"
        form.query_one("#r-analyst-perm", Select).value = "read-only"
        await pilot.pause()
        assert "perfil padrão" in str(form.query_one("#r-analyst-profile-info", Static).render())
        await pilot.click("#save")
        await pilot.pause()
    saved = config.load_project("trade").roles["analyst"]
    assert saved.cli == "claude" and saved.default_model == "sonnet" and saved.permission == "read-only"


async def test_nome_de_especialista_invalido_ou_repetido_e_recusado(project_dir):
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form._ask_specialist()
        await pilot.pause()
        await pilot.press(*"Dev", "enter")  # vira "dev": válido
        await pilot.pause(0.4)
        assert "dev" in form.role_ids
        form._ask_specialist()
        await pilot.pause()
        await pilot.press(*"dev", "enter")  # repetido
        await pilot.pause(0.4)
        assert form.role_ids.count("dev") == 1
        form.remove_specialist("dev")
        await pilot.pause()
        assert "dev" not in form.role_ids and not form.query("#rc-dev")


async def test_editar_projeto_mostra_especialista_existente_com_permissao(project_dir):
    from dev_orchestra.core.models import Project, Role

    p = Project(
        id="ed2",
        name="Ed2",
        path=project_dir,
        roles={"orchestrator": Role(cli="claude"), "analyst": Role(cli="claude", permission="read-only")},
    )
    config.save_project(p)
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(ProjectFormScreen(p))
        await pilot.pause(0.4)
        form = app.screen
        assert form.role_ids[-1] == "analyst"
        assert form.query_one("#r-analyst-perm", Select).value == "read-only"
        await pilot.click("#save")
        await pilot.pause()
    assert config.load_project("ed2").roles["analyst"].permission == "read-only"


async def test_resumo_avisa_modelo_fora_do_catalogo(project_dir):
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#f-id", Input).value = "m"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-orchestrator-cli", Select).value = "claude"
        form.query_one("#r-orchestrator-model", Input).value = "Opus 5.5"
        await pilot.pause()
        text = str(form.query_one("#pf-summary", Static).render())
        assert "Opus 5.5" in text and "não está no catálogo" in text
        form.query_one("#r-orchestrator-model", Input).value = "opus"
        await pilot.pause()
        assert "não está no catálogo" not in str(form.query_one("#pf-summary", Static).render())


async def test_botao_de_perfil_abre_a_tela_e_avisa_sem_delegador(project_dir):
    from dev_orchestra.tui.profile_form import ProfileScreen

    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = app.screen
        form.query_one("#f-id", Input).value = "pp"
        form.query_one("#f-path", Input).value = str(project_dir)
        form.query_one("#r-developer-cli", Select).value = "claude"
        await pilot.pause()
        form.query_one("#r-developer-profile", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, ProfileScreen)


def test_checks_do_delegador_apontam_o_que_falta(project):
    from types import SimpleNamespace

    from dev_orchestra.delegator import setup

    pend = SimpleNamespace(changes=["hook Stop"], mcp="registrado (local)")
    checks = setup.project_checks(
        project, "http://x", container_state=lambda: None, health=lambda u: False, install_dry=lambda p, u: pend
    )
    by = {c.key: c for c in checks}
    assert by["mode"].ok is False and by["mode"].fix == "edit"
    assert by["container"].ok is False and by["container"].fix == "up" and by["service"].fix == "up"
    assert by["install"].ok is False and by["install"].fix == "install" and "hook Stop" in by["install"].detail
    assert not setup.ready(checks)
    project.delegator = True
    good = SimpleNamespace(changes=[], mcp="já registrado")
    ok = setup.project_checks(
        project, "http://x", container_state=lambda: "running", health=lambda u: True, install_dry=lambda p, u: good
    )
    assert setup.ready(ok) and all(c.ok is not False for c in ok)


async def test_tela_preparar_delegador_resolve_com_botoes(project):
    from dev_orchestra.delegator import setup
    from dev_orchestra.tui.delegator_setup import DelegatorSetupScreen

    project.delegator = True
    state = {"up": False, "installed": False}

    def checker(p, u):
        return [
            setup.Check(
                "container",
                "Container",
                state["up"],
                "ok" if state["up"] else "não existe",
                "" if state["up"] else "up",
            ),
            setup.Check(
                "install",
                "Hooks",
                state["installed"],
                "em dia" if state["installed"] else "faltam",
                "" if state["installed"] else "install",
            ),
        ]

    def starter():
        state["up"] = True
        return ["Container iniciado."]

    def installer(p, u):
        state["installed"] = True
        return "settings.json atualizado"

    result = []
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(DelegatorSetupScreen(project, checker, starter, installer), result.append)
        await pilot.pause(0.5)
        screen = app.screen
        assert not screen.query_one("#ds-up", Button).disabled and not screen.query_one("#ds-install", Button).disabled
        screen.query_one("#ds-up", Button).press()
        await app.workers.wait_for_complete()
        await pilot.pause(0.3)
        assert (
            state["up"]
            and screen.query_one("#ds-up", Button).disabled
            and not screen.query_one("#ds-install", Button).disabled
        )
        screen.query_one("#ds-install", Button).press()
        await app.workers.wait_for_complete()
        await pilot.pause(0.3)
        assert "Pronto" in str(screen.query_one("#ds-status", Static).render())
        await pilot.press("escape")
        await pilot.pause()
    assert result == [True]


# ----------------------------------------------------------- cópia de sessão (fork feito pela ferramenta)


def make_base(tmp_path, base="aaaa1111-2222-3333-4444-555566667777"):
    root = tmp_path / "claude"
    project = tmp_path / "proj"
    project.mkdir()
    file = write_session(root, project, base, [
        {"type": "user", "sessionId": base, "timestamp": "2026-10-22T10:00:00Z", "message": {"content": "olá"}},
        {"type": "assistant", "sessionId": base, "message": {"model": "claude-opus-5-5", "content": []}, "ref": f"x/{base}/y"},
    ])  # fmt: skip
    return root, project, file, base


def test_fork_copia_com_id_novo_e_nao_toca_na_original(tmp_path):
    root, project, file, base = make_base(tmp_path)
    (file.parent / base).mkdir()
    (file.parent / base / "tool-results.txt").write_text("saida")
    original = file.read_bytes()
    new_id = (
        sessions.fork_session(project, base, root=root / "projects")
        if False
        else sessions.fork_session(project, base, root=root)
    )
    copy = file.parent / f"{new_id}.jsonl"
    assert new_id != base and copy.is_file() and file.read_bytes() == original  # original intacta
    text = copy.read_text()
    assert base not in text and text.count(new_id) == 3  # todo id da original foi trocado pelo da cópia
    assert oct(copy.stat().st_mode & 0o777) == "0o600"
    assert (file.parent / new_id / "tool-results.txt").read_text() == "saida"  # subpastas da sessão vão junto


def test_fork_de_sessao_inexistente_e_erro_claro(tmp_path):
    root, project, _file, _base = make_base(tmp_path)
    with pytest.raises(sessions.SessionError, match="não encontrada"):
        sessions.fork_session(project, "nao-existe", root=root)


def test_materializar_cria_so_as_copias_pendentes(tmp_path):
    from dev_orchestra.core.models import Project, Role

    root, project, _file, base = make_base(tmp_path)
    p = Project(id="m", name="M", path=project, roles={
        "developer": Role(cli="claude", resume=base, resume_fork=True),
        "qa": Role(cli="claude", resume=base, resume_fork=False),  # sem cópia: usa a original
        "planner": Role(cli="claude", resume="copia-x", resume_fork=True, resume_origin="orig-y"),  # já copiada
        "orchestrator": Role(cli="claude"),
    })  # fmt: skip
    notes = sessions.materialize_forks(p, root=root)
    assert len(notes) == 1 and "developer" in notes[0]
    assert p.roles["developer"].resume_origin == base and p.roles["developer"].resume != base
    assert p.roles["qa"].resume == base and p.roles["qa"].resume_origin is None
    assert p.roles["planner"].resume == "copia-x"


async def test_editar_nao_cria_outra_copia_e_nova_sessao_descarta_a_antiga(project_dir, tmp_path, monkeypatch):
    from dev_orchestra.core.models import Project, Role

    root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    base = "bbbb1111-aaaa"
    write_session(
        root / "projects",
        project_dir,
        base,
        [{"type": "user", "timestamp": "2026-10-22T10:00:00Z", "message": {"content": "x"}}],
    )
    p = Project(
        id="cp",
        name="Cp",
        path=project_dir,
        roles={"orchestrator": Role(cli="claude"), "developer": Role(cli="claude", resume=base, resume_fork=True)},
    )
    config.save_project(p)
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(ProjectFormScreen(p))
        await pilot.pause(0.4)
        await pilot.click("#save")  # 1º salvar: cria a cópia
        await pilot.pause(0.5)
    first = config.load_project("cp").roles["developer"]
    assert first.resume_origin == base and first.resume != base
    folder = root / "projects" / sessions.encode_path(project_dir)
    assert len(list(folder.glob("*.jsonl"))) == 2
    app = DevOrchestraApp(form_services())
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(ProjectFormScreen(config.load_project("cp")))
        await pilot.pause(0.4)
        assert "cópia" in str(app.screen.query_one("#r-developer-session-info", Static).render())
        await pilot.click("#save")  # 2º salvar sem mudar nada: reaproveita a cópia
        await pilot.pause(0.5)
    again = config.load_project("cp").roles["developer"]
    assert again.resume == first.resume and again.resume_origin == base
    assert len(list(folder.glob("*.jsonl"))) == 2  # nenhuma cópia nova
