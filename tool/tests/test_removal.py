"""Remoção de agente: o que é apagado, o que fica, CLI e TUI."""

from __future__ import annotations

import pytest
from textual.widgets import Button, Select

from dev_orchestra.cli import main
from dev_orchestra.core import config, paths, portable, profiles, removal, sender, state, tasks
from dev_orchestra.core.models import Project, Role
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.forms import ConfirmScreen, ProjectFormScreen
from dev_orchestra.tui.screens import ProjectScreen

SIZE = (200, 70)


@pytest.fixture
def proj(tmp_path) -> Project:
    p = Project(
        id="trade",
        name="Trade",
        path=tmp_path,
        prefix="meu-bot",
        roles={
            "orchestrator": Role(cli="claude"),
            "developer": Role(cli="claude"),
            "bot-analyst": Role(cli="claude", permission="read-only"),
        },
    )
    config.save_project(p)
    return p


def populate(p: Project) -> None:
    profiles.save_profile(p, "bot-analyst", "# Papel: x\n")
    profiles.write_agent_def(p, "bot-analyst", "Analisa", list(profiles.READ_TOOLS))
    sender.instruction_path(p, "bot-analyst").parent.mkdir(parents=True, exist_ok=True)
    sender.instruction_path(p, "bot-analyst").write_text("abertura")
    (p.path / "roles").mkdir()
    (p.path / "roles" / "bot-analyst.md").write_text("# meu papel")
    state.mark_sent(p.id, "meu-bot-bot-analyst", via="x", resume=False)
    state.mark_sent(p.id, "meu-bot-developer", via="x", resume=False)
    state.mark_task_sent(p.id, "T-1", "meu-bot-bot-analyst", via="x")


def test_plano_separa_o_que_apaga_do_que_fica(proj):
    populate(proj)
    plan = removal.plan_removal(proj, "bot-analyst")
    text = plan.text()
    assert not plan.blocked
    for item in (
        "perfil no projeto",
        "cópia do perfil na instalação",
        "instruções geradas",
        "definição de agente gerada",
    ):
        assert item in text
    assert "roles/bot-analyst.md (arquivo do método)" in text and "AI Memory" in text and "auditoria" in text
    assert "registro local de instruções" in text


def test_remover_apaga_so_o_gerado_e_preserva_o_resto(proj):
    populate(proj)
    plan = removal.plan_removal(proj, "bot-analyst")
    done = removal.execute(plan)
    assert "bot-analyst" not in config.load_project("trade").roles and len(done) >= 5
    assert not profiles.profile_path(proj, "bot-analyst").exists()
    assert not profiles.install_path(proj, "bot-analyst").exists()
    assert not profiles.install_path(proj, "bot-analyst").parent.exists()  # pasta vazia some
    assert not sender.instruction_path(proj, "bot-analyst").exists()
    assert not profiles.agent_def_path(proj, "bot-analyst").exists()
    assert (proj.path / "roles" / "bot-analyst.md").read_text() == "# meu papel"  # do método: fica
    info = state.load("trade")
    assert "meu-bot-bot-analyst" not in info["instrucoes"] and "meu-bot-developer" in info["instrucoes"]
    assert "T-1" not in info.get("tarefas", {})


def test_definicao_de_agente_escrita_a_mao_nao_e_apagada(proj):
    path = profiles.agent_def_path(proj, "bot-analyst")
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: bot-analyst\ntools: Read\n---\nfeito a mão\n")
    plan = removal.plan_removal(proj, "bot-analyst")
    assert "não foi gerado por esta ferramenta" in plan.text() and path not in plan.files
    removal.execute(plan)
    assert path.read_text().endswith("feito a mão\n")


def test_definicao_gerada_por_versao_anterior_tambem_e_reconhecida(proj):
    legacy = (
        "---\nname: x\n---\nAo abrir, registre-se no delegador (`register_session`) e siga o perfil devolvido; ...\n"
    )
    assert removal.generated_agent_def(legacy) and not removal.generated_agent_def("feito a mão")


def test_nao_remove_o_unico_papel_nem_papel_inexistente(tmp_path):
    p = Project(id="solo", name="Solo", path=tmp_path, roles={"developer": Role(cli="claude")})
    config.save_project(p)
    assert "único papel" in removal.plan_removal(p, "developer").blocked
    assert "não tem o papel" in removal.plan_removal(p, "ghost").blocked
    with pytest.raises(ValueError):
        removal.execute(removal.plan_removal(p, "developer"))


def test_avisos_de_tarefas_sessao_aberta_e_orquestrador(proj):
    tasks.create_task(proj, "T-9", "Analisar", "bot-analyst")
    text = removal.plan_removal(proj, "bot-analyst", live_session=True).text()
    assert "1 tarefa(s) apontam para o papel (T-9)" in text and "está aberta" in text
    assert "sem orquestrador" in removal.plan_removal(proj, "orchestrator").text()


def test_exportacao_portatil_e_atualizada(proj):
    portable.export_project(proj)
    removal.execute(removal.plan_removal(proj, "bot-analyst"))
    assert "bot-analyst" not in portable.export_path(proj).read_text()


def test_cli_so_mostra_o_plano_sem_yes(proj, capsys):
    populate(proj)
    assert main(["agents", "remove", "trade", "bot-analyst"]) == 0
    assert "nada foi alterado" in capsys.readouterr().out
    assert "bot-analyst" in config.load_project("trade").roles
    assert main(["agents", "remove", "trade", "bot-analyst", "--yes"]) == 0
    assert "bot-analyst" not in config.load_project("trade").roles
    assert main(["agents", "remove", "trade", "ghost", "--yes"]) == 2


async def test_tui_remove_agente_pelo_projeto_com_confirmacao(proj):
    populate(proj)
    app = DevOrchestraApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.5)
        await pilot.press("2")  # aba Agentes
        await pilot.pause(0.5)
        cards = [c for c in app.screen.query("AgentCard") if c.row.role == "bot-analyst"]
        cards[0].focus()
        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen) and "Será apagado" in app.screen.message
        await pilot.click("#no")
        await pilot.pause()
        assert "bot-analyst" in config.load_project("trade").roles  # recusou
        cards[0].focus()
        await pilot.press("D")
        await pilot.pause()
        await pilot.click("#yes")
        await pilot.pause(0.5)
        assert isinstance(app.screen, ProjectScreen)
    assert "bot-analyst" not in config.load_project("trade").roles
    assert not profiles.profile_path(proj, "bot-analyst").exists()


async def test_formulario_remove_agente_salvo_e_descarta_o_nao_salvo(proj):
    populate(proj)
    app = DevOrchestraApp()
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(ProjectFormScreen(proj))
        await pilot.pause(0.5)
        form = app.screen
        await form.add_specialist("novo")  # ainda não salvo: sai sem confirmação
        form.query_one("#r-novo-remove", Button).press()
        await pilot.pause()
        assert "novo" not in form.role_ids and isinstance(app.screen, ProjectFormScreen)
        form.query_one("#r-bot-analyst-remove", Button).press()  # salvo: mostra o plano
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await pilot.pause(0.5)
        assert isinstance(app.screen, ProjectFormScreen) and "bot-analyst" not in form.role_ids
        assert not form.query("#rc-bot-analyst")
        assert form.query_one("#r-developer-cli", Select).value == "claude"
    assert "bot-analyst" not in config.load_project("trade").roles
    assert "trade" in {p.id for p in config.list_projects()} and paths.project_file("trade").exists()
