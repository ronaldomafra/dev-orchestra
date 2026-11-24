"""Leitura e escrita da configuração em TOML (pasta de instalação)."""

from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from . import paths
from .models import (
    DEFAULT_APP_SERVER_URL,
    DEFAULT_DELEGATOR_URL,
    DEFAULT_TASKS_DIR,
    AlertSettings,
    GlobalConfig,
    Project,
    Role,
    Subscription,
    TaskSource,
    explain,
)

_KIND_TO_TOML = {"files": "arquivos", "trello": "trello"}
_KIND_FROM_TOML = {v: k for k, v in _KIND_TO_TOML.items()}


class ConfigError(Exception):
    """Arquivo de configuração ausente, ilegível ou inválido."""


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    """Remove valores None (TOML não os representa)."""
    return {k: v for k, v in d.items() if v is not None}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"arquivo não encontrado: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML inválido em {path}: {exc}") from exc


# ---------------------------------------------------------------- projeto


def project_to_dict(project: Project, *, portable: bool = False) -> dict[str, Any]:
    """Converte o projeto para o dicionário TOML (chaves em português, como na especificação).

    `portable=True` omite caminho local e nomes de assinatura (exportação para o Git do projeto).
    """
    proj: dict[str, Any] = {
        "id": project.id,
        "nome": project.name,
        "tarefas": project.tasks_dir,
        "prefixo": project.prefix,
        "metodo": project.method if project.method != "padrao" else None,
        "delegador": True if project.delegator else None,
        "referencias": project.references or None,
    }
    if not portable:
        proj["pasta"] = str(project.path)
        proj["app_server"] = project.app_server_url
        proj["ai_memory_projeto"] = project.ai_memory_project
        if project.ai_memory_workspace != "default":
            proj["ai_memory_workspace"] = project.ai_memory_workspace
    source = _clean(
        {
            "tipo": _KIND_TO_TOML[project.task_source.kind],
            "trello_board": project.task_source.trello_board,
            "trello_listas": project.task_source.trello_lists or None,
        }
    )
    roles: dict[str, Any] = {}
    for role_id in project.role_order():
        role = project.roles[role_id]
        roles[role_id] = _clean(
            {
                "cli": role.cli,
                "assinatura": None if portable else role.subscription,
                "modelo_padrao": role.default_model,
                "esforco_padrao": role.default_effort,
                "nome": role.name,
                "agente": role.agent,
                "codex_resume": None if portable else role.codex_resume,
                "sessao": None if portable else role.resume,
                "sessao_copia": None if portable or not role.resume else role.resume_fork,
                "sessao_origem": None if portable else role.resume_origin,
                "permissao": role.permission,
            }
        )
    return {"projeto": _clean(proj), "task_source": source, "papeis": roles}


def project_from_dict(data: dict[str, Any], *, path: Path | None = None) -> Project:
    try:
        proj = data["projeto"]
        source = data.get("task_source", {})
        roles_raw = data["papeis"]
    except KeyError as exc:
        raise ConfigError(f"seção obrigatória ausente: {exc}") from exc
    kind_toml = source.get("tipo", "arquivos")
    if kind_toml not in _KIND_FROM_TOML:
        raise ConfigError(f"task_source.tipo inválido: {kind_toml!r} (use arquivos ou trello)")
    roles = {
        rid: Role(
            cli=r.get("cli"),
            subscription=r.get("assinatura"),
            default_model=r.get("modelo_padrao"),
            default_effort=r.get("esforco_padrao"),
            name=r.get("nome"),
            agent=r.get("agente"),
            codex_resume=r.get("codex_resume"),
            resume=r.get("sessao"),
            resume_fork=bool(r.get("sessao_copia", True)),
            resume_origin=r.get("sessao_origem"),
            permission=r.get("permissao"),
        )
        for rid, r in roles_raw.items()
    }
    folder = path if path is not None else proj.get("pasta")
    if folder is None:
        raise ConfigError("o projeto não informa 'pasta'")
    return Project(
        id=proj["id"],
        name=proj.get("nome", proj["id"]),
        path=Path(folder),
        tasks_dir=proj.get("tarefas", DEFAULT_TASKS_DIR),
        task_source=TaskSource(
            kind=_KIND_FROM_TOML[kind_toml],
            trello_board=source.get("trello_board"),
            trello_lists=dict(source.get("trello_listas", {})),
        ),
        roles=roles,
        app_server_url=proj.get("app_server"),
        ai_memory_project=proj.get("ai_memory_projeto"),
        ai_memory_workspace=proj.get("ai_memory_workspace", "default"),
        prefix=proj.get("prefixo"),
        method=proj.get("metodo", "padrao"),
        references=list(proj.get("referencias", [])),
        delegator=bool(proj.get("delegador", False)),
    )


