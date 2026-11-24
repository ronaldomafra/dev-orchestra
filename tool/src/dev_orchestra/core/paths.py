"""Locais de instalação da ferramenta (configuração por usuário)."""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "DEV_ORCHESTRA_HOME"


def install_dir() -> Path:
    """Pasta de instalação: `DEV_ORCHESTRA_HOME`, ou `$XDG_DATA_HOME/dev-orchestra`,
    ou `~/.local/share/dev-orchestra`."""
    explicit = os.environ.get(ENV_HOME)
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "dev-orchestra"


def global_config_file() -> Path:
    return install_dir() / "config.toml"


def projects_dir() -> Path:
    return install_dir() / "projetos"


def project_file(project_id: str) -> Path:
    return projects_dir() / f"{project_id}.toml"


def state_file(project_id: str) -> Path:
    """Estado local do projeto (por exemplo, quais agentes já receberam as instruções)."""
    return install_dir() / "estado" / f"{project_id}.json"
