"""Subcomandos `dev-orchestra agents ...`: remover um agente (papel) do projeto com a limpeza do que foi gerado."""

from __future__ import annotations

from typing import TextIO

from .core import config, removal
from .core.liveness import collect_status


def cmd_remove(args, out: TextIO, services) -> int:
    from .cli import CliError

    project = config.load_project(args.project)
    live = False
    if args.role in project.roles and services is not None:
        try:
            report = collect_status(project, config.load_global(), services)
            live = any(r.role == args.role and r.state in ("ociosa", "ocupada") for r in report.roles)
        except Exception:  # noqa: BLE001 - o aviso de sessão aberta é opcional
            live = False
    plan = removal.plan_removal(project, args.role, live_session=live)
    print(plan.text(), file=out)
    if plan.blocked:
        return 2
    if not args.yes:
        print("\n(nada foi alterado; repita com --yes para remover)", file=out)
        return 0
    try:
        done = removal.execute(plan)
    except (ValueError, OSError) as exc:
        raise CliError(str(exc)) from exc
    print("\n" + "\n".join(f"- {line}" for line in done), file=out)
    return 0


def add_parser(sub) -> None:
    root = sub.add_parser("agents", help="agentes (papéis) do projeto")
    asub = root.add_subparsers(dest="agents_command", required=True)
    rm = asub.add_parser("remove", help="remove um agente e o que foi gerado para ele (sem --yes só mostra o plano)")
    rm.add_argument("project")
    rm.add_argument("role")
    rm.add_argument("--yes", action="store_true", help="executa a remoção")
    rm.set_defaults(func=cmd_remove)
