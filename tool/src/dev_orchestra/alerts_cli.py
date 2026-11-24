"""Subcomandos `dev-orchestra alerts ...`: lista, teste, watcher em segundo plano e serviço do usuário (systemd)."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .core import alerts as al
from .core import config

UNIT_NAME = "dev-orchestra-alerts.service"
UNIT = """\
[Unit]
Description=dev-orchestra: alertas (som e notificação do sistema) dos agentes
After=graphical-session.target

[Service]
ExecStart={python} -m dev_orchestra alerts watch
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _state_marks(a: al.Alert) -> str:
    return {"novo": "NOVO", "visto": "visto", "reconhecido": "ok", "silenciado": "silenc."}[a.state]


def cmd_list(args, out: TextIO, services) -> int:
    cfg = config.load_global()
    store = al.AlertStore()
    alerts, _ = al.refresh(config.list_projects(), cfg, services, store, notify=False)
    shown = [a for a in alerts if args.all or a.state in ("novo", "visto")]
    if not shown:
        print("Nenhum alerta." if not args.all else "Nenhum alerta (nem silenciado ou reconhecido).", file=out)
        return 0
    now = datetime.now().astimezone()
    for a in shown:
        icon = al.SEVERITY_ICON[a.severity]
        task = f" [{a.task}]" if a.task else ""
        print(
            f"{icon} {al.SEVERITY_LABEL[a.severity]:<8} {a.age_text(now):>6}  {a.project} · {a.agent}{task}", file=out
        )
        print(f"    {a.label}: {a.message}   ({_state_marks(a)})", file=out)
    return 0


def cmd_test(args, out: TextIO, services) -> int:
    """Dispara um alerta de cada gravidade (som e notificação do sistema, conforme as preferências)."""
    cfg = config.load_global()
    notifier = al.Notifier(cfg.alerts)
    now = datetime.now().astimezone()
    for kind in ("permission", "stalled", "done_unchecked"):
        sample = al.Alert(f"teste|{kind}", kind, "dev-orchestra", "teste", now, "Isto é um teste dos alertas.")
        if args.only and al.SEVERITY_LABEL[sample.severity].lower() not in args.only:
            continue
        done = notifier.notify(sample)
        print(f"{al.SEVERITY_ICON[sample.severity]} {sample.label}: notificação={'sim' if done['os'] else 'não'} "
              f"som={done['sound'] or 'não'}", file=out)  # fmt: skip
        time.sleep(1.0)
    return 0


def cmd_watch(args, out: TextIO, services) -> int:
    """Roda em segundo plano (sem TUI): calcula os alertas e avisa por som e notificação do sistema."""
    store = al.AlertStore()
    print(f"dev-orchestra alerts watch: a cada {args.interval:g} s (Ctrl+C para parar)", file=out, flush=True)
    try:
        while True:
            try:
                cfg = config.load_global()
                _alerts, fired = al.refresh(config.list_projects(), cfg, services, store, al.Notifier(cfg.alerts))
                for a, done in fired:
                    print(f"{datetime.now():%H:%M:%S} {a.label} · {a.project} · {a.agent}: {a.message} "
                          f"(notificação={'sim' if done['os'] else 'não'} som={done['sound'] or 'não'})", file=out, flush=True)  # fmt: skip
            except Exception as exc:  # noqa: BLE001 - o serviço não pode morrer por uma falha de rede
                print(f"{datetime.now():%H:%M:%S} falha no ciclo: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def cmd_install_service(args, out: TextIO, services) -> int:
    """Grava a unidade do systemd (do usuário). Não habilita sozinha: mostra os comandos."""
    path = unit_path()
    text = UNIT.format(python=sys.executable)
    if args.print:
        print(text, file=out)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"Unidade gravada em {path}", file=out)
    print("Para ligar o watcher em segundo plano:", file=out)
    print(f"  systemctl --user daemon-reload && systemctl --user enable --now {UNIT_NAME}", file=out)
    print("Para desligar:", file=out)
    print(f"  systemctl --user disable --now {UNIT_NAME}", file=out)
    return 0


def add_parser(sub) -> None:
    root = sub.add_parser("alerts", help="alertas dos agentes (som e notificação do sistema)")
    asub = root.add_subparsers(dest="alerts_command", required=True)
    ls = asub.add_parser("list", help="mostra os alertas de agora")
    ls.add_argument("--all", action="store_true", help="inclui reconhecidos e silenciados")
    ls.set_defaults(func=cmd_list)
    ts = asub.add_parser("test", help="dispara um alerta de teste de cada gravidade")
    ts.add_argument("--only", action="append", choices=["crítico", "atenção", "info"], help="só esta gravidade")
    ts.set_defaults(func=cmd_test)
    wt = asub.add_parser("watch", help="roda em segundo plano e avisa por som e notificação (sem TUI)")
    wt.add_argument("--interval", type=float, default=5.0)
    wt.add_argument("--once", action="store_true", help="um ciclo só (para teste)")
    wt.set_defaults(func=cmd_watch)
    iv = asub.add_parser("install-service", help="grava a unidade systemd do usuário para o watcher")
    iv.add_argument("--print", action="store_true", help="só mostra o conteúdo da unidade")
    iv.set_defaults(func=cmd_install_service)
