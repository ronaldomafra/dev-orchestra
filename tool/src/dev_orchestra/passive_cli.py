"""Subcomandos `dev-orchestra passive ...`: agentes passivos (cadastro, consulta pelo MCP e executor)."""

from __future__ import annotations

import json
import signal
import sys
import threading
from pathlib import Path
from typing import TextIO

from .core import config, mcp_http, passive, passive_runner


def _url(args) -> str:
    if getattr(args, "url", None):
        return args.url
    try:
        return config.load_global().delegator_url
    except config.ConfigError:
        from .delegator import DEFAULT_URL

        return DEFAULT_URL


def _err(message: str):
    from .cli import CliError

    return CliError(message)


def _load(agent_id: str) -> passive.PassiveAgent:
    try:
        return passive.load(agent_id)
    except passive.PassiveError as exc:
        raise _err(str(exc)) from exc


def cmd_list(args, out: TextIO, services) -> int:
    agents = passive.list_agents()
    if not agents:
        print(
            "Nenhum agente passivo. Crie com: dev-orchestra passive add ID --cli claude --description '...'", file=out
        )
        return 0
    for a in agents:
        state = passive.load_session(a.id)
        session = f"sessão {state.id[:8]} ({state.calls})" if state.id else "sem sessão"
        model = f"{a.cli} {a.model or ''}".strip()
        print(f"{a.id:<24} {model:<28} {', '.join(a.permissions.labels()):<32} {session}", file=out)
    return 0


def cmd_show(args, out: TextIO, services) -> int:
    agent = _load(args.id)
    p = agent.permissions
    print(f"{agent.id} — {agent.description or '(sem descrição)'}", file=out)
    print(f"  pasta        {agent.folder}", file=out)
    print(
        f"  cli          {agent.cli} · modelo {agent.model or 'padrão'} · esforço {agent.effort or 'padrão'}", file=out
    )
    print(f"  permissões   {', '.join(p.labels())}", file=out)
    if p.commands and not p.any_command:
        print(f"  comandos     {', '.join(p.commands)}", file=out)
    if p.read_dirs:
        print(f"  pode ler     {', '.join(p.read_dirs)}", file=out)
    print(
        f"  tempo limite {agent.timeout} s · recomeçar a sessão a cada {agent.reset_every or '∞'} consultas", file=out
    )
    files = passive.list_files(agent.id)
    print(f"  perfil       {len(passive.read_profile(agent.id))} caracteres · arquivos: {len(files)}", file=out)
    print("  como roda", file=out)
    for line in passive.describe(agent):
        print(f"    {line}", file=out)
    return 0


