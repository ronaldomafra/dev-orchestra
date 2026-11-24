from __future__ import annotations

from pathlib import Path

import pytest

from dev_orchestra.core.models import Project, Role, TaskSource


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Toda a configuração do teste fica numa pasta temporária."""
    home = tmp_path / "install"
    monkeypatch.setenv("DEV_ORCHESTRA_HOME", str(home))
    monkeypatch.setenv("DEV_ORCHESTRA_DISABLE_NOTIFY", "1")  # nenhum teste toca som nem notificação de verdade
    return home


@pytest.fixture(autouse=True)
def fake_catalogs(monkeypatch):
    """Os formulários leem modelos e esforços das CLIs: nos testes, catálogos fixos (nenhum subprocesso)."""
    from dev_orchestra.core import catalog

    def fixed(cli, *, refresh=False, run=None):
        if cli == "codex":
            models = [catalog.ModelInfo("gpt-6-luna", "rápido", "medium", ("low", "medium", "high"), "teste"),
                      catalog.ModelInfo("gpt-6-sol", "completo", "low", ("low", "medium", "high", "xhigh"), "teste")]  # fmt: skip
            return catalog.Catalog("codex", models, ["low", "medium", "high", "xhigh"])
        if cli == "claude":
            models = [
                catalog.ModelInfo(s, d, None, tuple(catalog.CLAUDE_EFFORTS), "teste") for s, d in catalog.CLAUDE_MODELS
            ]
            return catalog.Catalog("claude", models, list(catalog.CLAUDE_EFFORTS))
        return catalog.Catalog(cli, [], [])

    monkeypatch.setattr(catalog, "catalog_for", fixed)


@pytest.fixture
def project_dir(tmp_path) -> Path:
    folder = tmp_path / "meu-projeto"
    folder.mkdir()
    return folder


@pytest.fixture
def project(project_dir) -> Project:
    return Project(
        id="demo",
        name="Projeto de demonstração",
        path=project_dir,
        task_source=TaskSource(kind="files"),
        roles={
            "orchestrator": Role(cli="claude", default_model="sonnet", default_effort="medium"),
            "developer": Role(
                cli="codex", subscription="codex-pessoal", default_model="gpt-6-luna", default_effort="low"
            ),
        },
    )
