"""Remover um agente (papel) de um projeto: o que é apagado, o que fica e por quê.

Só some o que esta ferramenta gerou para o papel. Arquivos do método (`roles/<papel>.md`), definições de agente
escritas à mão, tarefas, a memória durável (AI Memory) e o histórico do delegador nunca são tocados.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config, paths, portable, profiles, sender, state, tasks
from .config import atomic_write_text
from .models import Project

AGENT_DEF_MARK = profiles.AGENT_DEF_MARK
_LEGACY_SIGNATURE = "Ao abrir, registre-se no delegador (`register_session`) e siga o perfil devolvido"


def generated_agent_def(text: str) -> bool:
    """A definição de agente foi escrita por esta ferramenta (e não à mão ou copiada do método)?"""
    return AGENT_DEF_MARK in text or _LEGACY_SIGNATURE in text


@dataclass
class Plan:
    project: Project
    role: str
    agent: str
    remove: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: str = ""
    files: list[Path] = field(default_factory=list)
    forget_state: bool = False
    re_export: bool = False

    def text(self) -> str:
        lines = [f"Remover o agente {self.agent} (papel {self.role}) do projeto {self.project.id}?", ""]
        if self.blocked:
            return "\n".join([*lines[:1], "", f"Não é possível: {self.blocked}"])
        lines.append("Será apagado:")
        lines += [f"  ✗ {item}" for item in self.remove]
        if self.keep:
            lines += ["", "Não será tocado:"] + [f"  = {item}" for item in self.keep]
        if self.warnings:
            lines += ["", "Atenção:"] + [f"  ▲ {item}" for item in self.warnings]
        return "\n".join(lines)


def plan_removal(project: Project, role: str, *, live_session: bool = False) -> Plan:
    agent = project.agent_name(role)
    plan = Plan(project, role, agent)
    if role not in project.roles:
        plan.blocked = f"o projeto não tem o papel {role!r}"
        return plan
    if len(project.roles) == 1:
        plan.blocked = "é o único papel do projeto (o cadastro precisa de ao menos um)"
        return plan
    plan.remove.append(f"o papel {role} do cadastro do projeto")
    for path, what in (
        (profiles.profile_path(project, role), "perfil no projeto"),
        (profiles.install_path(project, role), "cópia do perfil na instalação (a que o delegador lê)"),
        (sender.instruction_path(project, role), "instruções geradas de abertura"),
    ):
        if path.is_file():
            plan.remove.append(f"{what}: {_short(project, path)}")
            plan.files.append(path)
    definition = profiles.agent_def_path(project, role)
    if definition.is_file():
        try:
            mine = generated_agent_def(definition.read_text(encoding="utf-8"))
        except OSError:
            mine = False
        if mine:
            plan.remove.append(f"definição de agente gerada: {_short(project, definition)}")
            plan.files.append(definition)
        else:
            plan.keep.append(
                f"{_short(project, definition)} (não foi gerado por esta ferramenta; apague à mão se quiser)"
            )
    info = state.load(project.id)
    sent = (agent in (info.get("instrucoes") or {})) or any(
        isinstance(v, dict) and v.get("para") == agent for v in (info.get("tarefas") or {}).values()
    )
    if sent:
        plan.forget_state = True
        plan.remove.append("registro local de instruções e tarefas enviadas a este agente")
    if portable.export_path(project).is_file():
        plan.re_export = True
        plan.remove.append(f"o papel na configuração exportada ({_short(project, portable.export_path(project))})")
    if (project.path / "roles" / f"{role}.md").is_file():
        plan.keep.append(f"roles/{role}.md (arquivo do método)")
    plan.keep.append("AI Memory: o workstream e a memória durável não são apagados")
    plan.keep.append("delegador: o histórico de sessões e tarefas fica no registro de auditoria")
    try:
        pending, _ = tasks.list_tasks(project)
    except (OSError, ValueError):
        pending = []
    mine_tasks = [t.id for t in pending if t.role == role]
    if mine_tasks:
        plan.warnings.append(
            f"{len(mine_tasks)} tarefa(s) apontam para o papel ({', '.join(mine_tasks[:5])}): continuam como estão; "
            "reatribua-as"
        )
    if live_session:
        plan.warnings.append(f"a sessão {agent} está aberta: feche-a (ela continuará existindo fora do cadastro)")
    if role == "orchestrator":
        plan.warnings.append("sem orquestrador o projeto não tem quem delegue as tarefas")
    return plan


def _short(project: Project, path: Path) -> str:
    try:
        return str(path.relative_to(project.path))
    except ValueError:
        return str(path)


def execute(plan: Plan) -> list[str]:
    """Aplica o plano: primeiro o cadastro (a fonte), depois os arquivos. Devolve o relato do que foi feito."""
    if plan.blocked:
        raise ValueError(plan.blocked)
    project = plan.project
    remaining = {r: cfg for r, cfg in project.roles.items() if r != plan.role}
    updated = project.model_copy(update={"roles": remaining})
    config.save_project(updated)
    done = [f"papel {plan.role} removido do cadastro"]
    for path in plan.files:
        try:
            path.unlink(missing_ok=True)
            done.append(f"apagado {_short(project, path)}")
            if path.parent.name == project.id and path.parent.parent.name == "perfis":
                _rmdir_if_empty(path.parent)
        except OSError as exc:
            done.append(f"não consegui apagar {_short(project, path)}: {exc}")
    if plan.forget_state:
        _forget(project.id, plan.agent)
        done.append("registro de envios do agente limpo")
    if plan.re_export:
        portable.export_project(updated)
        done.append("configuração exportada atualizada")
    state.log_activity(project.id, f"Agente {plan.agent} removido do projeto", "info")
    return done


def _rmdir_if_empty(folder: Path) -> None:
    try:
        folder.rmdir()
    except OSError:
        pass


def _forget(project_id: str, agent: str) -> None:
    data = state.load(project_id)
    (data.get("instrucoes") or {}).pop(agent, None)
    tarefas = data.get("tarefas") or {}
    for key in [k for k, v in tarefas.items() if isinstance(v, dict) and v.get("para") == agent]:
        tarefas.pop(key)
    atomic_write_text(paths.state_file(project_id), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
