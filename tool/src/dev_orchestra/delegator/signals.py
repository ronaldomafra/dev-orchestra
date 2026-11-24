"""Comando dos hooks de alerta do Claude Code: avisa o delegador que a sessão parou esperando a pessoa.

`python -m dev_orchestra.delegator signal --project P --kind permission|question|turn_error|clear`

Lê o JSON do hook no stdin (melhor esforço: o texto vem de `message`, `tool_name`/`tool_input`, `error`...), nunca
escreve no stdout (num hook `PermissionRequest` o stdout seria lido como decisão) e sempre sai com 0: um alerta que
falha não pode atrapalhar a sessão. Cada chamada tem prazo curto. Cada payload recebido vai para um log pequeno
(`<instalação>/listeners/sinais.log`) para diagnóstico.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from ..core import config, paths
from .client import DelegatorClient
from .listener import agent_from_env

LOG_LIMIT = 150_000


def _text(value: Any, limit: int = 400) -> str:
    return " ".join(str(value).split())[:limit]


def describe(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    """(mensagem curta, detalhe) a partir do que o hook entregou; tolera campos ausentes."""
    message = _text(payload.get("message") or payload.get("error") or payload.get("error_message") or "")
    detail = ""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        if tool == "AskUserQuestion":
            questions = tool_input.get("questions")
            if isinstance(questions, list):
                texts = [q.get("question", "") for q in questions if isinstance(q, dict)]
                message = _text(" · ".join(t for t in texts if t)) or message
                detail = json.dumps(questions, ensure_ascii=False)[:1500]
        else:
            command = tool_input.get("command") or tool_input.get("file_path") or tool_input.get("path")
            shown = _text(command, 300) if command else _text(json.dumps(tool_input, ensure_ascii=False), 300)
            message = f"{tool}: {shown}" if tool else message
            detail = json.dumps(tool_input, ensure_ascii=False)[:1500]
    if not message:
        message = {"permission": "a sessão espera a sua aprovação", "question": "a sessão espera a sua resposta",
                   "turn_error": "o turno terminou com erro"}.get(kind, "")  # fmt: skip
    if kind == "turn_error" and payload:
        detail = detail or json.dumps(payload, ensure_ascii=False)[:1500]
    return message, detail


def _log(payload_text: str, args: argparse.Namespace) -> None:
    try:
        path = paths.install_dir() / "listeners" / "sinais.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%S')} kind={args.kind} agent={args.agent} {payload_text[:2000]}\n"
            )
    except OSError:
        pass


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dev-orchestra-delegator signal")
    parser.add_argument("--project", required=True)
    parser.add_argument("--kind", required=True, choices=["permission", "question", "turn_error", "clear"])
    parser.add_argument("--agent")
    parser.add_argument("--url")
    parser.add_argument("--timeout", type=float, default=1.5)
    args = parser.parse_args(argv)
    try:
        agent = args.agent or agent_from_env()
        if not agent:
            return 0
        raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        args.agent = agent
        if args.kind == "permission" and payload.get("tool_name") == "AskUserQuestion":
            args.kind = "question"  # perguntar à pessoa também passa pelo pedido de permissão: é um só alerta
        _log(raw, args)
        url = args.url
        if url is None:
            try:
                url = config.load_global().delegator_url
            except config.ConfigError:
                from . import DEFAULT_URL

                url = DEFAULT_URL
        client = DelegatorClient(url, timeout=args.timeout)
        if args.kind == "clear":
            client.clear_signals(args.project, agent)
        else:
            message, detail = describe(args.kind, payload)
            client.signal(args.project, agent, args.kind, message, detail)
    except Exception:  # noqa: BLE001 - um alerta que falha nunca atrapalha a sessão
        pass
    return 0


__all__ = ["main", "describe"]
