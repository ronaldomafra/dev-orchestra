"""Listener do hook `asyncRewake` do Claude Code: acorda a sessão ociosa quando há mensagem pendente.

Roda fora do modelo (não é polling do agente). Só **lê** a contagem no delegador; o conteúdo da mensagem só sai
por `receive`. Um único listener por agente (trava de arquivo); o hook `Stop` o rearma depois de cada turno.
Código de saída 2 = acordar a sessão com a mensagem escrita em stderr.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .client import DelegatorClient, DelegatorUnavailable

DEFAULT_MAX_SECONDS = 3540.0  # um pouco abaixo do `timeout` de 3600 s do hook
DEFAULT_INTERVAL = 2.0
RENOTIFY_SECONDS = (
    300.0  # o mesmo conjunto de mensagens só acorda de novo depois disso (evita laço se o agente ignorar)
)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _name_from_argv(argv: list[str]) -> str | None:
    """`claude --name X ...` (ou `--name=X`, `-n X`) -> X."""
    for index, arg in enumerate(argv):
        if arg in ("--name", "-n") and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--name="):
            return arg.split("=", 1)[1]
    return None


def agent_from_process(pid: int | None = None, proc: str = "/proc") -> str | None:
    """Nome da sessão, lido da linha de comando do processo `claude` que é ancestral deste (o hook roda dentro dele).

    Cobre sessões abertas sem `DEV_ORCHESTRA_AGENT` (por exemplo, com um comando de subida antigo)."""
    current = pid if pid is not None else os.getpid()
    for _ in range(12):
        try:
            argv = Path(f"{proc}/{current}/cmdline").read_bytes().decode(errors="replace").split("\0")
            stat = Path(f"{proc}/{current}/stat").read_text()
        except OSError:
            return None
        if argv and os.path.basename(argv[0]) == "claude":
            name = _name_from_argv(argv)
            if name:
                return name
        try:
            current = int(stat.rsplit(")", 1)[1].split()[1])  # PPID
        except (IndexError, ValueError):
            return None
        if current <= 1:
            return None
    return None


def agent_from_env(env: dict[str, str] | None = None, *, from_process: bool = True) -> str | None:
    """Quem é esta sessão: `DEV_ORCHESTRA_AGENT`; senão o `--name` do processo `claude`; senão o workstream.

    Valores com cara de UUID (o id do workstream) nunca servem de nome."""
    env = os.environ if env is None else env
    value = env.get("DEV_ORCHESTRA_AGENT")
    if value:
        return value
    if from_process:
        found = agent_from_process()
        if found:
            return found
    value = env.get("AI_MEMORY_WORKSTREAM")
    return value if value and not _UUID.match(value) else None


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def notice(info: dict) -> str:
    ids = ", ".join(str(i) for i in info.get("ids", []))
    types = ", ".join(sorted(set(info.get("types", [])))) or "?"
    if "cancel" in info.get("types", []):
        return (
            f"dev-orchestra: CANCELAMENTO no delegador (ids: {ids}). Chame agora a ferramenta `receive` com o seu "
            "session_id, interrompa o trabalho da tarefa cancelada e não chame `report` para ela. "
            "Este aviso não traz conteúdo."
        )
    return (
        f"dev-orchestra: {info.get('count', 0)} mensagem(ns) pendente(s) no delegador (ids: {ids}; tipos: {types}). "
        "Chame a ferramenta `receive` com o seu session_id para obtê-las "
        "(se ainda não se registrou, chame `register_session` antes). Este aviso não traz conteúdo."
    )


def listen(
    client: DelegatorClient,
    project: str,
    agent: str,
    *,
    state_dir: Path,
    interval: float = DEFAULT_INTERVAL,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    out: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> int:
    """0 = nada a fazer (tempo esgotado ou já há outro listener); 2 = acordar a sessão (aviso em `out`)."""
    out = out or sys.stderr
    state_dir.mkdir(parents=True, exist_ok=True)
    key = _safe(f"{project}__{agent}")
    lock = (state_dir / f"{key}.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return 0  # já existe um listener armado para este agente
    notified_file = state_dir / f"{key}.notified.json"
    try:
        deadline = clock() + max_seconds
        while True:
            try:
                info = client.pending(project, agent)
            except DelegatorUnavailable:
                info = None
            except RuntimeError:  # projeto desconhecido etc.: não adianta insistir
                return 0
            if info and info.get("count", 0) > 0 and _should_notify(notified_file, info, wall()):
                out.write(notice(info) + "\n")
                out.flush()
                return 2
            if clock() >= deadline:
                return 0
            sleep(interval)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _should_notify(path: Path, info: dict, now: float) -> bool:
    ids = sorted(info.get("ids", []))
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if last.get("ids") == ids and now - float(last.get("at", 0)) < RENOTIFY_SECONDS:
        return False
    try:
        path.write_text(json.dumps({"ids": ids, "at": now}), encoding="utf-8")
    except OSError:
        pass
    return True
