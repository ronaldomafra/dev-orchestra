"""Como cada CLI é acordada quando chega uma mensagem. O aviso nunca leva conteúdo: só manda chamar `receive`."""

from __future__ import annotations

import json
import threading
from typing import Any

from ..core.liveness import _rpc
from ..core.models import DEFAULT_APP_SERVER_URL, Project
from .core import NullWaker

NOTICE = (
    "dev-orchestra: há mensagem pendente no delegador para o seu papel. "
    "Chame a ferramenta `receive` (com o seu session_id) para obtê-la. Este aviso não traz conteúdo."
)


class CodexWaker:
    """Codex: o delegador é cliente do App Server e inicia um turno curto na thread (`turn/start`).

    IMPLEMENTADO MAS NÃO HOMOLOGADO (a homologação do Codex ficou para depois): a conta Codex do autor estava sem
    plano e a entrega a uma thread em execução ainda precisa de teste. Rede: requer `--network host` no container.
    """

    def __init__(
        self, default_url: str = DEFAULT_APP_SERVER_URL, *, timeout: float = 5.0, background: bool = True
    ) -> None:
        self.default_url = default_url
        self.timeout = timeout
        self.background = background

    def notify(self, project: Project, role: str, session: dict[str, Any] | None, message_id: int) -> str:
        if not session:
            return "no codex session registered for this role yet (message stays pending)"
        url = project.app_server_url or self.default_url
        thread_id = str(session["ref"])
        if self.background:
            threading.Thread(target=self._send, args=(url, thread_id), daemon=True).start()
            return "turn/start requested"
        return self._send(url, thread_id)

    def _send(self, url: str, thread_id: str) -> str:
        from websockets.sync.client import connect

        try:
            with connect(url, open_timeout=self.timeout, max_size=8_000_000) as ws:
                _rpc(
                    ws,
                    1,
                    "initialize",
                    {
                        "clientInfo": {"name": "dev-orchestra-delegator", "version": "0.1.0"},
                        "capabilities": {"experimentalApi": False},
                    },
                    self.timeout,
                )
                ws.send(json.dumps({"method": "initialized"}))
                params = {"threadId": thread_id, "input": [{"type": "text", "text": NOTICE}]}
                try:
                    _rpc(ws, 2, "turn/start", params, self.timeout)
                except RuntimeError:
                    # thread descarregada: reabre e tenta de novo (thread/resume está no esquema do App Server)
                    _rpc(ws, 3, "thread/resume", {"threadId": thread_id}, self.timeout)
                    _rpc(ws, 4, "turn/start", params, self.timeout)
        except Exception as exc:  # noqa: BLE001
            return f"codex wake failed: {exc}"
        return "turn/start sent"


class RoutingWaker:
    """Escolhe o despertador pela CLI do destino (decisão do cadastro do papel)."""

    def __init__(self, codex: CodexWaker | None = None) -> None:
        self.claude = NullWaker()
        self.codex = codex or CodexWaker()

    def notify(self, project: Project, role: str, session: dict[str, Any] | None, message_id: int) -> str:
        cli = project.roles[role].cli if role in project.roles else "claude"
        waker = self.codex if cli == "codex" else self.claude
        return waker.notify(project, role, session, message_id)
