"""Sugestões de pasta ao digitar um caminho (completar), com marcadores do que há em cada projeto."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Marker:
    key: str
    label: str


MARKERS = [
    Marker(".git", "GIT"),
    Marker("AGENTS.md", "AGENTS.md"),
    Marker("pyproject.toml", "PYTHON"),
    Marker("package.json", "NODE"),
    Marker("Cargo.toml", "RUST"),
    Marker("go.mod", "GO"),
]


@dataclass(frozen=True)
class Suggestion:
    path: Path
    name: str
    is_dir: bool
    markers: tuple[str, ...] = ()
    modified: datetime | None = None

    @property
    def shown(self) -> str:
        return self.name + ("/" if self.is_dir else "")


def markers_of(path: Path) -> tuple[str, ...]:
    return tuple(m.label for m in MARKERS if (path / m.key).exists())


def split_typed(text: str) -> tuple[Path, str]:
    """`~/projetos/pe` -> (pasta `~/projetos` expandida, prefixo `pe`). Termina em `/` = lista tudo."""
    expanded = os.path.expanduser(text or "~/")
    if expanded.endswith(os.sep) or not expanded:
        return Path(expanded or "/"), ""
    parent, _, prefix = expanded.rpartition(os.sep)
    return Path(parent or os.sep), prefix


def complete(text: str, *, hidden: bool = False, limit: int = 40) -> list[Suggestion]:
    """Subpastas da pasta que o texto já descreve, filtradas pelo que foi digitado (só diretórios)."""
    folder, prefix = split_typed(text)
    try:
        entries = [e for e in os.scandir(folder) if e.is_dir(follow_symlinks=True)]
    except OSError:
        return []
    out = []
    for e in entries:
        if not hidden and e.name.startswith(".") and not prefix.startswith("."):
            continue
        if not e.name.lower().startswith(prefix.lower()):
            continue
        path = Path(e.path)
        try:
            modified = datetime.fromtimestamp(e.stat().st_mtime).astimezone()
        except OSError:
            modified = None
        out.append(Suggestion(path, e.name, True, markers_of(path), modified))
    # projetos (com marcadores) primeiro, depois do mais recente ao mais antigo
    out.sort(key=lambda s: (not s.markers, -(s.modified.timestamp() if s.modified else 0)))
    return out[:limit]


def common_completion(text: str, suggestions: list[Suggestion]) -> str:
    """Texto completado até onde as sugestões concordam (Tab); com uma só, o caminho inteiro com `/`."""
    if not suggestions:
        return text
    folder, _prefix = split_typed(text)
    names = [s.name for s in suggestions]
    base = os.path.commonprefix(names) if len(names) > 1 else names[0] + os.sep
    shown = str(folder).rstrip(os.sep) + os.sep + base
    home = str(Path.home())
    return ("~" + shown[len(home) :]) if shown.startswith(home) and text.startswith("~") else shown


def describe_folder(path: Path) -> dict[str, object]:
    """O que há na pasta escolhida (para o painel de detalhe)."""
    info: dict[str, object] = {"exists": path.is_dir()}
    if not path.is_dir():
        return info
    info["git"] = (path / ".git").exists()
    if info["git"]:
        try:
            branch = subprocess.run(["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                    text=True, timeout=3, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
            changed = subprocess.run(["git", "-C", str(path), "status", "--short"], capture_output=True, text=True,
                                     timeout=3, stdin=subprocess.DEVNULL).stdout.splitlines()  # fmt: skip
            info["branch"], info["modified"] = branch, len(changed)
        except (OSError, subprocess.TimeoutExpired):
            pass
    agents = path / "AGENTS.md"
    info["agents_md"] = agents.is_file()
    if agents.is_file():
        try:
            info["agents_lines"] = len(agents.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            pass
    info["ai_memory_toml"] = (path / ".ai-memory.toml").is_file()
    info["claude_dir"] = (path / ".claude").is_dir()
    info["docs"] = sum(1 for _ in (path / "docs").glob("*")) if (path / "docs").is_dir() else 0
    return info


@dataclass
class Recents:
    paths: list[Path] = field(default_factory=list)


def recent_projects() -> list[Path]:
    """Pastas dos projetos já cadastrados (as mais usadas ficam fáceis de reencontrar)."""
    from . import config

    return [p.path for p in config.list_projects()]
