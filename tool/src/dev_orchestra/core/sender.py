"""Envio das instruções a uma sessão aberta.

- Codex: `codex queue` pelo App Server (valida a sessão pelo nome e a acorda se estiver ociosa).
- Claude Code: `SendMessage`, a ferramenta documentada de mensagens entre sessões. Ela só roda dentro de uma sessão
  Claude, então usamos uma sessão auxiliar curta (`claude -p`, Haiku, esforço baixo) que a chama. Não escrevemos no
  socket das sessões: o formato para postar de fora não é documentado.

A mensagem enviada é curta e aponta o arquivo `.dev-orchestra/abertura/<agente>.md` (as instruções completas ficam
lá), o que mantém o texto íntegro mesmo passando por um modelo.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import liveness
from .commands import LaunchContext, opening_prompt, role_title, transport_mode
from .config import atomic_write_text
from .liveness import Runner
from .models import GlobalConfig, Project
from .tasks import Task

RELAY_NAME = "dev-orchestra-relay"
INSTRUCTIONS_DIR = ".dev-orchestra/abertura"


@dataclass(frozen=True)
class SendResult:
    ok: bool
    via: str
    detail: str


def instruction_path(project: Project, role: str) -> Path:
    return project.path / INSTRUCTIONS_DIR / f"{project.agent_name(role)}.md"


def instruction_rel(project: Project, role: str) -> str:
    return f"{INSTRUCTIONS_DIR}/{project.agent_name(role)}.md"


def write_instruction_file(project: Project, cfg: GlobalConfig, role: str, ctx: LaunchContext) -> Path:
    """Grava (ou regrava) as instruções completas do agente. O arquivo é gerado pela ferramenta."""
    text = opening_prompt(project, cfg, role, transport_mode(project), ctx, force=True) or ""
    header = (
        f"# Instruções de {project.agent_name(role)} ({role_title(role)})\n\n"
        "<!-- Gerado por dev-orchestra e regerado a cada envio. Edite os arquivos do método "
        "(roles/, AGENTS.md), não este. -->\n\n"
    )
    path = instruction_path(project, role)
    atomic_write_text(path, header + text.strip() + "\n")
    return path


def pointer_message(project: Project, role: str, *, resume: bool) -> str:
    name = project.agent_name(role)
    rel = instruction_rel(project, role)
    if resume:
        return (
            f"Retomada do projeto {project.id}: você continua sendo {name} ({role_title(role)}). "
            f"Releia {rel} e siga as instruções desse arquivo. Responda apenas confirmando o papel; "
            "não inicie nenhuma tarefa nova."
        )
    return (
        f"Você é {name}, papel {role_title(role)} do projeto {project.id}. Leia agora {rel} e siga as instruções "
        "desse arquivo. Responda apenas confirmando o papel; não inicie nenhuma tarefa."
    )


def task_message(project: Project, task: Task) -> str:
    """Mensagem (uma linha) que entrega uma tarefa ao orquestrador; o conteúdo fica no arquivo da tarefa."""
    rel = f"{project.tasks_dir}/{task.id}.md"
    base = f"Nova tarefa do projeto {project.id}: {task.id} — {task.title}. Leia {rel}."
    if task.role == "orchestrator":
        return (
            f"{base} Esta tarefa é sua: confirme objetivo e critérios com a minha aprovação antes de executar e, "
            "ao concluir, atualize o campo estado e a seção ## Resultado do arquivo."
        )
    executor = project.agent_name(task.role)
    if project.delegator:
        return (
            f"{base} Papel executor indicado: {task.role}. Siga o seu papel de Orchestrator: confirme objetivo e "
            "critérios, recomende modelo e esforço, peça a minha aprovação e só então chame `delegate` (ferramenta "
            "do delegador, com user_approved=true). Não implemente a tarefa você mesmo e não use SendMessage."
        )
    return (
        f"{base} Papel executor indicado: {task.role} ({executor}). Siga o seu papel de Orchestrator: confirme "
        "objetivo e critérios, recomende MODEL, EFFORT e MODEL_REASON, peça a minha aprovação e só então delegue. "
        "Não implemente a tarefa você mesmo."
    )


def send_codex(url: str, thread: str, message: str, *, run: Runner | None = None) -> SendResult:
    code, out = (run or liveness.run_command)(
        ["codex", "queue", "--remote", url, "--thread", thread, "--message", message], None, 30.0
    )
    first = (out.strip().splitlines() or [""])[0]
    if code == 0 and "Queued message" in out:
        return SendResult(True, "codex queue", first)
    return SendResult(False, "codex queue", first or f"codex queue falhou (código {code})")


def send_claude(name: str, message: str, *, run: Runner | None = None, model: str = "haiku") -> SendResult:
    prompt = (
        f'Use a ferramenta SendMessage para enviar à sessão chamada "{name}" EXATAMENTE este texto, sem alterar '
        f'nada e sem acrescentar nada:\n\n{message}\n\nDepois responda apenas "ok" se o envio foi aceito, ou '
        '"falhou: <motivo>".'
    )
    args = [
        "claude",
        "-p",
        "--model",
        model,
        "--effort",
        "low",
        "--no-session-persistence",
        "--name",
        RELAY_NAME,
        "--allowedTools",
        "SendMessage ListAgents",
        "--output-format",
        "json",
        prompt,
    ]
    # cwd neutro (não carrega o CLAUDE.md do projeto) e captura do AI Memory desligada para a sessão auxiliar
    code, out = (run or liveness.run_command)(
        args, tempfile.gettempdir(), 120.0, {"AI_MEMORY_CAPTURE_OWNER": RELAY_NAME}
    )
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return SendResult(False, "SendMessage", f"resposta ilegível da sessão auxiliar (código {code})")
    answer = str(data.get("result", "")).strip()
    if code == 0 and not data.get("is_error") and answer.lower().startswith("ok"):
        return SendResult(True, "SendMessage", f"enviado por {RELAY_NAME}")
    return SendResult(False, "SendMessage", answer[:200] or "a sessão auxiliar não confirmou o envio")
