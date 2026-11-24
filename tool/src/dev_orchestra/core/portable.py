"""Exportação e importação da configuração do projeto (subconjunto portátil no Git do projeto)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w

from .config import ConfigError, atomic_write_text, project_from_dict, project_to_dict
from .models import Project, explain

EXPORT_DIR = ".dev-orchestra"
EXPORT_FILE = "projeto.toml"


def export_path(project: Project) -> Path:
    return project.path / EXPORT_DIR / EXPORT_FILE


def export_project(project: Project) -> Path:
    """Grava o subconjunto portátil (sem caminhos locais nem assinaturas pessoais)."""
    target = export_path(project)
    atomic_write_text(target, tomli_w.dumps(project_to_dict(project, portable=True)))
    return target


def import_project(
    source: Path,
    folder: Path,
    *,
    subscriptions: dict[str, str] | None = None,
    project_id: str | None = None,
) -> Project:
    """Cria um `Project` a partir de um arquivo exportado.

    `folder` é a pasta local do projeto nesta máquina; `subscriptions` mapeia papel -> assinatura local.
    """
    try:
        with Path(source).open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"arquivo não encontrado: {source}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML inválido em {source}: {exc}") from exc
    if project_id:
        data.setdefault("projeto", {})["id"] = project_id
    try:
        project = project_from_dict(data, path=Path(folder))
    except ValueError as exc:
        raise ConfigError(f"arquivo exportado inválido: {explain(exc)}") from exc
    for role_id, name in (subscriptions or {}).items():
        if role_id not in project.roles:
            raise ConfigError(f"papel desconhecido no arquivo importado: {role_id!r}")
        project.roles[role_id].subscription = name
    return project
