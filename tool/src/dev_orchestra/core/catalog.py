"""Modelos e esforços que cada CLI oferece (lidos da própria CLI quando ela tem como listar)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .liveness import Runner, run_command

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]  # `claude --help`: (low, medium, high, xhigh, max)
# A CLI do Claude não lista modelos: aliases do `--help` e os ids completos conhecidos (lista mantida aqui).
CLAUDE_MODELS = [
    ("fable", "alias do modelo Fable mais recente"),
    ("opus", "alias do modelo Opus mais recente"),
    ("sonnet", "alias do modelo Sonnet mais recente"),
    ("haiku", "alias do modelo Haiku mais recente"),
    ("claude-fable-5-2", "Fable 5.2"),
    ("claude-opus-5-5", "Opus 5.5"),
    ("claude-sonnet-5-5", "Sonnet 5.5"),
    ("claude-haiku-5-5", "Haiku 5.5"),
]
GENERIC_EFFORTS = ["low", "medium", "high"]


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    description: str = ""
    default_effort: str | None = None
    efforts: tuple[str, ...] = ()
    source: str = ""  # de onde veio: "claude (lista local)", "codex debug models"...


@dataclass
class Catalog:
    cli: str
    models: list[ModelInfo] = field(default_factory=list)
    efforts: list[str] = field(default_factory=list)  # esforços gerais da CLI
    note: str = ""

    def slugs(self) -> list[str]:
        return [m.slug for m in self.models]

    def efforts_for(self, model: str | None) -> list[str]:
        """Esforços aceitos pelo modelo (ou os gerais da CLI quando o modelo não diz)."""
        for m in self.models:
            if m.slug == model and m.efforts:
                return list(m.efforts)
        return list(self.efforts or GENERIC_EFFORTS)

    def default_effort(self, model: str | None) -> str | None:
        return next((m.default_effort for m in self.models if m.slug == model), None)


_CACHE: dict[str, Catalog] = {}


def claude_efforts(run: Runner = run_command) -> list[str]:
    """Níveis de `--effort` lidos do `claude --help` (com a lista conhecida como reserva)."""
    code, out = run(["claude", "--help"], timeout=10.0)
    match = re.search(r"--effort <level>.*?\(([a-z, ]+)\)", out, re.S) if code == 0 else None
    found = [x.strip() for x in match.group(1).split(",")] if match else []
    return found or list(CLAUDE_EFFORTS)


def claude_catalog(run: Runner = run_command) -> Catalog:
    efforts = claude_efforts(run)
    models = [
        ModelInfo(slug, desc, None, tuple(efforts), "claude (aliases do --help e lista local)")
        for slug, desc in CLAUDE_MODELS
    ]
    return Catalog("claude", models, efforts, "a CLI do Claude não lista modelos: aliases do --help e ids conhecidos")


def parse_codex_models(text: str) -> list[ModelInfo]:
    data = json.loads(text)
    items = data.get("models", data) if isinstance(data, dict) else data
    out = []
    for m in items:
        if m.get("visibility") not in (None, "list"):
            continue
        levels = tuple(
            x["effort"] for x in m.get("supported_reasoning_levels", []) if isinstance(x, dict) and "effort" in x
        )
        out.append(
            ModelInfo(
                str(m["slug"]),
                str(m.get("description", "")),
                m.get("default_reasoning_level"),
                levels,
                "codex debug models",
            )
        )
    return out


def codex_catalog(run: Runner = run_command) -> Catalog:
    code, out = run(["codex", "debug", "models"], timeout=15.0)
    if code != 0 or not out.strip():
        return Catalog("codex", [], list(GENERIC_EFFORTS), "não foi possível ler o catálogo (`codex debug models`)")
    try:
        models = parse_codex_models(out)
    except (ValueError, KeyError, TypeError):
        return Catalog("codex", [], list(GENERIC_EFFORTS), "catálogo do Codex ilegível")
    return Catalog(
        "codex", models, sorted({e for m in models for e in m.efforts}, key=_effort_rank) or list(GENERIC_EFFORTS)
    )


def _effort_rank(effort: str) -> int:
    order = ["minimal", "low", "medium", "high", "xhigh", "max"]
    return order.index(effort) if effort in order else len(order)


def opencode_catalog(run: Runner = run_command) -> Catalog:
    code, out = run(["opencode", "models"], timeout=15.0)
    models = (
        [ModelInfo(line.strip(), "", None, (), "opencode models") for line in out.splitlines() if "/" in line]
        if code == 0
        else []
    )
    return Catalog("opencode", models, [], "" if models else "não foi possível listar (`opencode models`)")


def ollama_catalog(url: str = "http://127.0.0.1:11434", *, timeout: float = 2.0) -> Catalog:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as response:  # noqa: S310 - loopback
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return Catalog("ollama", [], [], "Ollama não respondeu em " + url)
    models = [
        ModelInfo(m["name"], f"{round(m.get('size', 0) / 1e9, 1)} GB", None, (), "ollama /api/tags")
        for m in data.get("models", [])
    ]
    return Catalog("ollama", models, [], "")


def catalog_for(cli: str, *, refresh: bool = False, run: Runner = run_command) -> Catalog:
    """Catálogo da CLI (em cache durante o processo; `refresh` recarrega)."""
    if not refresh and cli in _CACHE:
        return _CACHE[cli]
    builders = {"claude": lambda: claude_catalog(run), "codex": lambda: codex_catalog(run), "opencode": lambda: opencode_catalog(run),
                "ollama": ollama_catalog}  # fmt: skip
    builder = builders.get(cli)
    catalog = builder() if builder else Catalog(cli, [], list(GENERIC_EFFORTS))
    _CACHE[cli] = catalog
    return catalog


def clear_cache() -> None:
    _CACHE.clear()
