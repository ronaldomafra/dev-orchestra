"""Status das sessões: abertas ou não, usando o que as CLIs já oferecem.

Fontes (verificadas em 2026-11-14):
- Claude Code: `claude agents --json` (sessões interativas desta máquina);
- Codex: App Server (`thread/list` por pasta e `thread/loaded/list`);
- AI Memory: `ai-memory workstreams --project <projeto>` (último uso de cada workstream).

Nenhuma fonte é obrigatória: se uma falhar, o papel correspondente fica como `desconhecida`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .models import GlobalConfig, Project

# Estados exibidos por papel
OCIOSA = "ociosa"
OCUPADA = "ocupada"
FECHADA = "fechada"
DESCONHECIDA = "desconhecida"
ERRO = "erro"

Runner = Callable[..., tuple[int, str]]


def run_command(
    args: list[str], cwd: str | None = None, timeout: float = 15.0, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """Executa um comando e devolve (código, stdout). Falhas viram (127 ou 124, '').

    O stdin é fechado (evita que a CLI espere entrada) e `env` acrescenta variáveis ao ambiente atual."""
    try:
        done = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout


def _same_dir(a: str, b: str) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


# ------------------------------------------------------------- Claude Code


@dataclass(frozen=True)
class ClaudeSession:
    pid: int | None
    cwd: str
    name: str | None
    session_id: str | None
    status: str | None
    kind: str | None


def parse_claude_agents(text: str) -> list[ClaudeSession]:
    """Interpreta a saída JSON de `claude agents --json`, tolerando campos ausentes."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("saída inesperada de 'claude agents --json' (esperava uma lista)")
    sessions = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sessions.append(
            ClaudeSession(
                pid=item.get("pid"),
                cwd=str(item.get("cwd", "")),
                name=item.get("name"),
                session_id=item.get("sessionId"),
                status=item.get("status"),
                kind=item.get("kind"),
            )
        )
    return sessions