def save_project(project: Project) -> Path:
    target = paths.project_file(project.id)
    atomic_write_text(target, tomli_w.dumps(project_to_dict(project)))
    return target


def load_project(project_id: str) -> Project:
    data = _read_toml(paths.project_file(project_id))
    try:
        return project_from_dict(data)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"cadastro inválido do projeto {project_id}: {explain(exc)}") from exc


def list_projects() -> list[Project]:
    """Projetos cadastrados, em ordem de id. Cadastros inválidos são ignorados."""
    folder = paths.projects_dir()
    if not folder.is_dir():
        return []
    found: list[Project] = []
    for file in sorted(folder.glob("*.toml")):
        try:
            found.append(load_project(file.stem))
        except ConfigError:
            continue
    return found


def remove_project(project_id: str) -> bool:
    """Remove só o cadastro (não toca na pasta do projeto)."""
    target = paths.project_file(project_id)
    if target.exists():
        target.unlink()
        return True
    return False


# ---------------------------------------------------------------- global


def load_global() -> GlobalConfig:
    file = paths.global_config_file()
    if not file.exists():
        return GlobalConfig()
    data = _read_toml(file)
    subs = {
        name: Subscription(cli=s.get("cli"), profile=s.get("perfil"), note=s.get("nota"))
        for name, s in data.get("assinaturas", {}).items()
    }
    try:
        return GlobalConfig(
            app_server_url=data.get("app_server", DEFAULT_APP_SERVER_URL),
            delegator_url=data.get("delegador_url", DEFAULT_DELEGATOR_URL),
            generator_command=data.get("gerador_comando"),
            subscriptions=subs,
            alerts=_alerts_from(data.get("alertas", {})),
        )
    except ValueError as exc:
        raise ConfigError(f"config.toml inválido: {explain(exc)}") from exc


def _alerts_from(data: dict[str, Any]) -> AlertSettings:
    keys = {
        "som": "sound", "notificacao_so": "os_notify", "volume": "volume", "silencio_inicio": "quiet_start",
        "silencio_fim": "quiet_end", "repetir_minutos": "repeat_minutes", "repetir_max": "repeat_max",
        "pergunta_minutos": "question_minutes", "parada_minutos": "stalled_minutes",
        "nao_recebida_minutos": "not_received_minutes", "concluida_minutos": "done_minutes",
        "silenciados": "muted_kinds",
    }  # fmt: skip
    values = {new: data[old] for old, new in keys.items() if old in data}
    return AlertSettings(**values)  # `silencio_inicio = ""` desliga o silêncio programado


def save_global(cfg: GlobalConfig) -> Path:
    data: dict[str, Any] = {
        "app_server": cfg.app_server_url,
        "delegador_url": cfg.delegator_url,
        **({"gerador_comando": cfg.generator_command} if cfg.generator_command else {}),
        "alertas": _clean(
            {
                "som": cfg.alerts.sound,
                "notificacao_so": cfg.alerts.os_notify,
                "volume": cfg.alerts.volume,
                "silencio_inicio": cfg.alerts.quiet_start or "",
                "silencio_fim": cfg.alerts.quiet_end or "",
                "repetir_minutos": cfg.alerts.repeat_minutes,
                "repetir_max": cfg.alerts.repeat_max,
                "pergunta_minutos": cfg.alerts.question_minutes,
                "parada_minutos": cfg.alerts.stalled_minutes,
                "nao_recebida_minutos": cfg.alerts.not_received_minutes,
                "concluida_minutos": cfg.alerts.done_minutes,
                "silenciados": cfg.alerts.muted_kinds or None,
            }
        ),
        "assinaturas": {
            name: _clean({"cli": s.cli, "perfil": s.profile, "nota": s.note}) for name, s in cfg.subscriptions.items()
        },
    }
    target = paths.global_config_file()
    atomic_write_text(target, tomli_w.dumps(data))
    return target
