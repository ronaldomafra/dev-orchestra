"""Subcomandos `dev-orchestra delegator ...` (a TUI usa as mesmas funções)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from ..core import config, paths
from . import DEFAULT_PORT, docker
from .client import DelegatorClient, DelegatorUnavailable
from .install import install_claude
from .listener import agent_from_env, listen


class DelegatorCommandError(Exception):
    pass


def _client(args) -> DelegatorClient:
    url = getattr(args, "url", None) or config.load_global().delegator_url
    return DelegatorClient(url)


def _project(project_id: str):
    try:
        return config.load_project(project_id)
    except config.ConfigError as exc:
        raise DelegatorCommandError(str(exc)) from exc


def cmd_serve(args, out: TextIO, services) -> int:
    from .server import serve

    serve(args.host, args.port, Path(args.db) if args.db else paths.install_dir() / "estado.db")
    return 0


def cmd_docker(args, out: TextIO, services) -> int:
    action = args.docker_command
    if action == "up":
        lines = docker.up(args.port, rebuild=args.rebuild)
    elif action == "down":
        lines = docker.down()
    elif action == "status":
        state = docker.container_state()
        healthy = _client(args).health()
        lines = [f"Container: {state or 'não instalado'}", f"Serviço: {'respondendo' if healthy else 'sem resposta'}"]
    else:
        lines = [f"docker logs {docker.CONTAINER} --tail 80   (rode no terminal)"]
    print("\n".join(lines), file=out)
    return 0


def cmd_install(args, out: TextIO, services) -> int:
    project = _project(args.project)
    report = install_claude(project, url=_client(args).url, exe=args.exe, scope=args.scope, write=not args.dry_run)
    print(report.format(), file=out)
    if not project.delegator:
        print(
            "Obs.: o cadastro ainda não está em modo delegador; ative `delegador = true` em "
            f"{paths.project_file(project.id)} (ou na edição do projeto na TUI).",
            file=out,
        )
    return 1 if report.warnings and not args.dry_run else 0


def cmd_listen(args, out: TextIO, services) -> int:
    agent = args.agent or agent_from_env()
    if not agent:
        print("dev-orchestra listener: nome do agente desconhecido (defina DEV_ORCHESTRA_AGENT).", file=sys.stderr)
        return 0  # não atrapalha a sessão: sem identidade não há o que escutar
    return listen(
        _client(args),
        args.project,
        agent,
        state_dir=paths.install_dir() / "listeners",
        interval=args.interval,
        max_seconds=args.max_seconds,
    )


def cmd_status(args, out: TextIO, services) -> int:
    from .core import Delegator

    client = _client(args)
    try:
        data = client.status(args.project)
    except DelegatorUnavailable as exc:
        raise DelegatorCommandError(
            f"o delegador não responde em {client.url} (rode `dev-orchestra delegator docker up`)"
        ) from exc
    except RuntimeError as exc:
        raise DelegatorCommandError(str(exc)) from exc
    print(Delegator.render_status(data), file=out)
    return 0


def cmd_events(args, out: TextIO, services) -> int:
    try:
        events = _client(args).events(args.project, args.limit)
    except (DelegatorUnavailable, RuntimeError) as exc:
        raise DelegatorCommandError(str(exc)) from exc
    for e in reversed(events):
        print(f"{e['ts']}  {e['kind']:<9} {e['text']}", file=out)
    return 0


def cmd_cancel(args, out: TextIO, services) -> int:
    try:
        result = _client(args).cancel(args.project, args.task, args.reason or "")
    except (DelegatorUnavailable, RuntimeError) as exc:
        raise DelegatorCommandError(str(exc)) from exc
    print(f"Tarefa {result['task_id']}: {result['task_state']}", file=out)
    return 0


def cmd_profile(args, out: TextIO, services) -> int:
    try:
        result = _client(args).push_profile(args.project, args.role)
    except (DelegatorUnavailable, RuntimeError) as exc:
        raise DelegatorCommandError(str(exc)) from exc
    print(f"Perfil de {args.role} enviado a {len(result['sessions'])} sessão(ões) viva(s).", file=out)
    return 0


def add_parser(sub) -> None:
    root = sub.add_parser("delegator", help="delegador MCP: comunicação controlada entre as sessões do projeto")
    root.add_argument("--url", help="endereço do delegador (padrão: o da configuração global)")
    dsub = root.add_subparsers(dest="delegator_command", required=True)

    serve = dsub.add_parser("serve", help="roda o servidor no terminal (sem Docker; para desenvolvimento)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--db", help="arquivo SQLite (padrão: <instalação>/estado.db)")
    serve.set_defaults(func=cmd_serve)

    dk = dsub.add_parser("docker", help="instala e controla o container do delegador")
    dk.add_argument("docker_command", choices=["up", "down", "status", "logs"])
    dk.add_argument("--port", type=int, default=DEFAULT_PORT)
    dk.add_argument("--rebuild", action="store_true", help="up: reconstrói a imagem e recria o container")
    dk.set_defaults(func=cmd_docker)

    ins = dsub.add_parser(
        "install", help="registra o delegador no projeto (servidor MCP, permissões e hooks do Claude)"
    )
    ins.add_argument("project")
    ins.add_argument("--exe", help="caminho do executável dev-orchestra usado pelos hooks")
    ins.add_argument("--scope", default="local", choices=["local", "project", "user"])
    ins.add_argument("--dry-run", action="store_true")
    ins.set_defaults(func=cmd_install)

    ls = dsub.add_parser("listen", help="(usado pelos hooks) escuta mensagens pendentes e acorda a sessão")
    ls.add_argument("--project", required=True)
    ls.add_argument("--agent")
    ls.add_argument("--interval", type=float, default=2.0)
    ls.add_argument("--max-seconds", type=float, default=3540.0)
    ls.set_defaults(func=cmd_listen)

    st = dsub.add_parser("status", help="sessões e tarefas no delegador")
    st.add_argument("project")
    st.set_defaults(func=cmd_status)

    ev = dsub.add_parser("events", help="histórico do delegador")
    ev.add_argument("project")
    ev.add_argument("--limit", type=int, default=30)
    ev.set_defaults(func=cmd_events)

    ca = dsub.add_parser("cancel", help="cancela uma tarefa delegada (a pessoa pode, sem sessão)")
    ca.add_argument("project")
    ca.add_argument("task")
    ca.add_argument("--reason")
    ca.set_defaults(func=cmd_cancel)

    pr = dsub.add_parser("profile", help="reenvia o perfil do papel às sessões vivas")
    pr.add_argument("project")
    pr.add_argument("role")
    pr.set_defaults(func=cmd_profile)