def claude_sessions(run: Runner = run_command) -> list[ClaudeSession] | None:
    code, out = run(["claude", "agents", "--json"])
    if code != 0:
        return None
    try:
        return parse_claude_agents(out)
    except (ValueError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ Codex


@dataclass(frozen=True)
class CodexThread:
    id: str
    name: str | None
    cwd: str | None
    status: str  # notLoaded | idle | active | systemError


def _rpc(ws, request_id: int, method: str, params: dict, timeout: float) -> dict:
    ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
    while True:
        message = json.loads(ws.recv(timeout=timeout))
        if message.get("id") == request_id and ("result" in message or "error" in message):
            if "error" in message:
                raise RuntimeError(f"{method}: {json.dumps(message['error'])[:200]}")
            return message["result"]


def codex_threads(url: str, cwd: str, *, timeout: float = 3.0) -> list[CodexThread] | None:
    """Threads do projeto no App Server, ou None se ele estiver inacessível."""
    from websockets.sync.client import connect

    try:
        with connect(url, open_timeout=timeout, max_size=8_000_000) as ws:
            _rpc(
                ws,
                1,
                "initialize",
                {
                    "clientInfo": {"name": "dev-orchestra", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": False},
                },
                timeout,
            )
            ws.send(json.dumps({"method": "initialized"}))
            loaded = set(_rpc(ws, 2, "thread/loaded/list", {}, timeout).get("data", []))
            listed = _rpc(ws, 3, "thread/list", {"cwd": cwd, "limit": 50}, timeout).get("data", [])
    except Exception:  # noqa: BLE001 - qualquer falha de rede/protocolo = App Server inacessível
        return None
    threads = []
    for item in listed:
        status = item.get("status") or {}
        kind = status.get("type") if isinstance(status, dict) else None
        if kind is None:
            kind = "idle" if item.get("id") in loaded else "notLoaded"
        threads.append(CodexThread(id=item.get("id", ""), name=item.get("name"), cwd=item.get("cwd"), status=kind))
    return threads


def rename_codex_thread(url: str, thread_id: str, name: str, *, timeout: float = 3.0) -> bool:
    """Dá nome a uma thread do Codex (`thread/name/set` no App Server). True se o servidor aceitou."""
    from websockets.sync.client import connect

    try:
        with connect(url, open_timeout=timeout, max_size=8_000_000) as ws:
            _rpc(
                ws,
                1,
                "initialize",
                {
                    "clientInfo": {"name": "dev-orchestra", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": False},
                },
                timeout,
            )
            ws.send(json.dumps({"method": "initialized"}))
            _rpc(ws, 2, "thread/name/set", {"threadId": thread_id, "name": name}, timeout)
    except Exception:  # noqa: BLE001
        return False
    return True


# -------------------------------------------------------------- AI Memory


@dataclass(frozen=True)
class Workstream:
    name: str
    last_used: str
    harnesses: str


_WS_LINE = re.compile(r"^\s*\*?\s*(?P<name>[A-Za-z0-9_.-]+)\s{2,}(?P<when>.+?)\s{2,}\[(?P<harness>[^\]]*)\]\s*$")


def parse_workstreams(text: str) -> list[Workstream]:
    found = []
    for line in text.splitlines():
        match = _WS_LINE.match(line)
        if match:
            found.append(Workstream(match["name"], match["when"].strip(), match["harness"].strip()))
    return found


def ai_memory_workstreams(
    project_name: str, cwd: str | None = None, run: Runner = run_command
) -> list[Workstream] | None:
    """Workstreams do projeto. O AI Memory lista os do *checkout atual*, então o comando roda em `cwd`."""
    code, out = run(["ai-memory", "workstreams", "--project", project_name], cwd)
    if code != 0:
        return None
    return parse_workstreams(out)


_PROJ_LINE = re.compile(r"^(?P<ws>\S+)\s{2,}(?P<proj>\S+)\s{2,}[0-9a-f]{8}-[0-9a-f-]{20,}")


def parse_projects(text: str) -> list[tuple[str, str]]:
    """(workspace, projeto) de cada linha da saída de `ai-memory list-projects`."""
    found = []
    for line in text.splitlines():
        match = _PROJ_LINE.match(line)
        if match:
            found.append((match["ws"], match["proj"]))
    return found


def ai_memory_projects(run: Runner = run_command) -> list[tuple[str, str]] | None:
    code, out = run(["ai-memory", "list-projects"])
    if code != 0:
        return None
    return parse_projects(out)


# ----------------------------------------------------------------- status


@dataclass(frozen=True)
class RoleStatus:
    role: str
    cli: str
    state: str
    detail: str
    name: str = ""  # identidade do agente: [prefixo]-[papel]
    thread_id: str | None = None  # Codex: thread encontrada com esse nome
    raw_status: str | None = None  # Claude: status cru de `claude agents` (`waiting` = espera a pessoa)


@dataclass(frozen=True)
class AiMemoryState:
    reachable: bool | None = None  # None = não consultado
    project_exists: bool | None = None
    workstreams: int | None = None


@dataclass
class Services:
    """Fontes de status injetáveis (os testes e a TUI trocam por simulações)."""

    claude_sessions: Callable[[], list[ClaudeSession] | None] = claude_sessions
    codex_threads: Callable[[str, str], list[CodexThread] | None] = codex_threads
    workstreams: Callable[[str, str], list[Workstream] | None] = ai_memory_workstreams
    ai_memory_projects: Callable[[], list[tuple[str, str]] | None] = ai_memory_projects
    scan_project: Callable[[Path], object] | None = None  # None = importer.scan
    delegator_status: Callable[[str, str], dict | None] | None = None  # (url, projeto) -> dict ou None (fora do ar)
    delegator_events: Callable[[str, str], list[dict]] | None = None  # (url, projeto) -> eventos recentes
    # monitor (todos recebem a URL do delegador; None = usa o cliente HTTP)
    delegator_timeline: Callable[[str, str], list[dict] | None] | None = None  # (url, projeto) -> eventos ricos
    delegator_stats: Callable[[str, str | None], dict | None] | None = None  # (url, projeto|None) -> números
    delegator_message: Callable[[str, str, int], dict | None] | None = None  # (url, projeto, id) -> mensagem
    delegator_task: Callable[[str, str, str], list[dict]] | None = None  # (url, projeto, tarefa) -> histórico
    delegator_alerts: Callable[[str, str], list[dict] | None] | None = None  # (url, projeto) -> sinais dos hooks


@dataclass
class StatusReport:
    roles: list[RoleStatus]
    workstream_names: set[str] | None
    app_server_up: bool | None
    notes: list[str] = field(default_factory=list)
    unnamed_codex: list[CodexThread] = field(default_factory=list)  # threads do projeto ainda sem nome
    ai_memory: AiMemoryState = field(default_factory=AiMemoryState)
    delegator: dict | None = None  # estado no delegador (só no modo delegador; None = fora do ar ou não usado)
    delegator_used: bool = False
    delegator_events: list[dict] = field(default_factory=list)


def delegator_status(url: str, project_id: str) -> dict | None:
    """Estado do projeto no delegador (HTTP), ou None se o serviço não responder."""
    from ..delegator.client import DelegatorClient, DelegatorUnavailable

    try:
        return DelegatorClient(url, timeout=2.0).status(project_id)
    except (DelegatorUnavailable, RuntimeError, ValueError):
        return None


def delegator_events(url: str, project_id: str) -> list[dict]:
    from ..delegator.client import DelegatorClient, DelegatorUnavailable

    try:
        return DelegatorClient(url, timeout=2.0).events(project_id, 8)
    except (DelegatorUnavailable, RuntimeError, ValueError):
        return []


_CODEX_RANK = {"active": 3, "idle": 2, "systemError": 1, "notLoaded": 0}


def describe_ai_memory(project: Project, state: AiMemoryState) -> str:
    """Frase curta sobre o cadastro do projeto no AI Memory (primeira vez ou retomada)."""
    name = project.ai_memory_name()
    if state.reachable is None:
        return "AI Memory: não consultado."
    if not state.reachable:
        return (
            "AI Memory: indisponível (ai-memory list-projects falhou); não dá para saber se é primeira vez ou retomada."
        )
    if not state.project_exists:
        return (
            f"AI Memory: o projeto {name!r} ainda não existe; será criado quando a primeira sessão abrir "
            "(primeira vez)."
        )
    count = (
        ""
        if state.workstreams is None
        else f" com {state.workstreams} workstream{'s' if state.workstreams != 1 else ''}"
    )
    return f"AI Memory: o projeto {name!r} já existe{count} (retomada)."


def _claude_status(name: str, project: Project, sessions: list[ClaudeSession] | None) -> tuple[str, str]:
    if sessions is None:
        return DESCONHECIDA, "não foi possível executar 'claude agents --json'"
    mine = [s for s in sessions if s.name == name and _same_dir(s.cwd, str(project.path))]
    if not mine:
        return FECHADA, f"nenhuma sessão aberta chamada {name!r} nesta pasta"
    first = mine[0]
    state = OCUPADA if first.status == "busy" else OCIOSA
    extra = f" ({len(mine)} sessões com esse nome)" if len(mine) > 1 else ""
    return state, f"pid {first.pid}, status {first.status or '?'}{extra}"


def _codex_status(name: str, url: str, threads: list[CodexThread] | None) -> tuple[str, str, str | None]:
    if threads is None:
        return DESCONHECIDA, f"App Server inacessível em {url}", None
    mine = sorted((t for t in threads if t.name == name), key=lambda t: _CODEX_RANK.get(t.status, 0), reverse=True)
    if not mine:
        return FECHADA, f"nenhuma thread chamada {name!r} neste projeto", None
    best = mine[0]
    if best.status == "active":
        return OCUPADA, f"thread {best.id[:8]}", best.id
    if best.status == "idle":
        return OCIOSA, f"thread {best.id[:8]}", best.id
    if best.status == "systemError":
        return ERRO, f"thread {best.id[:8]} em erro do sistema", best.id
    return FECHADA, f"thread {best.id[:8]} existe, mas não está carregada no App Server", best.id


def collect_status(project: Project, cfg: GlobalConfig, services: Services | None = None) -> StatusReport:
    services = services or Services()
    needs_claude = any(r.cli == "claude" for r in project.roles.values())
    needs_codex = any(r.cli == "codex" for r in project.roles.values())
    url = cfg.server_url_for(project)

    claude = services.claude_sessions() if needs_claude else None
    codex = services.codex_threads(url, str(project.path)) if needs_codex else None
    workstreams = services.workstreams(project.ai_memory_name(), str(project.path))
    by_name = {w.name: w for w in workstreams} if workstreams is not None else None
    known = services.ai_memory_projects()

    roles: list[RoleStatus] = []
    for role_id in project.role_order():
        cli = project.roles[role_id].cli
        name = project.agent_name(role_id)
        thread_id = None
        if cli == "claude":
            state, detail = _claude_status(name, project, claude)
        else:
            state, detail, thread_id = _codex_status(name, url, codex)
        if by_name is not None:
            ws = by_name.get(name)
            if ws:
                detail += f"; workstream usado {ws.last_used}"
            else:
                detail += "; sem workstream"
                if role_id in by_name:
                    detail += f" (há um antigo chamado {role_id!r}, sem prefixo; o nome novo cria outro)"
        raw = None
        if cli == "claude" and claude is not None:  # o status cru do Claude (`waiting` = espera a pessoa)
            raw = next((s.status for s in claude if s.name == name and _same_dir(s.cwd, str(project.path))), None)
        roles.append(RoleStatus(role_id, cli, state, detail, name=name, thread_id=thread_id, raw_status=raw))

    ai = AiMemoryState(
        reachable=known is not None,
        project_exists=(
            (project.ai_memory_workspace, project.ai_memory_name()) in known if known is not None else None
        ),
        workstreams=len(by_name) if by_name is not None else None,
    )
    notes: list[str] = []
    if known is not None and not ai.project_exists:
        pass  # projeto novo: `ai-memory workstreams` falha de propósito; não é um erro
    elif workstreams is None:
        notes.append("não foi possível consultar os workstreams do AI Memory (ai-memory workstreams)")
    deleg = None
    deleg_events: list[dict] = []
    if project.delegator:
        deleg = (services.delegator_status or delegator_status)(cfg.delegator_url, project.id)
        if deleg is not None:
            deleg_events = (services.delegator_events or delegator_events)(cfg.delegator_url, project.id)
    return StatusReport(
        roles=roles,
        workstream_names=set(by_name) if by_name is not None else (set() if ai.project_exists is False else None),
        app_server_up=(codex is not None) if needs_codex else None,
        notes=notes,
        unnamed_codex=[t for t in (codex or []) if not t.name],
        ai_memory=ai,
        delegator=deleg,
        delegator_used=project.delegator,
        delegator_events=deleg_events,
    )
