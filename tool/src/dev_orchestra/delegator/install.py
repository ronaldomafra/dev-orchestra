"""Instalação do delegador numa pasta de projeto para o Claude Code: servidor MCP, permissões e hooks do listener."""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import atomic_write_text
from ..core.liveness import Runner, run_command
from ..core.models import Project
from . import DEFAULT_URL, MCP_NAME

LISTENER_MARK = "delegator listen"
HOOK_TIMEOUT = 3600
# Quando o listener é (re)armado: ao abrir, a cada turno, a cada ferramenta usada (a sessão ocupada também precisa ser
# avisada de um cancelamento), ao terminar e quando o Claude avisa que está parado (cobre turnos interrompidos).
# O listener tem trava por agente: as chamadas repetidas terminam na hora.
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "Notification")
PERMISSION = f"mcp__{MCP_NAME}"  # libera todas as ferramentas deste servidor


def default_exe() -> str:
    found = shutil.which("dev-orchestra")
    return found or f"{sys.executable} -m dev_orchestra"


def listener_command(project_id: str, exe: str | None = None, url: str | None = None) -> str:
    """Comando do hook. Sem `exe`, usa o Python desta instalação (`-m dev_orchestra.delegator listen`): é leve
    (só biblioteca padrão), importante porque o hook roda a cada ferramenta usada."""
    if exe:
        base = f"{exe} delegator listen --project {project_id}"
    else:
        base = f"{sys.executable} -m dev_orchestra.delegator listen --project {project_id}"
    return base + (f" --url {url}" if url and url != DEFAULT_URL else "")


WORKER_TOOLS = ("register_session", "heartbeat", "receive", "report", "ask", "project_status")
ORCHESTRATOR_TOOLS = ("register_session", "heartbeat", "delegate", "receive", "answer", "cancel_task", "project_status")


def patch_agent_tools(text: str, tools: tuple[str, ...]) -> tuple[str, bool]:
    """Definições de agente do Claude Code com `tools:` explícito não enxergam o MCP: acrescenta as do delegador."""
    match = re.search(r"\A(---\n)(.*?)(\n---\n)", text, flags=re.S)
    if not match:
        return text, False
    lines = match.group(2).split("\n")
    for index, line in enumerate(lines):
        if line.startswith("tools:"):
            current = [t.strip() for t in line[len("tools:") :].split(",") if t.strip()]
            wanted = [f"mcp__{MCP_NAME}__{t}" for t in tools if f"mcp__{MCP_NAME}__{t}" not in current]
            if not wanted:
                return text, False
            lines[index] = "tools: " + ", ".join([*current, *wanted])
            return match.group(1) + "\n".join(lines) + match.group(3) + text[match.end() :], True
    return text, False  # sem `tools:`: a definição herda todas as ferramentas, MCP incluído


def patch_agent_defs(project: Project, *, write: bool = True) -> list[str]:
    folder = project.path / ".claude" / "agents"
    changed: list[str] = []
    if not folder.is_dir():
        return changed
    for file in sorted(folder.glob("*.md")):
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tools = ORCHESTRATOR_TOOLS if re.search(r"orquest|orchest", file.stem, re.I) else WORKER_TOOLS
        new, did = patch_agent_tools(text, tools)
        if did:
            if write:
                atomic_write_text(file, new)
            changed.append(f"agente {file.stem}: ferramentas do delegador")
    return changed


SIGNAL_MARK = "delegator signal"
SIGNAL_TIMEOUT = 10
# (evento, matcher, tipo): o que avisa o delegador que a sessão parou esperando a pessoa, e quando deixou de esperar.
# `async`: os hooks de alerta nunca seguram o diálogo de permissão nem o turno.
SIGNAL_HOOKS: tuple[tuple[str, str | None, str], ...] = (
    ("PermissionRequest", None, "permission"),
    ("Notification", "permission_prompt", "permission"),
    ("PreToolUse", "AskUserQuestion", "question"),
    ("Notification", "elicitation_dialog|agent_needs_input", "question"),
    ("StopFailure", None, "turn_error"),
    ("Notification", "idle_prompt", "clear"),  # parada esperando texto (por exemplo, depois de um Esc): nada a decidir
    ("PostToolUse", None, "clear"),
    ("UserPromptSubmit", None, "clear"),
    ("Stop", None, "clear"),
    ("SessionEnd", None, "clear"),
)


