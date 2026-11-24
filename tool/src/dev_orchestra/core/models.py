"""Modelos de cadastro (projeto, papéis, assinaturas) e validações."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

Cli = Literal["claude", "codex"]
TaskSourceKind = Literal["files", "trello"]
Method = Literal["padrao", "proprio"]  # `proprio` = projeto importado, que já tem o seu método

# Papéis canônicos = nomes dos workstreams (AGENTS.md). Especialistas usam ids próprios.
CANONICAL_ROLES = ("orchestrator", "developer", "qa", "planner")
DEFAULT_APP_SERVER_URL = "ws://127.0.0.1:4500"
DEFAULT_DELEGATOR_URL = "http://127.0.0.1:8787"
DEFAULT_TASKS_DIR = ".dev-orchestra/tarefas"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def explain(exc: Exception) -> str:
    """Mensagem curta para o usuário (o texto padrão do pydantic é longo e técnico)."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            msg = str(err.get("msg", "")).removeprefix("Value error, ")
            loc = ".".join(str(x) for x in err.get("loc", ()) if x != "__root__")
            parts.append(f"{loc}: {msg}" if loc and loc not in msg else msg)
        return "; ".join(parts)
    return str(exc)


def _check_id(value: str, what: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(
            f"{what} inválido: {value!r} (use minúsculas, números, '-' ou '_', começando por letra ou número)"
        )
    return value


class Role(BaseModel):
    """Configuração de um papel dentro de um projeto."""

    model_config = ConfigDict(extra="forbid")

    cli: Cli
    subscription: str | None = None
    default_model: str | None = None
    default_effort: str | None = None
    name: str | None = None  # nome explícito do agente (projetos importados preservam o nome existente)
    agent: str | None = None  # definição de agente do Claude Code (`--agent`), por exemplo `meuapp-developer`
    codex_resume: str | None = None  # UUID da thread do Codex a retomar (`codex resume <UUID>`)
    resume: str | None = None  # id da sessão do Claude a adotar (`claude --resume <id>`)
    resume_fork: bool = True  # adota uma CÓPIA da sessão: a original fica intacta
    resume_origin: str | None = None  # id da sessão original, quando `resume` já é a cópia feita pela ferramenta
    permission: Literal["read-only", "plan"] | None = None  # restrição de escrita ao abrir a sessão

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str | None) -> str | None:
        if v and not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", v):
            raise ValueError(f"nome de agente inválido: {v!r} (letras, números, '.', '-' ou '_', sem espaços)")
        return v or None


class TaskSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TaskSourceKind = "files"
    trello_board: str | None = None  # nome ou URL do board
    trello_lists: dict[str, str] = {}  # nome da lista do Trello -> estado da tarefa local


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    path: Path
    tasks_dir: str = DEFAULT_TASKS_DIR
    task_source: TaskSource = TaskSource()
    roles: dict[str, Role]
    app_server_url: str | None = None
    ai_memory_project: str | None = None
    ai_memory_workspace: str = "default"
    prefix: str | None = None  # prefixo da identidade dos agentes; padrão: o id do projeto
    method: Method = "padrao"
    references: list[str] = []  # documentos do método próprio que os textos de abertura citam
    delegator: bool = False  # as sessões se comunicam pelo delegador (MCP) em vez de SendMessage/codex queue

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        return _check_id(v, "id do projeto")

    @field_validator("path")
    @classmethod
    def _abs_path(cls, v: Path) -> Path:
        return v.expanduser()

    @field_validator("prefix")
    @classmethod
    def _valid_prefix(cls, v: str | None) -> str | None:
        return _check_id(v, "prefixo") if v else None

    @model_validator(mode="after")
    def _valid_roles(self) -> Project:
        if not self.roles:
            raise ValueError("o projeto precisa de ao menos um papel")
        for role_id in self.roles:
            _check_id(role_id, "id do papel")
        return self

    @property
    def tasks_path(self) -> Path:
        return self.path / self.tasks_dir

    def role_order(self) -> list[str]:
        """Papéis canônicos na ordem do fluxo, depois os especialistas em ordem alfabética."""
        known = [r for r in CANONICAL_ROLES if r in self.roles]
        extra = sorted(r for r in self.roles if r not in CANONICAL_ROLES)
        return known + extra

    def ai_memory_name(self) -> str:
        return self.ai_memory_project or self.id

    def agent_name(self, role: str) -> str:
        """Identidade do agente: `[prefixo]-[papel]` (nome da sessão e do workstream)."""
        explicit = self.roles[role].name if role in self.roles else None
        return explicit or f"{self.prefix or self.id}-{role}"


class Subscription(BaseModel):
    """Assinatura/conta referenciada pelos papéis. `profile` é o `--profile` do `ai-memory run`."""

    model_config = ConfigDict(extra="forbid")

    cli: Cli
    profile: str | None = None
    note: str | None = None


class AlertSettings(BaseModel):
    """Preferências dos alertas (som, notificação do sistema, silêncio e limites)."""

    model_config = ConfigDict(extra="forbid")

    sound: bool = True
    os_notify: bool = True
    volume: int = 80  # 0 a 100
    quiet_start: str | None = "22:00"  # silêncio programado: só o som (o alerta continua na TUI)
    quiet_end: str | None = "07:00"
    repeat_minutes: int = 5  # repete o alerta crítico sem resposta
    repeat_max: int = 3
    question_minutes: int = 5  # pergunta de worker sem resposta
    stalled_minutes: int = 10  # tarefa em execução sem nenhum evento
    not_received_minutes: int = 2  # delegada e o worker ainda não recebeu
    done_minutes: int = 2  # concluída, aguardando a conferência do orquestrador
    muted_kinds: list[str] = []

    @field_validator("volume")
    @classmethod
    def _volume(cls, v: int) -> int:
        return max(0, min(100, v))

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _clock(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError(f"horário inválido: {v!r} (use HH:MM)")
        return v


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: AlertSettings = AlertSettings()
    app_server_url: str = DEFAULT_APP_SERVER_URL
    delegator_url: str = DEFAULT_DELEGATOR_URL
    generator_command: str | None = None  # provedor "genérico" de perfis: lê o prompt no stdin, escreve o perfil
    subscriptions: dict[str, Subscription] = {}

    def server_url_for(self, project: Project) -> str:
        return project.app_server_url or self.app_server_url
