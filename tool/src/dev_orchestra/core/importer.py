"""Importação de um projeto que já existe (AI Memory, agentes e método próprios).

A importação **só lê** a pasta e o AI Memory. Ela preserva os nomes dos agentes (workstreams) e as definições de agente
do projeto, não copia arquivos do método e deixa o Trello como fonte oficial (as tarefas locais vêm do orquestrador).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .liveness import Runner, Workstream, ai_memory_projects, ai_memory_workstreams, run_command
from .models import CANONICAL_ROLES, DEFAULT_TASKS_DIR, Project, Role, TaskSource

# Listas comuns do Trello -> estado da tarefa local (o autor pode editar o mapa ao importar).
DEFAULT_TRELLO_LISTS = {
    "Backlog": "draft",
    "Próxima Sprint": "ready",
    "A fazer": "ready",
    "Em desenvolvimento": "running",
    "Em execução": "running",
    "Aguardando testes": "needs_review",
    "Finalizado": "done",
    "Concluído": "done",
}

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2_592_000, "year": 31_536_000}
_AGE_RE = re.compile(r"^(\d+|an?)\s+(second|minute|hour|day|week|month|year)s?\s+ago$")

_KEYWORDS = {
    "orchestrator": ("orquest", "orchest"),  # cobre "orquestrador" e "orquestador"
    "developer": ("developer", "dev"),
    "planner": ("planner", "plan"),
    "qa": ("qa", "review", "revis"),
}
_GENERIC_NAME = re.compile(r"^([a-z]+-\d+|default)$")  # codex-1, codex-2, default: sessões soltas, não são papéis


def age_seconds(text: str) -> float:
    """Converte o "há quanto tempo" do `ai-memory workstreams` ("2 minutes ago", "just now") em segundos."""
    t = text.strip().lower()
    if t in ("just now", "now"):
        return 0.0
    match = _AGE_RE.match(t)
    if not match:
        return float("inf")
    n = 1 if match[1] in ("a", "an") else int(match[1])
    return float(n * _UNITS[match[2]])


@dataclass(frozen=True)
class AgentDef:
    name: str
    file: str  # caminho relativo à pasta do projeto
    description: str = ""


@dataclass
class ScanResult:
    path: Path
    ai_memory_workspace: str = "default"
    ai_memory_project: str | None = None
    project_exists: bool | None = None  # None = AI Memory não consultado
    workstreams: list[Workstream] | None = None
    agent_defs: list[AgentDef] = field(default_factory=list)
    agents_md: bool = False
    references: list[str] = field(default_factory=list)
    trello_hint: str | None = None  # nome ou URL do board, se algum documento o cita

    def suggested_id(self) -> str:
        base = (self.ai_memory_project or self.path.name).lower()
        return re.sub(r"[^a-z0-9_-]+", "-", base).strip("-_") or "projeto"


@dataclass
class RoleSuggestion:
    workstream: str | None  # nome do agente existente (sessão = workstream)
    cli: str = "claude"
    agent: str | None = None
    candidates: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ leitura


def _read_frontmatter(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


def read_agent_defs(path: Path) -> list[AgentDef]:
    folder = path / ".claude" / "agents"
    if not folder.is_dir():
        return []
    defs = []
    for file in sorted(folder.glob("*.md")):
        meta = _read_frontmatter(file)
        defs.append(
            AgentDef(
                name=meta.get("name") or file.stem,
                file=f".claude/agents/{file.name}",
                description=(meta.get("description") or "")[:160],
            )
        )
    return defs


def find_references(path: Path) -> list[str]:
    """Documentos do método do projeto que os textos de abertura devem citar."""
    found = [name for name in ("AGENTS.md", "CLAUDE.md") if (path / name).is_file()]
    docs = path / "docs"
    if docs.is_dir():
        try:
            extra = sorted(str(p.relative_to(path)) for p in docs.rglob("*dev-orchestra*.md") if p.is_file())
        except OSError:
            extra = []
        found += extra[:3]
    return found


_TRELLO_URL = re.compile(r"https://trello\.com/b/[A-Za-z0-9]+(?:/[A-Za-z0-9_-]+)?")
_TRELLO_BOARD = re.compile(r"board\s+\*\*([^*\n]{3,80})\*\*", re.I)


def detect_trello(path: Path, references: list[str]) -> str | None:
    for rel in references:
        try:
            text = (path / rel).read_text(encoding="utf-8")[:300_000]
        except (OSError, UnicodeDecodeError):
            continue
        url = _TRELLO_URL.search(text)
        if url:
            return url.group(0)
        board = _TRELLO_BOARD.search(text)
        if board:
            return board.group(1).strip()
    return None


def scan(path: Path, *, run: Runner = run_command) -> ScanResult:
    """Lê a pasta e o AI Memory (somente leitura)."""
    path = Path(path).expanduser().resolve()
    result = ScanResult(path=path)
    marker = path / ".ai-memory.toml"
    if marker.is_file():
        try:
            data = tomllib.loads(marker.read_text(encoding="utf-8"))
            result.ai_memory_workspace = str(data.get("workspace", "default"))
            project = data.get("project")
            result.ai_memory_project = str(project) if project else None
        except (OSError, tomllib.TOMLDecodeError):
            pass
    result.agent_defs = read_agent_defs(path)
    result.agents_md = (path / "AGENTS.md").is_file()
    result.references = find_references(path)
    result.trello_hint = detect_trello(path, result.references)
    if result.ai_memory_project:
        projects = ai_memory_projects(run)
        if projects is not None:
            result.project_exists = (result.ai_memory_workspace, result.ai_memory_project) in projects
        if result.project_exists is not False:
            result.workstreams = ai_memory_workstreams(result.ai_memory_project, str(path), run)
    return result


# ---------------------------------------------------------------- sugestões


def _cli_from(harnesses: str) -> str:
    h = harnesses.lower()
    if "claude" in h:
        return "claude"  # cai em Claude também quando o workstream já foi usado pelas duas CLIs
    if "codex" in h:
        return "codex"
    return "claude"


def candidate_workstreams(scan_result: ScanResult) -> list[Workstream]:
    """Workstreams que parecem papéis: com sessão vinculada e sem nome genérico (codex-1...)."""
    out = []
    for ws in scan_result.workstreams or []:
        if "no linked harnesses" in ws.harnesses.lower() or _GENERIC_NAME.match(ws.name.lower()):
            continue
        out.append(ws)
    return out


def suggest_roles(scan_result: ScanResult) -> dict[str, RoleSuggestion]:
    """Sugere, por papel, o workstream mais recente cujo nome o descreve (o autor confirma na TUI)."""
    pool = candidate_workstreams(scan_result)
    suggestions: dict[str, RoleSuggestion] = {}
    for role in CANONICAL_ROLES:
        words = _KEYWORDS[role]
        matches = [w for w in pool if any(k in w.name.lower() for k in words)]
        matches.sort(key=lambda w: age_seconds(w.last_used))  # estável: preserva a ordem do AI Memory em empates
        best = matches[0] if matches else None
        # uma definição de agente existente é coisa do Claude Code: se há uma para o papel, a CLI é o Claude
        agent = next((d.name for d in scan_result.agent_defs if any(k in d.name.lower() for k in words)), None)
        cli = "claude" if agent else (_cli_from(best.harnesses) if best else "claude")
        suggestions[role] = RoleSuggestion(best.name if best else None, cli, agent, [w.name for w in matches])
    return suggestions


def parse_lists(text: str) -> dict[str, str]:
    """`Backlog=draft; Em desenvolvimento=running` -> {"Backlog": "draft", ...} (estados válidos apenas)."""
    from .tasks import STATES

    mapping: dict[str, str] = {}
    for part in text.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        name, _, state = part.partition("=")
        name, state = name.strip(), state.strip()
        if name and state in STATES:
            mapping[name] = state
    return mapping


def format_lists(mapping: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in mapping.items())


def build_project(
    scan_result: ScanResult,
    *,
    project_id: str,
    name: str,
    roles: dict[str, Role],
    task_source: TaskSource,
    references: list[str] | None = None,
) -> Project:
    """Monta o cadastro do projeto importado (método próprio; nomes e agentes existentes preservados)."""
    return Project(
        id=project_id,
        name=name or project_id,
        path=scan_result.path,
        tasks_dir=DEFAULT_TASKS_DIR,
        task_source=task_source,
        roles=roles,
        ai_memory_project=scan_result.ai_memory_project,
        ai_memory_workspace=scan_result.ai_memory_workspace,
        method="proprio",
        references=references if references is not None else scan_result.references,
    )
