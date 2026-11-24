from __future__ import annotations

import pytest

from dev_orchestra.core import config, portable


def test_exportar_cria_arquivo_na_pasta_do_projeto(project):
    target = portable.export_project(project)
    assert target == project.path / ".dev-orchestra" / "projeto.toml"
    text = target.read_text(encoding="utf-8")
    assert "pasta" not in text and "assinatura" not in text
    assert 'cli = "codex"' in text


def test_importar_pede_assinaturas_locais(project, tmp_path):
    exported = portable.export_project(project)
    outra_pasta = tmp_path / "outra-maquina"
    outra_pasta.mkdir()
    imported = portable.import_project(exported, outra_pasta, subscriptions={"developer": "codex-trabalho"})
    assert imported.path == outra_pasta
    assert imported.roles["developer"].subscription == "codex-trabalho"
    assert imported.roles["orchestrator"].subscription is None
    assert imported.roles["developer"].default_model == "gpt-6-luna"
    assert imported.id == project.id


def test_importar_com_novo_id(project, tmp_path):
    exported = portable.export_project(project)
    imported = portable.import_project(exported, tmp_path, project_id="copia")
    assert imported.id == "copia"


def test_importar_assinatura_de_papel_inexistente(project, tmp_path):
    exported = portable.export_project(project)
    with pytest.raises(config.ConfigError, match="papel desconhecido"):
        portable.import_project(exported, tmp_path, subscriptions={"qa": "x"})


def test_importar_arquivo_inexistente(tmp_path):
    with pytest.raises(config.ConfigError, match="não encontrado"):
        portable.import_project(tmp_path / "nada.toml", tmp_path)


def test_importar_toml_invalido(tmp_path):
    ruim = tmp_path / "ruim.toml"
    ruim.write_text("[[[", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="TOML inválido"):
        portable.import_project(ruim, tmp_path)


def test_importar_com_cli_invalida(project, tmp_path):
    exported = portable.export_project(project)
    exported.write_text(exported.read_text(encoding="utf-8").replace('cli = "codex"', 'cli = "x"'), encoding="utf-8")
    with pytest.raises(config.ConfigError, match="inválido"):
        portable.import_project(exported, tmp_path)
