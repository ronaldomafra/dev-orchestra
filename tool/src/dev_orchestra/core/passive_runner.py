"""Executor dos agentes passivos: roda no host (onde estão as CLIs) e atende a fila do delegador.

A cada volta: bate no delegador (está vivo, quais agentes atende), lê os pedidos pendentes e, para cada agente livre,
pega o mais antigo (`claim`), roda a CLI uma vez (`passive.execute`) e devolve o resultado (`finish`). Um pedido por
vez por agente (fila); agentes diferentes rodam em paralelo. Não é um agente: não decide nada, só executa.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from . import passive, paths

DEFAULT_INTERVAL = 2.0
STALE_MINUTES_MARGIN = 5.0  # um pedido `running` além do tempo limite do agente + margem volta para a fila


def runner_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class PassiveRunner:
    def __init__(
        self,
        client,
        *,
        name: str | None = None,
        load_agents: Callable[[], list[passive.PassiveAgent]] = passive.list_agents,
        execute: Callable[..., passive.Outcome] = passive.execute,
        max_parallel: int = 4,
        out: TextIO | None = None,
    ) -> None:
        self.client = client
        self.name = name or runner_name()
        self.load_agents = load_agents
        self.execute = execute
        self.max_parallel = max(1, max_parallel)
        self.out = out or sys.stdout
        self._busy: dict[str, int] = {}  # agente -> pedido em execução
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def log(self, text: str) -> None:
        print(f"{time.strftime('%H:%M:%S')}  {text}", file=self.out, flush=True)

    def busy(self) -> dict[str, int]:
        with self._lock:
            return dict(self._busy)

    def tick(self, *, wait: bool = False) -> int:
        """Uma volta. Devolve quantos pedidos começaram. `wait` espera terminarem (testes e `--once`)."""
        agents = {a.id: a for a in self.load_agents()}
        self.client.passive_heartbeat(self.name, os.getpid(), sorted(agents))
        started = 0
        for item in self.client.passive_pending():
            agent_id, consult_id = item["agent"], int(item["consult_id"])
            with self._lock:
                if agent_id in self._busy or len(self._busy) >= self.max_parallel:
                    continue
            try:
                claimed = self.client.passive_claim(consult_id, self.name)
            except RuntimeError as exc:  # outro executor levou, ou o agente está ocupado: tenta na próxima volta
                self.log(f"#{consult_id}: não peguei ({exc})")
                continue
            agent = agents.get(agent_id)
            if agent is None:
                self.client.passive_finish(consult_id, "failed", error=f"agente '{agent_id}' não existe mais")
                self.log(f"#{consult_id}: agente {agent_id} não existe mais")
                continue
            with self._lock:
                self._busy[agent_id] = consult_id
            thread = threading.Thread(
                target=self._work, args=(agent, consult_id, claimed.get("instruction") or ""), daemon=True
            )
            thread.start()
            self._threads.append(thread)
            started += 1
            self.log(f"#{consult_id}: {agent_id} começou ({claimed.get('origin') or 'sem origem'})")
        self._threads = [t for t in self._threads if t.is_alive()]
        if wait:
            for thread in list(self._threads):
                thread.join()
        return started

    def _work(self, agent: passive.PassiveAgent, consult_id: int, instruction: str) -> None:
        try:
            outcome = self.execute(agent, instruction)
        except Exception as exc:  # noqa: BLE001 - nunca deixa o pedido preso
            outcome = passive.Outcome(False, error=f"erro no executor: {exc}")
        try:
            self.client.passive_finish(
                consult_id,
                "done" if outcome.ok else "failed",
                result=outcome.result,
                error=outcome.error,
                usage=outcome.usage or None,
                duration_ms=outcome.duration_ms,
                model=outcome.model,
            )
            tokens = int(outcome.usage.get("input_tokens", 0)) + int(outcome.usage.get("output_tokens", 0))
            verdict = "respondeu" if outcome.ok else f"falhou: {outcome.error[:120]}"
            self.log(f"#{consult_id}: {agent.id} {verdict} · {outcome.duration_ms / 1000:.1f} s · {tokens} tokens")
        except Exception as exc:  # noqa: BLE001 - o delegador caiu: o pedido volta para a fila mais tarde
            self.log(f"#{consult_id}: não consegui devolver o resultado ({exc})")
        finally:
            with self._lock:
                self._busy.pop(agent.id, None)

    def requeue_stale(self) -> None:
        agents = self.load_agents()
        longest = max((a.timeout for a in agents), default=passive.DEFAULT_TIMEOUT)
        try:
            count = self.client.passive_requeue(longest / 60 + STALE_MINUTES_MARGIN)
        except Exception:  # noqa: BLE001
            return
        if count:
            self.log(f"{count} pedido(s) presos voltaram para a fila")

    def serve(self, interval: float = DEFAULT_INTERVAL, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.log(f"executor {self.name} no ar · agentes: {', '.join(a.id for a in self.load_agents()) or 'nenhum'}")
        self.requeue_stale()
        last_requeue = time.monotonic()
        failures = 0
        while not stop.is_set():
            try:
                self.tick()
                if failures:
                    self.log("delegador de volta")
                failures = 0
            except Exception as exc:  # noqa: BLE001 - delegador fora do ar: espera e tenta de novo
                failures += 1
                if failures in (1, 10) or failures % 100 == 0:
                    self.log(f"delegador indisponível ({exc}); tentando de novo")
            if time.monotonic() - last_requeue > 300:
                self.requeue_stale()
                last_requeue = time.monotonic()
            stop.wait(interval)
        if self._threads:
            self.log("parando: esperando as consultas em execução terminarem")
        for thread in list(self._threads):
            thread.join()
        self.log("executor parado")


# ------------------------------------------------------ executor em segundo plano (TUI)


def pid_file() -> Path:
    return paths.install_dir() / "agentes" / "executor.pid"


def log_file() -> Path:
    return paths.install_dir() / "agentes" / "executor.log"


def background_pid() -> int | None:
    """PID do executor iniciado pela TUI, se ainda estiver vivo."""
    try:
        pid = int(pid_file().read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:  # processo zumbi (terminou, mas a TUI que o iniciou ainda não o recolheu) conta como parado
        if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return None
    except (OSError, IndexError):
        pass
    return pid


def start_background(url: str | None = None) -> int:
    """Inicia `dev-orchestra passive run` desacoplado da TUI (continua se a TUI fechar)."""
    pid = background_pid()
    if pid:
        return pid
    log = log_file()
    log.parent.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", "dev_orchestra", "passive", "run"] + (["--url", url] if url else [])
    with log.open("a", encoding="utf-8") as fh:
        proc = subprocess.Popen(  # noqa: S603 - o próprio pacote
            args, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True
        )
    pid_file().write_text(str(proc.pid))
    return proc.pid


def stop_background() -> bool:
    pid = background_pid()
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    pid_file().unlink(missing_ok=True)
    return True
