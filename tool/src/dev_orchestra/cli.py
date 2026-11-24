"""Linha de comando do dev-orchestra (a mesma lógica da TUI, sem terminal interativo)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from .core import config, contract, dispatch, importer, paths, portable, state, tasks
from .core.commands import build_launch_plan, format_plan, local_context
from .core.liveness import RoleStatus, Services, StatusReport, collect_status, describe_ai_memory
from .core.models import GlobalConfig, Project, Role, Subscription, TaskSource, explain
from .delegator.commands import DelegatorCommandError


class CliError(Exception):
    """Erro de uso: a mensagem é mostrada ao usuário e o código de saída é 2."""


# ------------------------------------------------------------------ utilidades


def parse_role_spec(spec: str) -> tuple[str, Role]:
    """`developer=codex,model=gpt-6-luna,effort=low,subscription=codex-pessoal`."""
    head, *options = spec.split(",")
    if "=" not in head:
        raise CliError(f"papel inválido: {spec!r} (use PAPEL=CLI[,model=..,effort=..,subscription=..])")
    role_id, cli = (x.strip() for x in head.split("=", 1))
    fields: dict[str, str] = {}
    keys = {"model": "default_model", "effort": "default_effort", "subscription": "subscription"}
    for option in options:
        if "=" not in option:
            raise CliError(f"opção inválida em {spec!r}: {option!r}")
        key, value = (x.strip() for x in option.split("=", 1))
        if key not in keys:
            raise CliError(f"opção desconhecida em {spec!r}: {key!r} (use model, effort ou subscription)")
        fields[keys[key]] = value
    try:
        return role_id, Role(cli=cli, **fields)
    except ValueError as exc:
        raise CliError(f"papel inválido {spec!r}: {explain(exc)}") from exc


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = (
        [max(len(str(c)) for c in col) for col in zip(headers, *rows, strict=False)]
        if rows
        else [len(h) for h in headers]
    )
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()
    body = ["  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in rows]
    return "\n".join([line, *body])


def format_status(statuses: list[RoleStatus], project_id: str | None = None, delegator: bool = False) -> str:
    rows = []
    for s in statuses:
        info = state.sent_info(project_id, s.name, state.comm_mode(delegator)) if project_id and s.name else None
        rows.append([s.name or s.role, s.cli, s.state, state.describe(info), s.detail])
    return render_table(["AGENTE", "CLI", "SESSÃO", "INSTRUÇÕES", "DETALHE"], rows)


def format_ai_memory(project: Project, report: StatusReport) -> str:
    return describe_ai_memory(project, report.ai_memory)


def load(project_id: str) -> Project:
    try:
        return config.load_project(project_id)
    except config.ConfigError as exc:
        raise CliError(str(exc)) from exc


def _status_and_context(project: Project, cfg: GlobalConfig, services: Services | None, offline: bool):
    if offline:
        return None, local_context(project)
    report = collect_status(project, cfg, services)
    return report, local_context(project, workstreams=report.workstream_names, app_server_up=report.app_server_up)


# -------------------------------------------------------------------- comandos


def cmd_config(args, out: TextIO, services) -> int:
    print(f"pasta de instalação : {paths.install_dir()}", file=out)
    print(f"config global       : {paths.global_config_file()}", file=out)
    print(f"cadastros           : {paths.projects_dir()}", file=out)
    return 0


def cmd_projects_list(args, out: TextIO, services) -> int:
    projects = config.list_projects()
    if not projects:
        print("Nenhum projeto cadastrado. Use: dev-orchestra projects add ...", file=out)
        return 0
    rows = [[p.id, p.name, str(p.path), ", ".join(f"{r}:{p.roles[r].cli}" for r in p.role_order())] for p in projects]
    print(render_table(["ID", "NOME", "PASTA", "PAPÉIS"], rows), file=out)
    return 0


def cmd_projects_add(args, out: TextIO, services) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.is_dir():
        raise CliError(f"a pasta do projeto não existe: {path}")
    if not args.role:
        raise CliError("informe ao menos um papel com --role (ex.: --role orchestrator=claude)")
    roles = dict(parse_role_spec(spec) for spec in args.role)
    try:
        project = Project(
            id=args.id,
            name=args.name or args.id,
            path=path,
            task_source=TaskSource(kind=args.task_source, trello_board=args.trello_board),
            roles=roles,
            app_server_url=args.app_server,
            ai_memory_project=args.ai_memory_project,
            prefix=args.prefix,
            delegator=args.delegator,
        )
    except ValueError as exc:
        raise CliError(explain(exc)) from exc
    if paths.project_file(project.id).exists() and not args.force:
        raise CliError(f"o projeto {project.id!r} já existe (use --force para substituir o cadastro)")
    target = config.save_project(project)
    project.tasks_path.mkdir(parents=True, exist_ok=True)
    print(f"Projeto {project.id!r} cadastrado em {target}", file=out)
    print(f"Pasta de tarefas: {project.tasks_path}", file=out)
    if not args.no_scaffold:
        print(contract.format_report(contract.scaffold(project)), file=out)
    return 0


def _scan(services, path):
    fn = getattr(services, "scan_project", None) if services else None
    return (fn or importer.scan)(Path(path))


def format_scan(result, suggestions) -> str:
    lines = [f"Pasta: {result.path}"]
    if result.ai_memory_project:
        existe = {True: "existe", False: "ainda não existe", None: "não consultado"}[result.project_exists]
        lines.append(
            f"AI Memory: projeto {result.ai_memory_project!r} (workspace {result.ai_memory_workspace}) — {existe}"
        )
    else:
        lines.append("AI Memory: a pasta não tem .ai-memory.toml; o projeto não foi identificado")
    lines.append(
        f"AGENTS.md: {'sim' if result.agents_md else 'não'}   "
        f"documentos do método: {', '.join(result.references) or '-'}"
    )
    lines.append(
        "Agentes do Claude Code: " + (", ".join(f"{d.name} ({d.file})" for d in result.agent_defs) or "nenhum")
    )
    lines.append(f"Trello: {result.trello_hint or 'nenhuma referência encontrada'}")
    cands = importer.candidate_workstreams(result)
    lines.append("Workstreams candidatos: " + (", ".join(f"{w.name} [{w.last_used}]" for w in cands) or "nenhum"))
    rows = []
    for role, s in suggestions.items():
        rows.append([role, s.workstream or "(nenhum)", s.cli if s.workstream else "-", s.agent or "-"])
    lines.append("")
    lines.append("Sugestão de papéis (você confirma na TUI):")
    lines.append(render_table(["PAPEL", "AGENTE EXISTENTE", "CLI", "--agent"], rows))
    return "\n".join(lines)


def cmd_projects_scan(args, out: TextIO, services) -> int:
    result = _scan(services, args.path)
    if not result.path.is_dir():
        raise CliError(f"a pasta não existe: {result.path}")
    print(format_scan(result, importer.suggest_roles(result)), file=out)
    return 0


def cmd_projects_import_existing(args, out: TextIO, services) -> int:
    """Importa um projeto existente aceitando as sugestões (a TUI permite ajustar uma a uma)."""
    result = _scan(services, args.path)
    if not result.path.is_dir():
        raise CliError(f"a pasta não existe: {result.path}")
    suggestions = importer.suggest_roles(result)
    print(format_scan(result, suggestions), file=out)
    if not args.yes:
        print("\nNada foi gravado. Confirme com --yes (ou use a TUI, tecla m, para ajustar cada papel).", file=out)
        return 0
    roles = {
        role: Role(cli=s.cli, name=s.workstream, agent=s.agent)  # type: ignore[arg-type]
        for role, s in suggestions.items()
        if s.workstream
    }
    if not roles:
        raise CliError("nenhum agente existente foi reconhecido; use a TUI para escolher")
    kind = args.task_source or ("trello" if (args.trello_board or result.trello_hint) else "files")
    source = TaskSource(
        kind=kind,
        trello_board=(args.trello_board or result.trello_hint) if kind == "trello" else None,
        trello_lists=dict(importer.DEFAULT_TRELLO_LISTS) if kind == "trello" else {},
    )
    try:
        project = importer.build_project(
            result,
            project_id=args.id or result.suggested_id(),
            name=args.name or result.path.name,
            roles=roles,
            task_source=source,
        )
    except ValueError as exc:
        raise CliError(explain(exc)) from exc
    project.delegator = args.delegator
    if paths.project_file(project.id).exists() and not args.force:
        raise CliError(f"o projeto {project.id!r} já existe (use --force ou --id)")
    config.save_project(project)
    project.tasks_path.mkdir(parents=True, exist_ok=True)
    print(
        f"\nProjeto {project.id!r} importado. Nomes e agentes preservados; nenhum arquivo do método foi copiado.",
        file=out,
    )
    return 0


def cmd_scaffold(args, out: TextIO, services) -> int:
    """Cria na pasta do projeto os arquivos do método que faltam (nunca sobrescreve)."""
    project = load(args.project)
    try:
        report = contract.scaffold(project, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        raise CliError(str(exc)) from exc
    prefix = "(simulação, nada foi gravado)\n" if args.dry_run else ""
    print(prefix + contract.format_report(report), file=out)
    return 0


def cmd_projects_show(args, out: TextIO, services) -> int:
    p = load(args.project)
    print(f"{p.id} — {p.name}", file=out)
    print(f"pasta   : {p.path}", file=out)
    print(f"tarefas : {p.tasks_dir} ({p.task_source.kind})", file=out)
    print(f"AI Memory: projeto {p.ai_memory_name()} (workspace {p.ai_memory_workspace})", file=out)
    print(f"agentes : {', '.join(p.agent_name(r) for r in p.role_order())}", file=out)
    print(f"mensagens: {'delegador (MCP)' if p.delegator else 'SendMessage / codex queue'}", file=out)
    rows = [
        [
            r,
            p.roles[r].cli,
            p.roles[r].subscription or "-",
            p.roles[r].default_model or "-",
            p.roles[r].default_effort or "-",
        ]
        for r in p.role_order()
    ]
    print(render_table(["PAPEL", "CLI", "ASSINATURA", "MODELO", "ESFORÇO"], rows), file=out)
    return 0


def cmd_projects_remove(args, out: TextIO, services) -> int:
    if not args.yes:
        raise CliError("isto remove só o cadastro (a pasta do projeto não é tocada); confirme com --yes")
    if not config.remove_project(args.project):
        raise CliError(f"projeto não encontrado: {args.project}")
    print(f"Cadastro de {args.project!r} removido.", file=out)
    return 0


def cmd_subscriptions(args, out: TextIO, services) -> int:
    cfg = config.load_global()
    if args.sub_command == "add":
        cfg.subscriptions[args.name] = Subscription(cli=args.cli, profile=args.profile, note=args.note)
        config.save_global(cfg)
        print(f"Assinatura {args.name!r} salva.", file=out)
        return 0
    if not cfg.subscriptions:
        print(
            "Nenhuma assinatura definida. Use: dev-orchestra subscriptions add NOME --cli claude --profile PERFIL",
            file=out,
        )
        return 0
    rows = [[n, s.cli, s.profile or "-", s.note or ""] for n, s in cfg.subscriptions.items()]
    print(render_table(["NOME", "CLI", "PERFIL (ai-memory run --profile)", "NOTA"], rows), file=out)
    return 0


def cmd_status(args, out: TextIO, services) -> int:
    project = load(args.project)
    report = collect_status(project, config.load_global(), services)
    print(format_status(report.roles, project.id, project.delegator), file=out)
    print(format_ai_memory(project, report), file=out)
    for note in report.notes:
        print(f"nota: {note}", file=out)
    return 0


def cmd_send(args, out: TextIO, services) -> int:
    """Envia as instruções a cada agente com sessão aberta (Codex por queue; Claude por SendMessage)."""
    project = load(args.project)
    cfg = config.load_global()
    only = set(args.role) if args.role else None
    if only and not only <= set(project.roles):
        raise CliError(f"papel desconhecido neste projeto: {', '.join(sorted(only - set(project.roles)))}")
    report = collect_status(project, cfg, services)
    print(format_ai_memory(project, report), file=out)
    items = dispatch.plan_instructions(project, report, again=args.again, only=only)
    print(dispatch.format_plan(items), file=out)
    if args.dry_run or not any(i.action != "skip" for i in items):
        return 0
    results = dispatch.execute(project, cfg, report, items)
    print(dispatch.format_results(results), file=out)
    return 0 if all(r.ok for _, r in results) else 1


def cmd_up(args, out: TextIO, services) -> int:
    project = load(args.project)
    cfg = config.load_global()
    _, ctx = _status_and_context(project, cfg, services, args.offline)
    print(format_plan(build_launch_plan(project, cfg, ctx)), file=out, end="")
    return 0


def _resume_summary(project: Project) -> list[str]:
    items, warnings = tasks.list_tasks(project)
    lines: list[str] = []
    em_andamento = [t for t in items if t.state in tasks.OPEN_STATES]
    prontas = [t for t in items if t.state in ("draft", "ready")]
    lines.append("Em andamento:")
    lines += [f"  {t.id} [{t.state}] {t.title} (papel {t.role})" for t in em_andamento] or ["  (nenhuma)"]
    lines.append(f"Prontas/rascunho: {len(prontas)}")
    resultados = [t for t in items if t.result_text()]
    resultados.sort(key=lambda t: t.path.stat().st_mtime if t.path else 0, reverse=True)
    lines.append("Últimos resultados:")
    for t in resultados[:3]:
        text = " ".join(t.result_text().split())
        lines.append(f"  {t.id} [{t.state}] {text[:240]}{'…' if len(text) > 240 else ''}")
    if not resultados:
        lines.append("  (nenhum)")
    lines += [f"aviso: {w}" for w in warnings]
    return lines


def cmd_resume(args, out: TextIO, services) -> int:
    project = load(args.project)
    cfg = config.load_global()
    report, ctx = _status_and_context(project, cfg, services, args.offline)
    print(f"Projeto {project.id} — {project.name}", file=out)
    print(f"Pasta: {project.path}", file=out)
    print("", file=out)
    if report is not None:
        print("Sessões:", file=out)
        print(format_status(report.roles, project.id, project.delegator), file=out)
        print(format_ai_memory(project, report), file=out)
        print("", file=out)
    print("\n".join(_resume_summary(project)), file=out)
    print("", file=out)
    print("Comandos para subir ou retomar:", file=out)
    print("", file=out)
    print(format_plan(build_launch_plan(project, cfg, ctx)), file=out, end="")
    return 0


def cmd_tasks(args, out: TextIO, services) -> int:
    project = load(args.project)
    cfg_global = config.load_global()
    try:
        if args.task_command == "list":
            items, warnings = tasks.list_tasks(project)
            if items:
                rows = [[t.id, t.state, t.role, t.model or "-", t.effort or "-", t.title] for t in items]
                print(render_table(["ID", "ESTADO", "PAPEL", "MODELO", "ESFORÇO", "TÍTULO"], rows), file=out)
            else:
                print("Nenhuma tarefa.", file=out)
            for w in warnings:
                print(f"aviso: {w}", file=out)
        elif args.task_command == "new":
            task = tasks.create_task(
                project,
                args.task_id,
                args.title,
                args.role,
                model=args.model,
                effort=args.effort,
                objective=args.objective,
            )
            print(f"Tarefa criada: {task.path}", file=out)
        elif args.task_command == "import-trello":
            report = collect_status(project, cfg_global, services)
            item = dispatch.plan_trello_import(project, report, again=args.again)
            print(dispatch.format_plan([item]), file=out)
            if args.dry_run or item.action == "skip":
                return 0
            result = dispatch.execute_trello_import(project, cfg_global, item)
            print(dispatch.format_results([result]), file=out)
            return 0 if result[1].ok else 1
        elif args.task_command == "send":
            report = collect_status(project, cfg_global, services)
            item = dispatch.plan_task(project, report, args.task_id, again=args.again)
            print(dispatch.format_plan([item]), file=out)
            if args.dry_run or item.action == "skip":
                return 0
            result = dispatch.execute_task(project, cfg_global, args.task_id, item)
            print(dispatch.format_results([result]), file=out)
            return 0 if result[1].ok else 1
        else:  # set-state
            task = tasks.set_task_state(project, args.task_id, args.state)
            print(f"{task.id}: estado = {task.state}", file=out)
    except tasks.TaskError as exc:
        raise CliError(str(exc)) from exc
    return 0


def cmd_export(args, out: TextIO, services) -> int:
    project = load(args.project)
    target = portable.export_project(project)
    print(f"Configuração portátil exportada para {target}", file=out)
    print("Esse arquivo pode ir para o Git do projeto (não contém caminhos locais nem assinaturas).", file=out)
    return 0


def cmd_import(args, out: TextIO, services) -> int:
    folder = Path(args.path).expanduser().resolve()
    if not folder.is_dir():
        raise CliError(f"a pasta do projeto não existe: {folder}")
    source = Path(args.file).expanduser() if args.file else folder / ".dev-orchestra" / "projeto.toml"
    subs = {}
    for item in args.subscription or []:
        if "=" not in item:
            raise CliError(f"use PAPEL=ASSINATURA em --subscription, recebido {item!r}")
        role, name = item.split("=", 1)
        subs[role.strip()] = name.strip()
    try:
        project = portable.import_project(source, folder, subscriptions=subs, project_id=args.id)
    except config.ConfigError as exc:
        raise CliError(str(exc)) from exc
    if paths.project_file(project.id).exists() and not args.force:
        raise CliError(f"o projeto {project.id!r} já existe (use --force ou --id para outro nome)")
    config.save_project(project)
    print(f"Projeto {project.id!r} importado para {paths.project_file(project.id)}", file=out)
    if not args.no_scaffold:
        print(contract.format_report(contract.scaffold(project)), file=out)
    missing = [r for r in project.role_order() if project.roles[r].subscription is None]
    if missing:
        print(f"Assinaturas ainda não definidas (use --subscription PAPEL=NOME): {', '.join(missing)}", file=out)
    return 0


def cmd_tui(args, out: TextIO, services) -> int:
    from .tui.app import run_app

    run_app(services)
    return 0


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev-orchestra", description="Cadastro, subida e retomada de projetos do Dev Orchestra."
    )
    parser.add_argument("--version", action="version", version=f"dev-orchestra {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("tui", help="abre a TUI (padrão sem argumentos)").set_defaults(func=cmd_tui)
    sub.add_parser("config", help="mostra onde ficam os arquivos de configuração").set_defaults(func=cmd_config)

    projects = sub.add_parser("projects", help="cadastro de projetos")
    psub = projects.add_subparsers(dest="projects_command", required=True)
    psub.add_parser("list", help="lista os projetos").set_defaults(func=cmd_projects_list)
    add = psub.add_parser("add", help="cadastra um projeto")
    add.add_argument("--id", required=True)
    add.add_argument("--name")
    add.add_argument("--path", required=True, help="pasta do projeto")
    add.add_argument("--role", action="append", help="PAPEL=CLI[,model=..,effort=..,subscription=..] (repetível)")
    add.add_argument("--task-source", choices=["files", "trello"], default="files")
    add.add_argument("--trello-board")
    add.add_argument("--ai-memory-project")
    add.add_argument("--prefix", help="prefixo dos nomes dos agentes: [prefixo]-[papel] (padrão: o id do projeto)")
    add.add_argument(
        "--delegator",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="as sessões se comunicam pelo delegador (MCP); padrão: sim (--no-delegator usa SendMessage/codex queue)",
    )
    add.add_argument("--app-server", help="URL do App Server deste projeto (padrão: a do config global)")
    add.add_argument("--force", action="store_true")
    add.add_argument("--no-scaffold", action="store_true", help="não cria os arquivos do método na pasta do projeto")
    add.set_defaults(func=cmd_projects_add)
    show = psub.add_parser("show", help="mostra o cadastro")
    show.add_argument("project")
    show.set_defaults(func=cmd_projects_show)
    pscan = psub.add_parser("scan", help="lê um projeto existente (AI Memory, agentes, Trello) sem gravar nada")
    pscan.add_argument("path")
    pscan.set_defaults(func=cmd_projects_scan)
    pimp = psub.add_parser("import-existing", help="importa um projeto existente preservando nomes e agentes")
    pimp.add_argument("path")
    pimp.add_argument("--id")
    pimp.add_argument("--name")
    pimp.add_argument("--task-source", choices=["files", "trello"])
    pimp.add_argument("--trello-board")
    pimp.add_argument("--delegator", action=argparse.BooleanOptionalAction, default=True,
                      help="comunicação pelo delegador (MCP); padrão: sim")  # fmt: skip
    pimp.add_argument("--yes", action="store_true", help="grava o cadastro (sem isso só mostra as sugestões)")
    pimp.add_argument("--force", action="store_true")
    pimp.set_defaults(func=cmd_projects_import_existing)
    rm = psub.add_parser("remove", help="remove o cadastro (não toca na pasta do projeto)")
    rm.add_argument("project")
    rm.add_argument("--yes", action="store_true")
    rm.set_defaults(func=cmd_projects_remove)

    subs = sub.add_parser("subscriptions", help="assinaturas (contas) usadas pelos papéis")
    ssub = subs.add_subparsers(dest="sub_command", required=True)
    ssub.add_parser("list").set_defaults(func=cmd_subscriptions)
    sadd = ssub.add_parser("add")
    sadd.add_argument("name")
    sadd.add_argument("--cli", required=True, choices=["claude", "codex"])
    sadd.add_argument("--profile", help="valor de 'ai-memory run --profile'")
    sadd.add_argument("--note")
    sadd.set_defaults(func=cmd_subscriptions)

    for name, helptext, func in [
        ("status", "mostra se as sessões estão abertas", cmd_status),
        ("up", "imprime os comandos para subir ou retomar as sessões", cmd_up),
        ("resume", "resumo do projeto (sessões, tarefas, resultados) e comandos", cmd_resume),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("project")
        if name != "status":
            p.add_argument("--offline", action="store_true", help="não consulta as CLIs nem o AI Memory")
        p.set_defaults(func=func)

    tk = sub.add_parser("tasks", help="tarefas do projeto")
    tsub = tk.add_subparsers(dest="task_command", required=True)
    tl = tsub.add_parser("list")
    tl.add_argument("project")
    tn = tsub.add_parser("new")
    tn.add_argument("project")
    tn.add_argument("task_id")
    tn.add_argument("--title", required=True)
    tn.add_argument("--role", required=True)
    tn.add_argument("--model")
    tn.add_argument("--effort")
    tn.add_argument("--objective", default="")
    ts = tsub.add_parser("set-state")
    ts.add_argument("project")
    ts.add_argument("task_id")
    ts.add_argument("state", choices=list(tasks.STATES))
    timp = tsub.add_parser(
        "import-trello", help="pede ao orquestrador que traga os cartões do Trello para arquivos locais"
    )
    timp.add_argument("project")
    timp.add_argument("--again", action="store_true")
    timp.add_argument("--dry-run", action="store_true")
    tsend = tsub.add_parser("send", help="envia a tarefa ao orquestrador (sessão aberta)")
    tsend.add_argument("project")
    tsend.add_argument("task_id")
    tsend.add_argument("--again", action="store_true", help="reenviar uma tarefa já enviada")
    tsend.add_argument("--dry-run", action="store_true", help="só mostra o que seria enviado")
    for p in (tl, tn, ts, tsend, timp):
        p.set_defaults(func=cmd_tasks)

    sc = sub.add_parser("scaffold", help="cria na pasta do projeto os arquivos do método que faltam (não sobrescreve)")
    sc.add_argument("project")
    sc.add_argument("--dry-run", action="store_true", help="só mostra o que seria criado")
    sc.set_defaults(func=cmd_scaffold)

    sd = sub.add_parser(
        "send", help="envia as instruções aos agentes com sessão aberta (Codex: queue; Claude: SendMessage)"
    )
    sd.add_argument("project")
    sd.add_argument("--role", action="append", help="só este papel (repetível)")
    sd.add_argument("--again", action="store_true", help="reenviar a quem já recebeu (mensagem de retomada)")
    sd.add_argument("--dry-run", action="store_true", help="só mostra o que seria enviado")
    sd.set_defaults(func=cmd_send)

    ex = sub.add_parser("export", help="exporta a configuração portátil para a pasta do projeto")
    ex.add_argument("project")
    ex.set_defaults(func=cmd_export)
    im = sub.add_parser("import", help="importa a configuração exportada (outra máquina)")
    im.add_argument("--path", required=True, help="pasta do projeto nesta máquina")
    im.add_argument("--file", help="arquivo exportado (padrão: <pasta>/.dev-orchestra/projeto.toml)")
    im.add_argument("--id", help="id do cadastro local (padrão: o do arquivo)")
    im.add_argument("--subscription", action="append", help="PAPEL=ASSINATURA (repetível)")
    im.add_argument("--force", action="store_true")
    im.add_argument("--no-scaffold", action="store_true", help="não cria os arquivos do método na pasta do projeto")
    im.set_defaults(func=cmd_import)

    from .delegator import commands as delegator_commands

    delegator_commands.add_parser(sub)
    from . import alerts_cli

    alerts_cli.add_parser(sub)
    from . import profiles_cli

    profiles_cli.add_parser(sub)
    from . import roles_cli

    roles_cli.add_parser(sub)
    from . import passive_cli

    passive_cli.add_parser(sub)
    return parser


def main(argv: list[str] | None = None, *, services: Services | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.func = cmd_tui
    try:
        return args.func(args, out, services)
    except (CliError, DelegatorCommandError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except config.ConfigError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
