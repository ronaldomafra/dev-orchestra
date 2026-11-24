"""Estado local por projeto: quais agentes já receberam as instruções (na pasta de instalação)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import paths
from .config import atomic_write_text


def load(project_id: str) -> dict[str, Any]:
    try:
        data = json.loads(paths.state_file(project_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def sent_info(project_id: str, agent_name: str, mode: str | None = None) -> dict[str, Any] | None:
    """Quando as instruções foram enviadas. Com `mode`, instruções de outro modo de comunicação (padrão ou
    delegador) não contam: o texto mudou e o agente precisa recebê-lo de novo."""
    info = load(project_id).get("instrucoes", {}).get(agent_name)
    if not isinstance(info, dict):
        return None
    if mode is not None and info.get("modo", "padrao") != mode:
        return None
    return info


def comm_mode(delegator: bool) -> str:
    return "delegador" if delegator else "padrao"


def mark_sent(
    project_id: str,
    agent_name: str,
    *,
    via: str,
    resume: bool,
    when: datetime | None = None,
    mode: str = "padrao",
) -> None:
    data = load(project_id)
    when = when or datetime.now().astimezone()
    data.setdefault("instrucoes", {})[agent_name] = {
        "enviado_em": when.isoformat(timespec="seconds"),
        "via": via,
        "retomada": resume,
        "modo": mode,
    }
    atomic_write_text(paths.state_file(project_id), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def task_sent_info(project_id: str, task_id: str) -> dict[str, Any] | None:
    info = load(project_id).get("tarefas", {}).get(task_id)
    return info if isinstance(info, dict) else None


def mark_task_sent(project_id: str, task_id: str, agent_name: str, *, via: str, when: datetime | None = None) -> None:
    data = load(project_id)
    when = when or datetime.now().astimezone()
    data.setdefault("tarefas", {})[task_id] = {
        "enviada_em": when.isoformat(timespec="seconds"),
        "para": agent_name,
        "via": via,
    }
    atomic_write_text(paths.state_file(project_id), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def task_sent_label(info: dict[str, Any] | None) -> str:
    """`-` ou `15/11 09:30 → demo-orchestrator`."""
    if not info:
        return "-"
    try:
        hora = datetime.fromisoformat(info["enviada_em"]).strftime("%d/%m %H:%M")
    except (KeyError, ValueError):
        hora = "?"
    return f"{hora} → {info.get('para', '?')}"


def describe_short(info: dict[str, Any] | None) -> str:
    """`✓ 14/11 09:30` ou `pendentes` (versão curta para tabelas)."""
    if not info:
        return "pendentes"
    try:
        hora = datetime.fromisoformat(info["enviado_em"]).strftime("%d/%m %H:%M")
    except (KeyError, ValueError):
        hora = "?"
    return f"✓ {hora}"


def trello_import_info(project_id: str) -> dict[str, Any] | None:
    info = load(project_id).get("importacao_trello")
    return info if isinstance(info, dict) else None


def mark_trello_import(project_id: str, agent_name: str, *, via: str, when: datetime | None = None) -> None:
    data = load(project_id)
    when = when or datetime.now().astimezone()
    data["importacao_trello"] = {"enviada_em": when.isoformat(timespec="seconds"), "para": agent_name, "via": via}
    atomic_write_text(paths.state_file(project_id), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


ACTIVITY_LIMIT = 200


def log_activity(project_id: str, text: str, kind: str = "info", *, when: datetime | None = None) -> None:
    """Registra um evento para a tela do projeto (`ok`, `info`, `aviso` ou `erro`); guarda os últimos 200."""
    data = load(project_id)
    when = when or datetime.now().astimezone()
    items = data.get("atividade")
    items = items if isinstance(items, list) else []
    items.append({"em": when.isoformat(timespec="seconds"), "texto": text, "tipo": kind})
    data["atividade"] = items[-ACTIVITY_LIMIT:]
    atomic_write_text(paths.state_file(project_id), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def recent_activity(project_id: str, limit: int = 6) -> list[dict[str, Any]]:
    """Eventos mais recentes primeiro."""
    items = load(project_id).get("atividade")
    if not isinstance(items, list):
        return []
    return [i for i in reversed(items) if isinstance(i, dict)][:limit]


def ago(iso: str | None, *, now: datetime | None = None) -> str:
    """`agora`, `há 5 min`, `há 3 h`, `há 2 dias`."""
    if not iso:
        return "-"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "?"
    now = now or datetime.now().astimezone()
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "agora"
    if seconds < 3600:
        return f"há {seconds // 60} min"
    if seconds < 86400:
        return f"há {seconds // 3600} h"
    days = seconds // 86400
    return f"há {days} dia{'s' if days > 1 else ''}"


def clear(project_id: str, agent_name: str) -> None:
    data = load(project_id)
    if data.get("instrucoes", {}).pop(agent_name, None) is not None:
        atomic_write_text(paths.state_file(project_id), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def describe(info: dict[str, Any] | None) -> str:
    """`enviadas 14:02 (via codex queue)` ou `pendentes`."""
    if not info:
        return "pendentes"
    try:
        hora = datetime.fromisoformat(info["enviado_em"]).strftime("%d/%m %H:%M")
    except (KeyError, ValueError):
        hora = "?"
    return f"enviadas {hora}"


def observed(project_id: str) -> dict[str, Any] | None:
    """Último retrato de tarefas e agentes visto pelo observador (None = ainda não observou)."""
    snap = load(project_id).get("observado")
    return snap if isinstance(snap, dict) else None


def save_observed(project_id: str, snapshot: dict[str, Any]) -> None:
    data = load(project_id)
    data["observado"] = snapshot
    atomic_write_text(paths.state_file(project_id), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