def _split(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        out.extend(v.strip() for v in value.split(",") if v.strip())
    return out


def cmd_add(args, out: TextIO, services) -> int:
    try:
        passive.validate_id(args.id)
    except passive.PassiveError as exc:
        raise _err(str(exc)) from exc
    if passive.exists(args.id) and not args.force:
        raise _err(f"o agente {args.id} já existe (use --force para substituir a configuração)")
    agent = passive.PassiveAgent(
        id=args.id, description=args.description or "", cli=args.cli, model=args.model, effort=args.effort,
        timeout=args.timeout, reset_every=args.reset_every, command=args.command_line,
        permissions=passive.Permissions(
            read=not args.no_read, write=args.write, commands=_split(args.allow_command),
            any_command=args.any_command, web=args.web, user_mcps=args.mcps, user_settings=args.user_settings,
            read_dirs=[str(Path(d).expanduser()) for d in (args.dir or [])],
        ),
    )  # fmt: skip
    if agent.cli == "comando" and not agent.command:
        raise _err("--cli comando exige --command-line 'programa args' (lê o pedido no stdin)")
    if agent.cli == "ollama" and not agent.model:
        raise _err("--cli ollama exige --model (ex.: qwen2.5:3b-instruct)")
    profile = None
    if args.profile_file:
        profile = Path(args.profile_file).expanduser().read_text(encoding="utf-8")
    elif not passive.read_profile(agent.id):
        profile = passive.default_profile(agent)
    folder = passive.save(agent, profile)
    print(f"Agente {agent.id} salvo em {folder}", file=out)
    if args.adopt_session:
        try:
            src, sid = args.adopt_session.rsplit(":", 1)
            new = passive.adopt_session(agent, Path(src).expanduser(), sid)
        except (ValueError, passive.PassiveError, OSError) as exc:
            raise _err(f"não adotei a sessão: {exc}") from exc
        print(f"Sessão {sid[:8]} copiada para o agente como {new[:8]} (a original fica intacta)", file=out)
    print(f"Perfil: {folder / passive.PROFILE_NAME} · material de consulta: {folder / passive.FILES_DIR}/", file=out)
    return 0


def cmd_remove(args, out: TextIO, services) -> int:
    agent = _load(args.id)
    print(f"Remover o agente passivo {agent.id}:", file=out)
    print(f"  - pasta {agent.folder} (cadastro, perfil, arquivos de consulta)", file=out)
    for item in passive.session_files(agent):
        print(f"  - {'' if args.sessions else '(mantida) '}sessões da CLI em {item}", file=out)
    print("  - o histórico de consultas no delegador fica (registro de custo)", file=out)
    if not args.yes:
        print("\n(nada foi alterado; repita com --yes para remover)", file=out)
        return 0
    for line in passive.remove(agent.id, sessions=args.sessions):
        print(f"- {line}", file=out)
    return 0


def cmd_send(args, out: TextIO, services) -> int:
    """Consulta pelo MCP do delegador, exatamente como um consumidor externo faria."""
    text = args.text
    if args.file:
        text = Path(args.file).expanduser().read_text(encoding="utf-8")
    elif text == "-" or (text is None and not sys.stdin.isatty()):
        text = sys.stdin.read()
    if not text or not text.strip():
        raise _err("informe o pedido (texto, --file ou stdin)")
    url = _url(args)
    try:
        sent = mcp_http.consult(url, args.id, text, args.origin)
    except mcp_http.McpError as exc:
        raise _err(str(exc)) from exc
    cid = int(sent["consult_id"])
    if args.no_wait:
        print(json.dumps(sent, ensure_ascii=False) if args.json else f"consulta #{cid} na fila", file=out)
        return 0
    if not args.json:
        ahead = sent.get("ahead_in_queue") or 0
        print(
            f"consulta #{cid} enviada" + (f" ({ahead} na frente)" if ahead else "") + " · aguardando…", file=sys.stderr
        )
        if sent.get("note"):
            print(f"aviso: {sent['note']}", file=sys.stderr)
    try:
        final = mcp_http.wait_result(url, cid, timeout=args.timeout)
    except mcp_http.McpError as exc:
        raise _err(str(exc)) from exc
    if args.json:
        print(json.dumps(final, ensure_ascii=False, indent=2), file=out)
    elif final.get("state") == "done":
        print(final.get("result") or "", file=out)
        usage = final.get("usage") or {}
        print(
            f"[#{cid} · {(final.get('duration_ms') or 0) / 1000:.1f} s · entrada {usage.get('input_tokens', 0)} · "
            f"saída {usage.get('output_tokens', 0)} tokens]",
            file=sys.stderr,
        )
    else:
        print(f"consulta #{cid}: {final.get('state')} {final.get('error') or ''}".strip(), file=out)
    return 0 if final.get("state") == "done" else 1


def cmd_result(args, out: TextIO, services) -> int:
    try:
        print(json.dumps(mcp_http.consult_result(_url(args), args.consult_id), ensure_ascii=False, indent=2), file=out)
    except mcp_http.McpError as exc:
        raise _err(str(exc)) from exc
    return 0


def cmd_consults(args, out: TextIO, services) -> int:
    from .delegator.client import DelegatorClient, DelegatorUnavailable

    try:
        items = DelegatorClient(_url(args)).passive_consults(args.id, args.limit)
    except (DelegatorUnavailable, RuntimeError) as exc:
        raise _err(f"delegador indisponível: {exc}") from exc
    for c in items:
        usage = c.get("usage") or {}
        tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        print(
            f"#{c['consult_id']:<5} {c['agent']:<20} {c['state']:<10} {c.get('origin') or '-':<18} "
            f"{(c.get('duration_ms') or 0) / 1000:>6.1f} s {tokens:>8} tok  {c.get('preview', '')[:60]}",
            file=out,
        )
    return 0


def cmd_run(args, out: TextIO, services) -> int:
    from .delegator.client import DelegatorClient

    runner = passive_runner.PassiveRunner(
        DelegatorClient(_url(args), timeout=10.0), max_parallel=args.parallel, out=out
    )
    if args.once:
        runner.tick(wait=True)
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runner.serve(args.interval, stop)
    return 0


def cmd_start(args, out: TextIO, services) -> int:
    pid = passive_runner.start_background(getattr(args, "url", None))
    print(f"executor em segundo plano (pid {pid}) · log: {passive_runner.log_file()}", file=out)
    return 0


def cmd_stop(args, out: TextIO, services) -> int:
    print(
        "executor parado" if passive_runner.stop_background() else "nenhum executor iniciado pela ferramenta", file=out
    )
    return 0


def add_parser(sub) -> None:
    root = sub.add_parser("passive", help="agentes passivos (auxiliares sem sessão aberta, chamados pelo MCP)")
    psub = root.add_subparsers(dest="passive_command", required=True)
    psub.add_parser("list", help="lista os agentes passivos").set_defaults(func=cmd_list)
    show = psub.add_parser("show", help="mostra o cadastro e como cada consulta roda")
    show.add_argument("id")
    show.set_defaults(func=cmd_show)

    add = psub.add_parser("add", help="cria (ou, com --force, substitui) um agente passivo")
    add.add_argument("id")
    add.add_argument("--description", "-d")
    add.add_argument("--cli", choices=passive.CLIS, default="claude")
    add.add_argument("--model", "-m")
    add.add_argument("--effort", "-e")
    add.add_argument("--timeout", type=int, default=passive.DEFAULT_TIMEOUT, help="segundos por consulta")
    add.add_argument("--reset-every", type=int, default=0, help="recomeça a sessão a cada N consultas (0 = nunca)")
    add.add_argument("--command-line", help="CLI 'comando': programa que lê o pedido no stdin")
    add.add_argument("--no-read", action="store_true", help="sem leitura de arquivos")
    add.add_argument("--write", action="store_true", help="libera escrita/edição")
    add.add_argument("--allow-command", action="append", help="prefixo de comando liberado (repetível ou a,b,c)")
    add.add_argument("--any-command", action="store_true", help="libera qualquer comando")
    add.add_argument("--web", action="store_true", help="libera WebFetch/WebSearch")
    add.add_argument("--mcps", action="store_true", help="carrega os MCPs do usuário")
    add.add_argument(
        "--user-settings", action="store_true", help="Claude: carrega ~/.claude/settings.json (hooks, plugins)"
    )
    add.add_argument("--dir", action="append", help="pasta extra que o agente pode ler (repetível)")
    add.add_argument("--profile-file", help="perfil (Markdown) a usar; sem ele, cria um modelo para editar")
    add.add_argument("--adopt-session", metavar="PASTA:SESSAO", help="Claude: copia uma sessão existente para o agente")
    add.add_argument("--force", action="store_true")
    add.set_defaults(func=cmd_add)

    rm = psub.add_parser("remove", help="remove o agente (sem --yes só mostra o plano)")
    rm.add_argument("id")
    rm.add_argument("--sessions", action="store_true", help="apaga também as sessões da CLI")
    rm.add_argument("--yes", action="store_true")
    rm.set_defaults(func=cmd_remove)

    send = psub.add_parser("send", help="consulta um agente pelo MCP do delegador e espera a resposta")
    send.add_argument("id")
    send.add_argument("text", nargs="?", help="o pedido (ou '-' / stdin)")
    send.add_argument("--file", "-f")
    send.add_argument("--origin", "-o", default="cli", help="quem está pedindo (histórico e custo)")
    send.add_argument("--no-wait", action="store_true", help="só enfileira e devolve o número")
    send.add_argument("--timeout", type=float, default=900.0, help="quanto esperar pela resposta (s)")
    send.add_argument("--json", action="store_true")
    send.add_argument("--url")
    send.set_defaults(func=cmd_send)

    res = psub.add_parser("result", help="estado e resposta de uma consulta")
    res.add_argument("consult_id", type=int)
    res.add_argument("--url")
    res.set_defaults(func=cmd_result)

    cons = psub.add_parser("consults", help="consultas recentes (todas ou de um agente)")
    cons.add_argument("id", nargs="?")
    cons.add_argument("--limit", type=int, default=20)
    cons.add_argument("--url")
    cons.set_defaults(func=cmd_consults)

    run = psub.add_parser("run", help="executor: atende a fila do delegador (fica rodando)")
    run.add_argument("--once", action="store_true", help="uma volta só (espera as consultas que começar)")
    run.add_argument("--interval", type=float, default=passive_runner.DEFAULT_INTERVAL)
    run.add_argument("--parallel", type=int, default=4, help="agentes diferentes ao mesmo tempo")
    run.add_argument("--url")
    run.set_defaults(func=cmd_run)
    start = psub.add_parser("start", help="inicia o executor em segundo plano")
    start.add_argument("--url")
    start.set_defaults(func=cmd_start)
    psub.add_parser("stop", help="para o executor iniciado em segundo plano").set_defaults(func=cmd_stop)
