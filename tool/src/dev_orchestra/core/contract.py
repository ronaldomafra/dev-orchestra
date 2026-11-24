"""Arquivos do método (contrato) que cada projeto cadastrado precisa ter.

Regras (decididas pelo autor em 2026-11-14):
1. Se um arquivo não existe, é copiado.
2. Se o projeto já tem alguns, só os que faltam são copiados; **nada é sobrescrito**.
3. Se o projeto já tem um `AGENTS.md` próprio, ele **não é alterado**: o modelo do contrato vai para
   `.dev-orchestra/contrato/AGENTS.md` e o texto de abertura do orquestrador o instrui a incorporá-lo
   (preservando o conteúdo atual e pedindo aprovação antes de gravar).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from .config import atomic_write_text
from .models import Project

CONTRACT_MODEL_TARGET = ".dev-orchestra/contrato/AGENTS.md"
AGENTS_FILE = "AGENTS.md"

# Marcador de que o AGENTS.md do projeto já incorpora o contrato (lista de papéis com seus arquivos).
INTEGRATED_MARKER = "roles/orchestrator.md"
# No modo delegador o AGENTS.md também precisa citar o documento do delegador (precedência sobre o transporte).
DELEGATOR_MARKER = "docs/DELEGATOR.md"

ROLE_FILES = ("orchestrator", "developer", "qa", "planner", "social-media")


@dataclass(frozen=True)
class Entry:
    source: str  # caminho dentro de dev_orchestra/contract/
    target: str  # caminho relativo à pasta do projeto
    claude_only: bool = False
    delegator_only: bool = False  # só nos projetos em modo delegador


ENTRIES: tuple[Entry, ...] = (
    Entry("AGENTS.md", AGENTS_FILE),
    *(Entry(f"roles/{r}.md", f"roles/{r}.md") for r in ROLE_FILES),
    Entry("docs/PROTOCOL.md", "docs/PROTOCOL.md"),
    Entry("docs/MODEL-SELECTION.md", "docs/MODEL-SELECTION.md"),
    Entry("docs/ADAPTER-CLAUDE-CODE.md", "docs/ADAPTER-CLAUDE-CODE.md"),
    Entry("docs/ADAPTER-CODEX.md", "docs/ADAPTER-CODEX.md"),
    Entry("docs/DELEGATOR.md", "docs/DELEGATOR.md", delegator_only=True),
    Entry("templates/handoff.md", "templates/handoff.md"),
    Entry("templates/result.md", "templates/result.md"),
    *(
        Entry(f"claude-agents/{a}.md", f".claude/agents/{a}.md", claude_only=True)
        for a in ("developer", "planner", "qa")
    ),
)


def read_bundled(source: str) -> str:
    return files("dev_orchestra").joinpath("contract", *source.split("/")).read_text(encoding="utf-8")


def _used(entry: Entry, project: Project) -> bool:
    """Só copia o que o projeto usa: os arquivos dos papéis cadastrados e o adapter de cada CLI em uso."""
    clis = {r.cli for r in project.roles.values()}
    if entry.target.startswith("roles/"):
        return Path(entry.target).stem in project.roles
    if entry.claude_only:  # `.claude/agents/<papel>.md`
        role = project.roles.get(Path(entry.target).stem)
        return role is not None and role.cli == "claude"
    if entry.target == "docs/ADAPTER-CLAUDE-CODE.md":
        return "claude" in clis
    if entry.target == "docs/ADAPTER-CODEX.md":
        return "codex" in clis
    return project.delegator or not entry.delegator_only


def applicable(project: Project) -> list[Entry]:
    return [e for e in ENTRIES if _used(e, project)]


def agents_md_state(project: Project) -> str:
    """`absent`, `integrated` (já cita os papéis do método) ou `needs_merge` (AGENTS.md próprio sem o contrato)."""
    path = project.path / AGENTS_FILE
    if not path.is_file():
        return "absent"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "needs_merge"
    if INTEGRATED_MARKER not in text:
        return "needs_merge"
    if project.delegator and DELEGATOR_MARKER not in text:
        return "needs_merge"  # o contrato é anterior ao modo delegador
    return "integrated"


def missing_files(project: Project) -> list[str]:
    """Arquivos do método que ainda não existem (o AGENTS.md próprio do projeto não conta como faltando)."""
    if project.method == "proprio":
        return [e.target for e in own_method_entries(project) if not (project.path / e.target).exists()]
    return [e.target for e in applicable(project) if not (project.path / e.target).exists()]


def own_method_entries(project: Project) -> list[Entry]:
    """Projeto de método próprio: nada do método é copiado, exceto o contrato do delegador quando ele está ligado."""
    return [e for e in ENTRIES if e.delegator_only] if project.delegator else []


def own_docs_cite_delegator(project: Project) -> bool:
    """O AGENTS.md próprio já aponta para docs/DELEGATOR.md (a regra de comunicação do modo delegador)?"""
    try:
        return DELEGATOR_MARKER in (project.path / AGENTS_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


@dataclass
class ScaffoldReport:
    created: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    agents_md: str = "integrated"  # estado do AGENTS.md ANTES da execução
    contract_model: str | None = None  # onde o modelo do contrato foi salvo (AGENTS.md próprio existente)
    own_method: bool = False  # projeto importado: usa o método próprio, nada é copiado

    @property
    def needs_merge(self) -> bool:
        return self.agents_md == "needs_merge"


def scaffold(project: Project, *, dry_run: bool = False) -> ScaffoldReport:
    """Cria na pasta do projeto os arquivos do método que faltam. Nunca sobrescreve."""
    if not project.path.is_dir():
        raise FileNotFoundError(f"a pasta do projeto não existe: {project.path}")
    if project.method == "proprio":
        report = ScaffoldReport(own_method=True)
        for entry in own_method_entries(project):
            dest = project.path / entry.target
            if dest.exists():
                report.kept.append(entry.target)
                continue
            report.created.append(entry.target)
            if not dry_run:
                atomic_write_text(dest, read_bundled(entry.source))
        return report
    report = ScaffoldReport(agents_md=agents_md_state(project))
    for entry in applicable(project):
        dest = project.path / entry.target
        if dest.exists():
            report.kept.append(entry.target)
            continue
        report.created.append(entry.target)
        if not dry_run:
            atomic_write_text(dest, read_bundled(entry.source))
    if report.needs_merge:
        model = project.path / CONTRACT_MODEL_TARGET
        if not model.exists():
            if not dry_run:
                atomic_write_text(model, read_bundled(AGENTS_FILE))
            report.created.append(CONTRACT_MODEL_TARGET)
        report.contract_model = CONTRACT_MODEL_TARGET
    return report


def format_report(report: ScaffoldReport) -> str:
    if report.own_method:
        text = "Projeto importado (método próprio): os documentos do projeto valem; nenhum arquivo do método é copiado"
        if report.created or report.kept:
            done = ", ".join([*(f"+ {t}" for t in report.created), *(f"= {t} (já existia)" for t in report.kept)])
            return f"{text}, exceto o contrato do delegador: {done}."
        return text + "."
    lines = [f"Arquivos do método: {len(report.created)} criados, {len(report.kept)} já existiam (não alterados)."]
    lines += [f"  + {t}" for t in report.created]
    if report.needs_merge:
        lines.append(
            "O projeto já tem um AGENTS.md próprio e ele NÃO foi alterado. O modelo do contrato foi salvo em "
            f"{CONTRACT_MODEL_TARGET}; o texto de abertura do orquestrador pede que ele o incorpore ao AGENTS.md, "
            "com a sua aprovação."
        )
    return "\n".join(lines)


def reference_paths(role: str) -> list[str]:
    """Caminhos de referência que o texto de abertura de um papel cita."""
    if role == "orchestrator":
        return [
            AGENTS_FILE,
            "roles/orchestrator.md",
            "docs/PROTOCOL.md",
            "docs/MODEL-SELECTION.md",
            "templates/handoff.md",
            "templates/result.md",
        ]
    return [AGENTS_FILE, f"roles/{role}.md", "docs/PROTOCOL.md", "templates/result.md"]
