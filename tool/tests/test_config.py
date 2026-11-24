from __future__ import annotations

import pytest

from dev_orchestra.core import config, paths
from dev_orchestra.core.models import GlobalConfig, Project, Role, Subscription


def test_install_dir_usa_variavel_de_ambiente(isolated_home):
    assert paths.install_dir() == isolated_home


def test_install_dir_padrao_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("DEV_ORCHESTRA_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.install_dir() == tmp_path / "xdg" / "dev-orchestra"


def test_salvar_e_carregar_projeto(project):
    target = config.save_project(project)
    assert target == paths.project_file("demo")
    loaded = config.load_project("demo")
    assert loaded == project


def test_toml_usa_chaves_em_portugues(project):
    text = config.save_project(project).read_text(encoding="utf-8")
    assert "[projeto]" in text and "[papeis.orchestrator]" in text
    assert 'modelo_padrao = "gpt-6-luna"' in text
    assert 'assinatura = "codex-pessoal"' in text


def test_exportacao_portatil_omite_pasta_e_assinatura(project):
    data = config.project_to_dict(project, portable=True)
    assert "pasta" not in data["projeto"]
    assert all("assinatura" not in r for r in data["papeis"].values())
    assert data["papeis"]["developer"]["cli"] == "codex"


def test_listar_projetos_ignora_cadastro_invalido(project):
    config.save_project(project)
    (paths.projects_dir() / "quebrado.toml").write_text("isto não é toml [", encoding="utf-8")
    assert [p.id for p in config.list_projects()] == ["demo"]


def test_listar_sem_pasta_de_instalacao():
    assert config.list_projects() == []


def test_carregar_projeto_inexistente():
    with pytest.raises(config.ConfigError, match="não encontrado"):
        config.load_project("nao-existe")


def test_cli_invalida_e_rejeitada(project):
    data = config.project_to_dict(project)
    data["papeis"]["developer"]["cli"] = "gemini"
    with pytest.raises(ValueError):
        config.project_from_dict(data)


def test_id_de_projeto_invalido():
    with pytest.raises(ValueError, match="id do projeto"):
        Project(id="Meu Projeto", name="x", path="/tmp/x", roles={"developer": Role(cli="claude")})


def test_projeto_sem_papeis():
    with pytest.raises(ValueError, match="ao menos um papel"):
        Project(id="x", name="x", path="/tmp/x", roles={})


def test_ordem_dos_papeis():
    p = Project(
        id="x",
        name="x",
        path="/tmp/x",
        roles={
            "social-media": Role(cli="claude"),
            "qa": Role(cli="claude"),
            "orchestrator": Role(cli="claude"),
            "developer": Role(cli="claude"),
        },
    )
    assert p.role_order() == ["orchestrator", "developer", "qa", "social-media"]


def test_config_global_ida_e_volta():
    cfg = GlobalConfig(
        app_server_url="ws://127.0.0.1:4600",
        subscriptions={"claude-pessoal": Subscription(cli="claude", profile="work")},
    )
    config.save_global(cfg)
    assert config.load_global() == cfg


def test_config_global_padrao_quando_nao_existe():
    assert config.load_global() == GlobalConfig()


def test_url_do_app_server_do_projeto_tem_prioridade(project):
    cfg = GlobalConfig()
    assert cfg.server_url_for(project) == "ws://127.0.0.1:4500"
    project.app_server_url = "ws://127.0.0.1:4700"
    assert cfg.server_url_for(project) == "ws://127.0.0.1:4700"
