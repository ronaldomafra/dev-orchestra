"""Observador: registra no Monitor o que muda fora do delegador (tarefas nos arquivos e sessões dos agentes).

No modo Claude Code os agentes conversam por SendMessage, que esta ferramenta não enxerga. O que ela consegue ver são os
arquivos de tarefa e o status das sessões; a cada ciclo compara com o retrato anterior e anota as mudanças na atividade
do projeto, que é o que o Monitor mostra. A primeira observação só guarda o retrato (não inventa histórico).
"""

from __future__ import annotations

from datetime import datetime

from . import state, tasks
from .liveness import FECHADA, OCIOSA, OCUPADA, StatusReport
from .models import Project

STATE_PT = {
    "draft": "rascunho",
    "ready": "pronta",
    "delegated": "delegada",
    "running": "em andamento",
    "asked": "com pergunta",
    "done": "concluída",
    "partial": "parcial",
    "blocked": "bloqueada",
    "failed": "falhou",
    "needs_review": "em revisão",
    "cancelled": "cancelada",
}
_KIND = {"done": "ok", "failed": "erro", "blocked": "aviso", "partial": "aviso", "needs_review": "info"}
_AGENT_STATES = (OCIOSA, OCUPADA, FECHADA)


def _snapshot(project: Project, report: StatusReport | None) -> tuple[dict, dict[str, tasks.Task]]:
    found, _warnings = tasks.list_tasks(project)
    by_id = {t.id: t for t in found}
    snap: dict = {
        "tarefas": {t.id: {"estado": t.state, "resultado": bool(t.result_text())} for t in found},
        "agentes": {},
    }
    if report is not None:
        snap["agentes"] = {
            r.name or project.agent_name(r.role): r.state for r in report.roles if r.state in _AGENT_STATES
        }
    return snap, by_id


def observe(project: Project, report: StatusReport | None = None, *, now: datetime | None = None) -> list[str]:
    """Compara com o retrato anterior, anota as mudanças e devolve os textos anotados."""
    snap, by_id = _snapshot(project, report)
    before = state.observed(project.id)
    if before is None:  # primeira vez: só guarda
        state.save_observed(project.id, snap)
        return []
    events: list[tuple[str, str]] = []
    old_tasks = before.get("tarefas", {})
    for tid, now_info in snap["tarefas"].items():
        task = by_id[tid]
        old = old_tasks.get(tid)
        if old is None:
            events.append(
                (f"Nova tarefa {tid} «{task.title}» → {task.role} ({STATE_PT.get(task.state, task.state)})", "info")
            )
            continue
        if old.get("estado") != now_info["estado"]:
            label = STATE_PT.get(now_info["estado"], now_info["estado"])
            was = STATE_PT.get(old.get("estado"), old.get("estado"))
            events.append((f"Tarefa {tid}: {was} → {label}", _KIND.get(now_info["estado"], "info")))
        if now_info["resultado"] and not old.get("resultado"):
            events.append((f"Tarefa {tid}: resultado registrado no arquivo", "ok"))
    old_agents = before.get("agentes", {})
    for agent, new_state in snap["agentes"].items():
        previous = old_agents.get(agent)
        if previous is None or previous == new_state:
            continue
        text = {
            OCUPADA: f"{agent} começou a trabalhar",
            OCIOSA: f"{agent} terminou o turno" if previous == OCUPADA else f"{agent}: sessão aberta (ociosa)",
            FECHADA: f"{agent}: sessão fechada",
        }[new_state]
        events.append((text, "aviso" if new_state == FECHADA else "info"))
    for text, kind in events:
        state.log_activity(project.id, text, kind, when=now)
    state.save_observed(project.id, snap)
    return [t for t, _k in events]
