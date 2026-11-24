"""Perfis dos papéis guardados no projeto (`.dev-orchestra/perfis/<papel>.md`) e sincronizados com a instalação.

O arquivo no projeto é a fonte (vai para o Git). O delegador, no container, não lê as pastas dos projetos: ele lê a
cópia em `<instalação>/perfis/<projeto>/<papel>.md`, que esta camada mantém igual.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import paths
from .config import atomic_write_text
from .models import Project

PROFILES_DIR = ".dev-orchestra/perfis"
DELEGATOR_TOOLS = ("register_session", "heartbeat", "receive", "report", "ask", "project_status")
READ_TOOLS = ("Read", "Grep", "Glob")
AGENT_DEF_MARK = "<!-- gerado por dev-orchestra -->"  # permite reconhecer o que a ferramenta gerou (ex.: ao remover)


def profile_path(project: Project, role: str) -> Path:
    return project.path / PROFILES_DIR / f"{role}.md"


def install_path(project: Project, role: str) -> Path:
    return paths.install_dir() / "perfis" / project.id / f"{role}.md"


def read_profile(project: Project, role: str) -> str | None:
    for path in (profile_path(project, role), install_path(project, role)):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def save_profile(project: Project, role: str, text: str) -> tuple[Path, Path]:
    """Grava no projeto (fonte) e na instalação (o que o delegador lê). Devolve os dois caminhos."""
    body = text.strip() + "\n"
    source, target = profile_path(project, role), install_path(project, role)
    atomic_write_text(source, body)
    atomic_write_text(target, body)
    return source, target


def sync_to_install(project: Project) -> list[str]:
    """Copia para a instalação os perfis do projeto que estão diferentes (o projeto manda). Devolve os papéis."""
    folder = project.path / PROFILES_DIR
    changed = []
    if not folder.is_dir():
        return changed
    for file in sorted(folder.glob("*.md")):
        text = file.read_text(encoding="utf-8")
        target = install_path(project, file.stem)
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            atomic_write_text(target, text)
            changed.append(file.stem)
    return changed


@dataclass(frozen=True)
class ProfileEntry:
    role: str
    origin: str  # projeto | instalação | padrão
    path: Path | None


def list_profiles(project: Project) -> list[ProfileEntry]:
    out = []
    for role in project.role_order():
        if profile_path(project, role).is_file():
            out.append(ProfileEntry(role, "projeto", profile_path(project, role)))
        elif install_path(project, role).is_file():
            out.append(ProfileEntry(role, "instalação", install_path(project, role)))
        else:
            out.append(ProfileEntry(role, "padrão", None))
    return out


# ----------------------------------------------------------- definição de agente (.claude/agents)


def render_agent_def(project: Project, role: str, description: str, tools: list[str]) -> str:
    """Definição de agente do Claude com `tools:` restrito: é o que de fato impede o papel de editar arquivos."""
    listed = [*tools, *[f"mcp__dev-orchestra__{t}" for t in DELEGATOR_TOOLS]]
    desc = " ".join(description.split())[:240] or f"Papel {role} do projeto {project.name}."
    return f"""---
name: {role}
description: {desc}
tools: {", ".join(listed)}
---
{AGENT_DEF_MARK}

Você é o papel `{role}` do projeto {project.name} (sessão `{project.agent_name(role)}`).

Ao abrir, registre-se no delegador (`register_session`) e siga o perfil devolvido; ele é a sua fonte de instruções.
Fale com outras sessões somente pelas ferramentas do delegador (docs/DELEGATOR.md). Você só dispõe das ferramentas
listadas no cabeçalho deste arquivo: se faltar uma ferramenta, peça (`ask`) em vez de contornar.
"""


def agent_def_path(project: Project, role: str) -> Path:
    return project.path / ".claude" / "agents" / f"{role}.md"


def write_agent_def(
    project: Project, role: str, description: str, tools: list[str], *, overwrite: bool = False
) -> Path:
    path = agent_def_path(project, role)
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    atomic_write_text(path, render_agent_def(project, role, description, tools))
    return path
