"""Plano e execução do envio das instruções a cada agente aberto."""

from __future__ import annotations

from dataclasses import dataclass

from . import liveness, state, tasks, trello
from .commands import local_context
from .liveness import OCIOSA, OCUPADA, Runner, StatusReport
from .models import GlobalConfig, Project
from .sender import SendResult, pointer_message, send_claude, send_codex, task_message, write_instruction_file


@dataclass(frozen=True)
class Item:
    role: str
    name: str
    cli: str
    action: str  # send | rename+send | skip
    reason: str
    thread_id: str | None = None  # Codex: thread a nomear (rename+send)
    resume: bool = False


def plan_instructions(
    project: Project, report: StatusReport, *, again: bool = False, only: set[str] | None = None
) -> list[Item]:
    """Decide, por papel, se as instruções são enviadas (nunca envia a sessão fechada nem repete sem `again`)."""
    items: list[Item] = []
    # threads do Codex ainda sem nome que poderiam ser as deste projeto (só as carregadas)
    candidates = [t for t in report.unnamed_codex if t.status in ("idle", "active")]
    codex_unnamed_roles = [
        r.role for r in report.roles if r.cli == "codex" and r.thread_id is None and (only is None or r.role in only)
    ]
    for status in report.roles:
        if only is not None and status.role not in only:
            continue
        name = status.name or project.agent_name(status.role)
        previous = state.sent_info(project.id, name, state.comm_mode(project.delegator))
        resume = previous is not None
        thread_id = status.thread_id
        action = "send"
        reason = "sessão aberta"
        if status.cli == "codex" and status.state not in (OCIOSA, OCUPADA):
            if thread_id is None and len(candidates) == 1 and len(codex_unnamed_roles) == 1:
                action, thread_id = "rename+send", candidates[0].id
                reason = f"há uma thread sem nome ({candidates[0].id[:8]}); será nomeada {name!r}"
            elif thread_id is None and candidates:
                items.append(Item(status.role, name, status.cli, "skip", "mais de uma thread sem nome; use /rename"))
                continue
            else:
                items.append(Item(status.role, name, status.cli, "skip", f"sessão {status.state}: {status.detail}"))
                continue
        elif status.state not in (OCIOSA, OCUPADA):
            items.append(Item(status.role, name, status.cli, "skip", f"sessão {status.state}: {status.detail}"))
            continue
        if previous is not None and not again:
            when = state.describe(previous)
            items.append(
                Item(status.role, name, status.cli, "skip", f"já {when}; use --again para reenviar", resume=True)
            )
            continue
        if status.state == OCUPADA:
            reason = "sessão ocupada: a mensagem chega ao fim do turno atual"
        items.append(Item(status.role, name, status.cli, action, reason, thread_id=thread_id, resume=resume))
    return items


def execute(
    project: Project,
    cfg: GlobalConfig,
    report: StatusReport,
    items: list[Item],
    *,
    run: Runner | None = None,
    rename=None,
) -> list[tuple[Item, SendResult]]:
    rename = rename or liveness.rename_codex_thread
    results: list[tuple[Item, SendResult]] = []
    ctx = local_context(project, workstreams=report.workstream_names, app_server_up=report.app_server_up)
    url = cfg.server_url_for(project)
    for item in items:
        if item.action == "skip":
            continue
        try:
            write_instruction_file(project, cfg, item.role, ctx)
        except OSError as exc:
            results.append((item, SendResult(False, "arquivo", f"não foi possível gravar as instruções: {exc}")))
            continue
        message = pointer_message(project, item.role, resume=item.resume)
        if item.cli == "codex":
            if item.action == "rename+send":
                if not item.thread_id or not rename(url, item.thread_id, item.name):
                    results.append((item, SendResult(False, "App Server", "não consegui nomear a thread")))
                    continue
            result = send_codex(url, item.name, message, run=run)
        else:
            result = send_claude(item.name, message, run=run)
        if result.ok:
            state.mark_sent(
                project.id, item.name, via=result.via, resume=item.resume, mode=state.comm_mode(project.delegator)
            )
            what = "Instruções de retomada" if item.resume else "Instruções"
            state.log_activity(project.id, f"{what} enviadas a {item.name} via {result.via}", "ok")
        else:
            state.log_activity(project.id, f"Falha ao enviar instruções a {item.name}: {result.detail}", "erro")
        results.append((item, result))
    return results