def signal_base(project_id: str) -> str:
    return f"{sys.executable} -m dev_orchestra.delegator signal --project {project_id}"


def _merge_signal_hooks(data: dict[str, Any], base: str, changes: list[str]) -> None:
    hooks = data.setdefault("hooks", {})
    for event, matcher, kind in SIGNAL_HOOKS:
        command = f"{base} --kind {kind}"
        entry = {"type": "command", "command": command, "async": True, "timeout": SIGNAL_TIMEOUT}
        groups = hooks.setdefault(event, [])
        found = False
        for group in groups:
            if group.get("matcher") != matcher:
                continue
            for hook in group.get("hooks", []):
                text = str(hook.get("command", ""))
                if SIGNAL_MARK in text and f"--kind {kind}" in text:
                    found = True
                    if hook != entry:
                        hook.clear()
                        hook.update(entry)
                        changes.append(f"hook de alerta {event}/{kind} atualizado")
        if not found:
            group = {"hooks": [entry]}
            if matcher:
                group = {"matcher": matcher, **group}
            groups.append(group)
            changes.append(f"hook de alerta {event}/{kind}")


def merge_settings(
    existing: dict[str, Any], command: str, signal_command: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Acrescenta a permissão e os hooks do listener sem tocar no resto. Idempotente. Devolve (novo, mudanças)."""
    data = json.loads(json.dumps(existing))  # cópia profunda
    changes: list[str] = []
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    if PERMISSION not in allow:
        allow.append(PERMISSION)
        changes.append(f"permissão {PERMISSION}")
    hooks = data.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        entry = {"type": "command", "command": command, "async": True, "asyncRewake": True, "timeout": HOOK_TIMEOUT}
        found = False
        for group in groups:
            for hook in group.get("hooks", []):
                if LISTENER_MARK in str(hook.get("command", "")):
                    found = True
                    if hook != entry:  # comando ou parâmetros mudaram: atualiza o nosso hook
                        hook.clear()
                        hook.update(entry)
                        changes.append(f"hook {event} atualizado")
        if not found:
            groups.append({"hooks": [entry]})
            changes.append(f"hook {event}")
    if signal_command:
        _merge_signal_hooks(data, signal_command, changes)
    return data, changes


@dataclass
class InstallReport:
    settings_path: Path
    changes: list[str] = field(default_factory=list)
    mcp: str = ""
    warnings: list[str] = field(default_factory=list)

    def format(self) -> str:
        lines = [f"Delegador no projeto ({self.settings_path.parent.parent}):"]
        lines.append("  " + (", ".join(self.changes) if self.changes else "settings.json já estava atualizado"))
        lines.append(f"  servidor MCP: {self.mcp}")
        lines += [f"  aviso: {w}" for w in self.warnings]
        return "\n".join(lines)


def install_claude(
    project: Project,
    *,
    url: str = DEFAULT_URL,
    exe: str | None = None,
    scope: str = "local",
    run: Runner = run_command,
    write: bool = True,
) -> InstallReport:
    """Grava `.claude/settings.json` (permissão e hooks) e registra o servidor MCP na CLI (`claude mcp add`)."""
    settings_path = project.path / ".claude" / "settings.json"
    report = InstallReport(settings_path=settings_path)
    try:
        existing = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except (OSError, ValueError) as exc:
        report.warnings.append(f"não consegui ler {settings_path}: {exc}; nada foi alterado")
        return report
    merged, report.changes = merge_settings(
        existing,
        listener_command(project.id, exe, url),
        signal_base(project.id) + (f" --url {url}" if url != DEFAULT_URL else ""),
    )
    report.changes += patch_agent_defs(project, write=write)
    if any(not c.startswith("agente ") for c in report.changes) and write:
        atomic_write_text(settings_path, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    code, _ = run(["claude", "mcp", "get", MCP_NAME], cwd=str(project.path), timeout=20.0)
    if code == 0:
        report.mcp = "já registrado"
    elif write:
        code, _ = run(
            ["claude", "mcp", "add", "--transport", "http", "--scope", scope, MCP_NAME, f"{url}/mcp"],
            cwd=str(project.path),
            timeout=30.0,
        )
        report.mcp = f"registrado ({scope})" if code == 0 else "FALHOU: rode `claude mcp add` manualmente"
        if code != 0:
            report.warnings.append(f"claude mcp add --transport http --scope {scope} {MCP_NAME} {url}/mcp")
    else:
        report.mcp = "seria registrado"
    return report
