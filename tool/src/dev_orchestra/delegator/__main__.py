"""Ponto de entrada do container: `python -m dev_orchestra.delegator` (não carrega a TUI)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..core import paths
from . import DEFAULT_PORT
from .server import serve


def _listen(argv: list[str]) -> int:
    """`python -m dev_orchestra.delegator listen ...`: o listener dos hooks, sem carregar a TUI nem o servidor."""
    from .client import DelegatorClient
    from .listener import agent_from_env, listen

    parser = argparse.ArgumentParser(prog="dev-orchestra-delegator listen")
    parser.add_argument("--project", required=True)
    parser.add_argument("--agent")
    parser.add_argument("--url", default=None)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--max-seconds", type=float, default=3540.0)
    args = parser.parse_args(argv)
    agent = args.agent or agent_from_env()
    if not agent:
        return 0  # sem identidade não há o que escutar (nem atrapalha a sessão)
    url = args.url
    if url is None:
        from ..core import config

        try:
            url = config.load_global().delegator_url
        except config.ConfigError:
            from . import DEFAULT_URL

            url = DEFAULT_URL
    return listen(
        DelegatorClient(url),
        args.project,
        agent,
        state_dir=paths.install_dir() / "listeners",
        interval=args.interval,
        max_seconds=args.max_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    import sys

    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "listen":
        return _listen(raw[1:])
    if raw and raw[0] == "signal":
        from .signals import main as signal_main

        return signal_main(raw[1:])
    parser = argparse.ArgumentParser(prog="dev-orchestra-delegator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=os.environ.get("DEV_ORCHESTRA_DB") or str(paths.install_dir() / "estado.db"))
    args = parser.parse_args(argv)
    serve(args.host, args.port, Path(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