def _orchestrator_item(project: Project, report: StatusReport) -> tuple[Item | None, Item]:
    """(item pronto para enviar ou None, item de pulo explicando por quê)."""
    status = next((r for r in report.roles if r.role == "orchestrator"), None)
    if status is None:
        return None, Item("orchestrator", "-", "-", "skip", "o projeto não tem o papel orchestrator")
    name = status.name or project.agent_name("orchestrator")
    if status.state not in (OCIOSA, OCUPADA):
        return None, Item("orchestrator", name, status.cli, "skip", f"orquestrador {status.state}: {status.detail}")
    reason = "sessão aberta" if status.state == OCIOSA else "sessão ocupada: a mensagem chega ao fim do turno atual"
    if state.sent_info(project.id, name, state.comm_mode(project.delegator)) is None:
        reason += " (atenção: o orquestrador ainda não recebeu as instruções de papel por esta ferramenta)"
    ready = Item("orchestrator", name, status.cli, "send", reason, thread_id=status.thread_id)
    return ready, ready


def plan_task(project: Project, report: StatusReport, task_id: str, *, again: bool = False) -> Item:
    """Decide se a tarefa pode ser enviada ao orquestrador (sessão aberta; não repete sem `again`)."""
    tasks.read_task(project, task_id)  # TaskError se a tarefa não existir
    ready, outcome = _orchestrator_item(project, report)
    if ready is None:
        return outcome
    previous = state.task_sent_info(project.id, task_id)
    if previous is not None and not again:
        reason = f"tarefa já enviada ({state.task_sent_label(previous)}); use --again"
        return Item("orchestrator", ready.name, ready.cli, "skip", reason)
    return ready


def plan_trello_import(project: Project, report: StatusReport, *, again: bool = False) -> Item:
    """Decide se o pedido de importação do Trello pode ser enviado ao orquestrador."""
    if project.task_source.kind != "trello":
        return Item("orchestrator", "-", "-", "skip", "a fonte de tarefas do projeto não é o Trello")
    ready, outcome = _orchestrator_item(project, report)
    if ready is None:
        return outcome
    previous = state.trello_import_info(project.id)
    if previous is not None and not again:
        reason = f"importação já enviada ({state.task_sent_label(previous)}); use --again"
        return Item("orchestrator", ready.name, ready.cli, "skip", reason)
    return ready


def execute_trello_import(
    project: Project, cfg: GlobalConfig, item: Item, *, run: Runner | None = None
) -> tuple[Item, SendResult]:
    trello.write_import_file(project)
    message = trello.import_message(project)
    if item.cli == "codex":
        result = send_codex(cfg.server_url_for(project), item.name, message, run=run)
    else:
        result = send_claude(item.name, message, run=run)
    if result.ok:
        state.mark_trello_import(project.id, item.name, via=result.via)
        state.log_activity(project.id, f"Importação do Trello pedida a {item.name} via {result.via}", "ok")
    else:
        state.log_activity(project.id, f"Falha ao pedir a importação do Trello: {result.detail}", "erro")
    return item, result


def execute_task(
    project: Project,
    cfg: GlobalConfig,
    task_id: str,
    item: Item,
    *,
    run: Runner | None = None,
) -> tuple[Item, SendResult]:
    task = tasks.read_task(project, task_id)
    message = task_message(project, task)
    if item.cli == "codex":
        result = send_codex(cfg.server_url_for(project), item.name, message, run=run)
    else:
        result = send_claude(item.name, message, run=run)
    if result.ok:
        state.mark_task_sent(project.id, task_id, item.name, via=result.via)
        state.log_activity(project.id, f"Tarefa {task_id} enviada a {item.name} via {result.via}", "ok")
    else:
        state.log_activity(project.id, f"Falha ao enviar a tarefa {task_id}: {result.detail}", "erro")
    return item, result


def format_plan(items: list[Item]) -> str:
    lines = []
    for item in items:
        mark = {"send": "enviar", "rename+send": "nomear e enviar", "skip": "pular"}[item.action]
        lines.append(f"{item.name} ({item.cli}): {mark} — {item.reason}")
    return "\n".join(lines) or "Nenhum agente no projeto."


def format_results(results: list[tuple[Item, SendResult]]) -> str:
    if not results:
        return "Nada foi enviado."
    return "\n".join(f"{'OK ' if r.ok else 'FALHOU'} {i.name} via {r.via}: {r.detail}" for i, r in results)
