"""Preparar o delegador de um projeto: mostra o que falta e resolve com um botão (serviço e hooks)."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from ..core import config
from ..core.models import Project, explain
from ..delegator import docker, setup
from ..delegator.install import install_claude
from . import theme as T


class DelegatorSetupScreen(ModalScreen[bool]):
    """Devolve True se o delegador ficou pronto para o projeto."""

    BINDINGS = [("escape", "close", "Fechar"), ("r", "recheck", "Verificar de novo")]

    def __init__(self, project: Project, checker=None, starter=None, installer=None) -> None:
        super().__init__()
        self.project = project
        self.url = config.load_global().delegator_url
        self._checker = checker or (lambda p, u: setup.project_checks(p, u))
        self._starter = starter or (lambda: docker.up())
        self._installer = installer or (lambda p, u: install_claude(p, url=u).format())
        self.checks: list[setup.Check] = []
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="pf-small"):
            yield Label(f"Preparar o delegador · {self.project.id}", classes="title")
            yield Static(Text("Verificando…", style=T.C_MUTED), id="ds-list")
            yield Static("", id="ds-status")
            with Horizontal(classes="buttons"):
                yield Button("Subir o serviço", id="ds-up", disabled=True)
                yield Button("Instalar no projeto", id="ds-install", disabled=True)
                yield Button("Verificar de novo", id="ds-recheck")
                yield Button("Fechar", id="ds-close")

    def on_mount(self) -> None:
        self.action_recheck()

    def action_close(self) -> None:
        self.dismiss(setup.ready(self.checks) if self.checks else False)

    def action_recheck(self) -> None:
        if not self.busy:
            self._run_checks()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id
        if wid == "ds-close":
            self.action_close()
        elif wid == "ds-recheck":
            self.action_recheck()
        elif wid == "ds-up" and not self.busy:
            self._act(
                "Subindo o serviço (a primeira vez constrói a imagem e pode levar alguns minutos)…", self._starter
            )
        elif wid == "ds-install" and not self.busy:
            self._act("Instalando hooks, agentes e MCP no projeto…", lambda: self._installer(self.project, self.url))

    # ------------------------------------------------------------------

    def _set_busy(self, message: str) -> None:
        self.busy = True
        self.query_one("#ds-status", Static).update(Text(message, style=T.C_BUSY))
        for wid in ("#ds-up", "#ds-install", "#ds-recheck"):
            self.query_one(wid, Button).disabled = True

    @staticmethod
    def _lines(result) -> str:
        return "\n".join(result) if isinstance(result, list) else str(result or "")

    def _act(self, message: str, func) -> None:
        self._set_busy(message)
        self.run_worker(self._do(func), exclusive=True, group="setup")

    async def _do(self, func) -> None:
        try:
            text = self._lines(await asyncio.to_thread(func))
        except Exception as exc:  # noqa: BLE001 - qualquer falha vira mensagem na tela
            text = f"Falhou: {explain(exc)}"
        self.busy = False
        await self._check(text)

    def _run_checks(self) -> None:
        self._set_busy("Verificando…")
        self.run_worker(self._check(""), exclusive=True, group="setup")

    async def _check(self, message: str) -> None:
        try:
            self.checks = await asyncio.to_thread(self._checker, self.project, self.url)
        except Exception as exc:  # noqa: BLE001
            self.checks = []
            message = f"{message}\nNão consegui verificar: {explain(exc)}".strip()
        self.busy = False
        t = Text()
        for c in self.checks:
            mark, color = ("✓", T.C_OK) if c.ok else (("○", T.C_MUTED) if c.ok is None else ("✗", T.C_OFF))
            t.append(f" {mark} ", style=color)
            t.append(f"{c.label:<34}", style=T.C_STRONG)
            t.append(f"{c.detail}\n", style=T.C_TEXT if c.ok else T.C_MUTED if c.ok is None else T.C_BUSY)
        self.query_one("#ds-list", Static).update(t)
        status = Text()
        if message:
            status.append(message.strip() + "\n\n", style=T.C_TEXT)
        if self.checks and setup.ready(self.checks):
            status.append("✓ Pronto. ", style=f"bold {T.C_OK}")
            status.append(
                "Sessões que já estavam abertas precisam ser reabertas (u) para carregar os hooks e o MCP; "
                "depois reenvie as instruções (Shift+R).",
                style=T.C_MUTED,
            )
        elif self.checks:
            status.append("Falta resolver o que está marcado com ✗.", style=T.C_BUSY)
        self.query_one("#ds-status", Static).update(status)
        fixes = {c.fix for c in self.checks if c.ok is False}
        self.query_one("#ds-up", Button).disabled = "up" not in fixes
        self.query_one("#ds-install", Button).disabled = "install" not in fixes
        self.query_one("#ds-recheck", Button).disabled = False
