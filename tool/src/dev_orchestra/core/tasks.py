"""Tarefas em Markdown com metadados em TOML entre linhas `+++`."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import tomli_w

from .config import ConfigError, atomic_write_text
from .models import Project

# `draft`/`ready` antes da execução; `done`... são os STATUS de resultado do PROTOCOL.md.
STATES = (
    "draft",
    "ready",
    "delegated",
    "running",
    "asked",
    "done",
    "partial",
    "blocked",
    "failed",
    "needs_review",
    "cancelled",
)
OPEN_STATES = ("delegated", "running", "asked")
FINAL_STATES = ("done", "cancelled")

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FENCE = "+++"
_RESULT_PLACEHOLDER = "(preenchido pelo orquestrador ao concluir)"

BODY_TEMPLATE = """## Objetivo
{objective}

## Critérios de aceite
- [ ] {criterion}

## Resultado
{placeholder}
"""


class TaskError(ConfigError):
    """Arquivo de tarefa inválido."""


@dataclass
class Task:
    id: str
    title: str
    role: str
    state: str = "ready"
    model: str | None = None
    effort: str | None = None
    created: date = field(default_factory=date.today)
    body: str = ""
    path: Path | None = None
    trello: str | None = None  # URL do cartão de origem
    list_name: str | None = None  # lista do Trello de origem
    live: str | None = None  # estado no delegador, quando ele sobrepõe o do arquivo (só na exibição; nunca é gravado)

    def result_text(self) -> str:
        """Texto da seção `## Resultado`, sem o marcador de preenchimento."""
        match = re.search(r"^## Resultado\s*\n(.*?)(?=^## |\Z)", self.body, re.S | re.M)
        if not match:
            return ""
        text = match.group(1).strip()
        return "" if text == _RESULT_PLACEHOLDER else text


def validate_task_id(task_id: str) -> str:
    if not _TASK_ID_RE.match(task_id):
        raise TaskError(
            f"id de tarefa inválido: {task_id!r} (letras, números, '.', '-' ou '_', começando por letra ou número)"
        )
    return task_id


# Agentes de IA escrevem os arquivos de tarefa à mão e variam os nomes: aceitamos os sinônimos mais comuns.
ROLE_KEYS = ("papel", "role", "responsavel", "agente", "para")
DATE_KEYS = ("criado", "criada", "data")
STATE_ALIASES = {
    "em_andamento": "running",
    "andamento": "running",
    "em_execucao": "running",
    "executando": "running",
    "iniciada": "running",
    "iniciado": "running",
    "in_progress": "running",
    "pronta": "ready",
    "pronto": "ready",
    "pendente": "ready",
    "todo": "ready",
    "delegada": "delegated",
    "delegado": "delegated",
    "concluida": "done",
    "concluido": "done",
    "finalizada": "done",
    "finalizado": "done",
    "feita": "done",
    "feito": "done",
    "bloqueada": "blocked",
    "bloqueado": "blocked",
    "falhou": "failed",
    "falha": "failed",
    "cancelada": "cancelled",
    "cancelado": "cancelled",
    "canceled": "cancelled",
    "em_revisao": "needs_review",
    "em_teste": "needs_review",
    "rascunho": "draft",
    "parcial": "partial",
    "perguntando": "asked",
    "aguardando": "asked",
}


def normalize_state(value: object) -> str:
    plain = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()  # "Concluída" -> "Concluida"
    key = re.sub(r"[\s-]+", "_", plain.strip().lower())
    return STATE_ALIASES.get(key, key)


def resolve_role(value: str, project: Project | None) -> str:
    """O papel pode vir como id do papel ou como o NOME da sessão (ex.: `meu-bot-analise`)."""
    if project is None or value in project.roles:
        return value
    for role in project.roles:
        if project.agent_name(role) == value:
            return role
    return value


def _first(meta: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((meta[k] for k in keys if k in meta), None)


def parse_task(text: str, *, path: Path | None = None, project: Project | None = None) -> Task:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise TaskError("a tarefa precisa começar com uma linha '+++' (metadados TOML)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FENCE)
    except StopIteration as exc:
        raise TaskError("faltou a linha '+++' que fecha os metadados") from exc
    try:
        meta: dict[str, Any] = tomllib.loads("\n".join(lines[1:end]))
    except tomllib.TOMLDecodeError as exc:
        raise TaskError(f"metadados TOML inválidos: {exc}") from exc
    try:
        task_id = validate_task_id(str(meta["id"]))
        title = str(meta["titulo"] if "titulo" in meta else meta["title"])
    except KeyError as exc:
        raise TaskError(f"metadado obrigatório ausente: {exc}") from exc
    raw_role = _first(meta, ROLE_KEYS)
    if raw_role is None:
        raise TaskError("metadado obrigatório ausente: 'papel' (id do papel que executa a tarefa)")
    role = resolve_role(str(raw_role), project)
    state = normalize_state(meta.get("estado", "ready"))
    if state not in STATES:
        raise TaskError(f"estado inválido: {state!r} (use um de: {', '.join(STATES)})")
    created = _first(meta, DATE_KEYS)
    if created is None:
        created = date.today()
    elif isinstance(created, str):
        try:
            created = date.fromisoformat(created.strip())
        except ValueError as exc:
            raise TaskError("'criado' deve ser uma data TOML (AAAA-MM-DD)") from exc
    if not isinstance(created, date):
        raise TaskError("'criado' deve ser uma data TOML (AAAA-MM-DD)")
    return Task(
        id=task_id,
        title=title,
        role=role,
        state=state,
        model=meta.get("modelo"),
        effort=meta.get("esforco"),
        created=created,
        body="\n".join(lines[end + 1 :]).strip("\n"),
        path=path,
        trello=meta.get("trello"),
        list_name=meta.get("lista"),
    )


def render_task(task: Task) -> str:
    meta: dict[str, Any] = {
        "id": task.id,
        "titulo": task.title,
        "papel": task.role,
        "estado": task.state,
        "modelo": task.model,
        "esforco": task.effort,
        "criado": task.created,
        "trello": task.trello,
        "lista": task.list_name,
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    header = tomli_w.dumps(meta).rstrip("\n")
    body = task.body.strip("\n")
    return f"{_FENCE}\n{header}\n{_FENCE}\n\n{body}\n" if body else f"{_FENCE}\n{header}\n{_FENCE}\n"


def task_file(project: Project, task_id: str) -> Path:
    return project.tasks_path / f"{validate_task_id(task_id)}.md"


def read_task(project: Project, task_id: str) -> Task:
    path = task_file(project, task_id)
    if not path.exists():
        raise TaskError(f"tarefa não encontrada: {task_id}")
    return parse_task(path.read_text(encoding="utf-8"), path=path, project=project)


def list_tasks(project: Project) -> tuple[list[Task], list[str]]:
    """Tarefas do projeto e a lista de avisos de arquivos inválidos."""
    folder = project.tasks_path
    if not folder.is_dir():
        return [], []
    tasks: list[Task] = []
    warnings: list[str] = []
    for file in sorted(folder.glob("*.md")):
        try:
            tasks.append(parse_task(file.read_text(encoding="utf-8"), path=file, project=project))
        except TaskError as exc:
            warnings.append(f"{file.name}: {exc}")
    return tasks, warnings


def create_task(
    project: Project,
    task_id: str,
    title: str,
    role: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    objective: str = "",
) -> Task:
    if role not in project.roles:
        raise TaskError(f"papel desconhecido neste projeto: {role!r}")
    path = task_file(project, task_id)
    if path.exists():
        raise TaskError(f"a tarefa já existe: {task_id}")
    role_cfg = project.roles[role]
    task = Task(
        id=task_id,
        title=title,
        role=role,
        model=model or role_cfg.default_model,
        effort=effort or role_cfg.default_effort,
        body=BODY_TEMPLATE.format(
            objective=objective.strip() or "(descreva o objetivo)",
            criterion="(descreva o critério de aceite)",
            placeholder=_RESULT_PLACEHOLDER,
        ).strip("\n"),
        path=path,
    )
    atomic_write_text(path, render_task(task))
    return task


def set_task_state(project: Project, task_id: str, state: str) -> Task:
    if state not in STATES:
        raise TaskError(f"estado inválido: {state!r} (use um de: {', '.join(STATES)})")
    task = read_task(project, task_id)
    task.state = state
    atomic_write_text(task_file(project, task_id), render_task(task))
    return task
