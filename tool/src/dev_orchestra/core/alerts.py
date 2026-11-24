"""Alertas: o que parou ou desviou o trabalho dos agentes e precisa da pessoa.

Três partes, todas sem terminal: o motor (`compute`, regras puras), o estado persistente (`AlertStore`: visto,
reconhecido, silenciado e o que já foi avisado) e o notificador (`Notifier`: som e notificação do sistema no Linux).
A TUI e o watcher em segundo plano usam as mesmas funções e o mesmo arquivo de estado (com trava), então um alerta
nunca é avisado duas vezes.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import paths, tasks
from .config import atomic_write_text
from .liveness import Services
from .models import AlertSettings, GlobalConfig, Project

CRIT, WARN, INFO = "crit", "warn", "info"
SEVERITY_ORDER = {CRIT: 0, WARN: 1, INFO: 2}
SEVERITY_ICON = {CRIT: "▲", WARN: "●", INFO: "○"}
SEVERITY_LABEL = {CRIT: "CRÍTICO", WARN: "ATENÇÃO", INFO: "INFO"}

# tipo -> (severidade, rótulo, o que fazer)
KINDS: dict[str, tuple[str, str, str]] = {
    "permission": (CRIT, "aguardando permissão", "Abra a sessão do agente e aprove ou negue o comando. O trabalho só continua depois."),
    "question": (CRIT, "pergunta para a pessoa", "Abra a sessão do agente e responda à pergunta. O trabalho só continua depois."),
    "waiting": (CRIT, "aguardando você", "A sessão parou esperando uma resposta ou aprovação sua. Abra o terminal dela. (Sem o detalhe do comando ou da pergunta: atualize os hooks de alerta com `dev-orchestra delegator install` e reabra a sessão.)"),
    "turn_error": (CRIT, "turno interrompido", "O turno terminou com erro (limite de uso, falha de API ou de autenticação). Abra a sessão e reenvie a mensagem."),
    "worker_question": (WARN, "pergunta sem resposta", "O orquestrador ainda não respondeu. Abra a sessão dele ou responda você mesmo."),
    "stalled": (WARN, "tarefa parada", "A tarefa está em execução sem nenhum evento há muito tempo. Veja a sessão; se travou, cancele e delegue de novo."),
    "not_received": (WARN, "worker não recebeu", "A tarefa foi delegada e o worker não a recebeu. Confira se a sessão está aberta e registrada."),
    "session_closed": (WARN, "sessão fechada", "A sessão do agente fechou com a tarefa ainda em andamento. Reabra-a (dev-orchestra up) ou cancele a tarefa."),
    "refused": (WARN, "recusa de permissão", "Um agente tentou algo que o papel dele não permite. Revise o que ele estava fazendo."),
    "delegator_down": (WARN, "delegador fora do ar", "O serviço não responde: rode `dev-orchestra delegator docker up`."),
    "done_unchecked": (INFO, "concluída, aguardando conferência", "O worker concluiu e o orquestrador ainda não conferiu nem atualizou o arquivo da tarefa."),
}  # fmt: skip
SOUND_FOR = {CRIT: "alarme", WARN: "mensagem", INFO: None}
FINAL_STATES = ("done", "cancelled")


@dataclass
class Alert:
    key: str
    kind: str
    project: str
    agent: str
    since: datetime
    message: str
    task: str | None = None
    detail: str = ""
    count: int = 1
    state: str = "novo"  # novo | visto | silenciado | reconhecido

    @property
    def severity(self) -> str:
        return KINDS[self.kind][0]

    @property
    def label(self) -> str:
        return KINDS[self.kind][1]

    @property
    def advice(self) -> str:
        return KINDS[self.kind][2]

    def age_text(self, now: datetime | None = None) -> str:
        seconds = max(0, int(((now or datetime.now().astimezone()) - self.since).total_seconds()))
        if seconds < 60:
            return f"{seconds} s"
        if seconds < 3600:
            return f"{seconds // 60} min"
        if seconds < 86400:
            return f"{seconds // 3600} h"
        return f"{seconds // 86400} d"


def _local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return None


# ------------------------------------------------------------------ coleta


@dataclass
class ProjectInput:
    project: Project
    status: dict | None  # `status_data` do delegador; None = fora do ar
    signals: list[dict] = field(default_factory=list)  # sinais ativos dos hooks (permissão, pergunta, erro de turno)
    refusals: list[dict] = field(default_factory=list)  # recusas recentes
    roles_state: dict[str, str] = field(default_factory=dict)  # papel -> ociosa | ocupada | fechada | ...
    roles_raw: dict[str, str | None] = field(default_factory=dict)  # papel -> status cru do Claude (`waiting`...)
    local_tasks: list[tasks.Task] = field(default_factory=list)


def _call(fn: Callable | None, default: Callable, *args: Any) -> Any:
    return (fn or default)(*args)


def gather(projects: list[Project], cfg: GlobalConfig, services: Services | None = None) -> list[ProjectInput]:
    """Lê o delegador e o estado das sessões (só dos projetos em modo delegador: é de lá que vêm os alertas)."""
    from ..delegator.client import DelegatorClient, DelegatorUnavailable
    from . import observer, summary
    from .liveness import delegator_status

    services = services or Services()
    wanted = [p for p in projects if p.delegator]
    if not projects:
        return []
    # todos os projetos: o observador anota no Monitor o que muda nas tarefas e nas sessões (também fora do delegador)
    reports, _ = summary.collect_all(projects, cfg, services)
    for p in projects:
        try:
            observer.observe(p, reports.get(p.id))
        except OSError:
            pass
    out: list[ProjectInput] = []
    for p in wanted:
        status = _call(services.delegator_status, delegator_status, cfg.delegator_url, p.id)
        signals: list[dict] = []
        refusals: list[dict] = []
        if status is not None:
            if services.delegator_alerts is not None:
                signals = services.delegator_alerts(cfg.delegator_url, p.id) or []
            else:
                try:
                    signals = DelegatorClient(cfg.delegator_url, timeout=2.0).alerts(p.id)
                except (DelegatorUnavailable, RuntimeError, ValueError):
                    signals = []
            if services.delegator_timeline is not None:
                events = services.delegator_timeline(cfg.delegator_url, p.id) or []
            else:
                try:
                    events = DelegatorClient(cfg.delegator_url, timeout=2.0).timeline(p.id, 80)
                except (DelegatorUnavailable, RuntimeError, ValueError):
                    events = []
            refusals = [e for e in events if e.get("kind") == "refused"]
        report = reports.get(p.id)
        roles_state = {r.role: r.state for r in report.roles} if report else {}
        roles_raw = {r.role: r.raw_status for r in report.roles} if report else {}
        local, _warnings = tasks.list_tasks(p)
        out.append(ProjectInput(p, status, signals, refusals, roles_state, roles_raw, local))
    return out


# ------------------------------------------------------------------- motor


def compute(inputs: list[ProjectInput], settings: AlertSettings, now: datetime | None = None) -> list[Alert]:
    now = now or datetime.now().astimezone()
    alerts: list[Alert] = []
    for item in inputs:
        alerts += _project_alerts(item, settings, now)
    alerts.sort(key=lambda a: (SEVERITY_ORDER[a.severity], -a.since.timestamp()))
    return alerts


def _minutes(moment: datetime, now: datetime) -> float:
    return (now - moment).total_seconds() / 60


def _project_alerts(item: ProjectInput, cfg: AlertSettings, now: datetime) -> list[Alert]:
    p = item.project
    out: list[Alert] = []
    if item.status is None:
        # sem horário de origem: o estado persistente guarda a primeira vez que vimos (`AlertStore.apply`)
        out.append(Alert(f"delegator_down|{p.id}", "delegator_down", p.id, "delegador", now, "o serviço não responde"))
        return out
    # `claude agents` diz, ao vivo, quem está esperando a pessoa (`waiting`): é a palavra final sobre permissão e
    # pergunta. Um sinal de hook sem essa espera é velho (por exemplo, depois de um Esc) e não vale.
    known = {p.agent_name(r) for r, raw in item.roles_raw.items() if raw is not None}
    waiting = {p.agent_name(r) for r, raw in item.roles_raw.items() if raw == "waiting"}
    hooked: set[str] = set()
    for s in item.signals:
        if s["kind"] in ("permission", "question"):
            if s["agent"] in known and s["agent"] not in waiting:
                continue
            hooked.add(s["agent"])
        since = _local(s.get("created_at")) or now
        out.append(
            Alert(
                key=f"{s['kind']}|{p.id}|{s['agent']}",
                kind=s["kind"],
                project=p.id,
                agent=s["agent"],
                since=since,
                message=str(s.get("message") or ""),
                task=s.get("task_id"),
                detail=str(s.get("detail") or ""),
            )  # fmt: skip
        )
    for agent in sorted(waiting - hooked):  # esperando, mas sem hook para dizer o quê
        out.append(
            Alert(f"waiting|{p.id}|{agent}", "waiting", p.id, agent, now, "a sessão espera a sua resposta ou aprovação")
        )
    status_tasks = item.status.get("tasks", [])
    by_id = {t["task_id"]: t for t in status_tasks}
    for q in item.status.get("open_questions", []):
        since = _local(q.get("created_at")) or now
        if _minutes(since, now) < cfg.question_minutes:
            continue
        task = by_id.get(q["task_id"], {})
        agent = p.agent_name(task["to_role"]) if task.get("to_role") in p.roles else "worker"
        text = " ".join(str(q.get("question", "")).split())[:100]
        out.append(Alert(f"worker_question|{p.id}|{q['id']}", "worker_question", p.id, agent, since,
                         f"pergunta #{q['id']} aberta há {int(_minutes(since, now))} min: {text}", q["task_id"]))  # fmt: skip
    local_state = {t.id: t.state for t in item.local_tasks}
    for t in status_tasks:
        state, role = t["state"], t.get("to_role")
        agent = p.agent_name(role) if role in p.roles else "worker"
        last = _local(t.get("last_event") or t.get("updated_at")) or now
        updated = _local(t.get("updated_at")) or now
        if state == "running" and _minutes(last, now) >= cfg.stalled_minutes:
            out.append(Alert(f"stalled|{p.id}|{t['task_id']}", "stalled", p.id, agent, last,
                             f"executando sem nenhum evento há {int(_minutes(last, now))} min", t["task_id"]))  # fmt: skip
        if state == "delegated" and _minutes(updated, now) >= cfg.not_received_minutes:
            out.append(Alert(f"not_received|{p.id}|{t['task_id']}", "not_received", p.id, agent, updated,
                             f"delegada há {int(_minutes(updated, now))} min e ainda não recebida", t["task_id"]))  # fmt: skip
        if state in ("running", "asked", "delegated") and item.roles_state.get(role or "") == "fechada":
            out.append(Alert(f"session_closed|{p.id}|{t['task_id']}", "session_closed", p.id, agent, last,
                             f"a sessão fechou com a tarefa em {state}", t["task_id"]))  # fmt: skip
        if state in ("done", "needs_review") and local_state.get(t["task_id"]) not in FINAL_STATES:
            if _minutes(updated, now) >= cfg.done_minutes:
                out.append(Alert(f"done_unchecked|{p.id}|{t['task_id']}", "done_unchecked", p.id, agent, updated,
                                 "report recebido; o arquivo da tarefa ainda não foi atualizado", t["task_id"]))  # fmt: skip
    window = [e for e in item.refusals if (_local(e.get("ts")) or now) >= now - timedelta(hours=1)]
    grouped: dict[str, list[dict]] = {}
    for e in window:
        grouped.setdefault(str(e.get("role_from") or "?"), []).append(e)
    for role, events in grouped.items():
        first = min(_local(e.get("ts")) or now for e in events)
        last_text = " ".join(str(events[0].get("text", "")).split())[:110]
        agent = p.agent_name(role) if role in p.roles else role
        out.append(Alert(f"refused|{p.id}|{role}", "refused", p.id, agent, first,
                         f"{last_text}" + (f" · {len(events)} vezes" if len(events) > 1 else ""), count=len(events)))  # fmt: skip
    return out


# ------------------------------------------------------------ estado e avisos


def in_quiet_hours(now: datetime, start: str | None, end: str | None) -> bool:
    """Silêncio programado (pode atravessar a meia-noite)."""
    if not start or not end:
        return False
    current = now.strftime("%H:%M")
    return start <= current < end if start <= end else (current >= start or current < end)


class AlertStore:
    """`alertas.json` na pasta de instalação: visto, reconhecido, silenciado e o que já foi avisado."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths.install_dir() / "alertas.json")
        self.lock_path = self.path.with_suffix(".lock")
        self.data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        for key in ("seen", "ack", "snooze", "notified", "first"):
            self.data.setdefault(key, {})

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=1) + "\n")

    @contextmanager
    def locked(self) -> Iterator[AlertStore]:
        """Trava entre processos (TUI e watcher) e recarrega o arquivo antes de decidir."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                self.reload()
                yield self
                self.save()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def apply(self, alerts: list[Alert], settings: AlertSettings, now: datetime | None = None) -> list[Alert]:
        """Dá a cada alerta o seu estado (novo, visto, reconhecido ou silenciado)."""
        now = now or datetime.now().astimezone()
        out = []
        live = {a.key for a in alerts}
        for a in alerts:
            if a.kind in ("delegator_down", "waiting"):  # sem origem no delegador: vale a primeira vez que o vimos
                a = replace(a, since=_local(self.data["first"].setdefault(a.key, a.since.isoformat())) or a.since)
            iso = a.since.isoformat()
            until = _local(self.data["snooze"].get(a.key))
            if a.kind in settings.muted_kinds or (until and until > now):
                state = "silenciado"
            elif self.data["ack"].get(a.key) == iso:
                state = "reconhecido"
            elif self.data["seen"].get(a.key) == iso:
                state = "visto"
            else:
                state = "novo"
            out.append(replace(a, state=state))
        for table in ("first",):  # esquece o que já se resolveu (um novo episódio começa do zero)
            for key in [k for k in self.data[table] if k not in live]:
                del self.data[table][key]
        return out

    def mark_seen(self, alerts: list[Alert]) -> None:
        with self.locked():
            for a in alerts:
                self.data["seen"][a.key] = a.since.isoformat()

    def acknowledge(self, alert: Alert) -> None:
        with self.locked():
            self.data["ack"][alert.key] = alert.since.isoformat()

    def snooze(self, alert: Alert, minutes: int, now: datetime | None = None) -> None:
        with self.locked():
            self.data["snooze"][alert.key] = (
                (now or datetime.now().astimezone()) + timedelta(minutes=minutes)
            ).isoformat()

    def prune(self, keys: set[str]) -> None:
        """Remove registros de alertas que já não existem (mantém o arquivo pequeno)."""
        with self.locked():
            for table in ("seen", "ack", "snooze", "notified"):
                for key in [k for k in self.data[table] if k not in keys]:
                    del self.data[table][key]


ENV_DISABLE = "DEV_ORCHESTRA_DISABLE_NOTIFY"


def _noop_spawn(*args: Any, **kwargs: Any) -> None:
    return None


class Notifier:
    """Som e notificação do sistema no Linux (notify-send; pw-play, paplay ou canberra-gtk-play)."""

    SOUND_DIR = Path("/usr/share/sounds/freedesktop/stereo")
    SOUND_FILES = {
        "alarme": ("alarm-clock-elapsed.oga", "dialog-warning.oga", "bell.oga"),
        "mensagem": ("message.oga", "message-new-instant.oga", "dialog-information.oga"),
    }
    CANBERRA = {"alarme": "alarm-clock-elapsed", "mensagem": "message"}

    def __init__(
        self,
        settings: AlertSettings,
        *,
        spawn: Callable[..., Any] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        sound_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        # DEV_ORCHESTRA_DISABLE_NOTIFY=1 (usado nos testes) nunca dispara som nem notificação de verdade
        self._spawn = spawn or (_noop_spawn if os.environ.get(ENV_DISABLE) else subprocess.Popen)
        self._which = which
        self.sound_dir = sound_dir or self.SOUND_DIR

    def _run(self, args: list[str]) -> bool:
        try:
            self._spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True)  # fmt: skip
        except OSError:
            return False
        return True

    def os_notification(self, title: str, body: str, severity: str) -> bool:
        if not self._which("notify-send"):
            return False
        critical = severity == CRIT
        args = ["notify-send", "-a", "dev-orchestra", "-u", "critical" if critical else "normal",
                "-i", "dialog-warning" if severity != INFO else "dialog-information",
                "-h", "string:category:dev-orchestra"]  # fmt: skip
        if not critical:
            args += ["-t", "10000"]
        return self._run([*args, title, body])

    def _sound_file(self, kind: str) -> Path | None:
        for name in self.SOUND_FILES.get(kind, ()):
            path = self.sound_dir / name
            if path.is_file():
                return path
        return None

    def play(self, kind: str) -> str | None:
        """Toca o som (`alarme` ou `mensagem`). Devolve o player usado, ou None se não há como tocar."""
        file = self._sound_file(kind)
        volume = max(0, min(100, self.settings.volume)) / 100
        if file and self._which("pw-play") and self._run(["pw-play", f"--volume={volume:.2f}", str(file)]):
            return "pw-play"
        if file and self._which("paplay") and self._run(["paplay", f"--volume={int(volume * 65536)}", str(file)]):
            return "paplay"
        if self._which("canberra-gtk-play"):
            args = ["canberra-gtk-play", "-i", self.CANBERRA.get(kind, "message")]
            if self._run(args):
                return "canberra-gtk-play"
        return None

    def notify(self, alert: Alert, *, sound: bool = True) -> dict[str, Any]:
        """Avisa por som e notificação do sistema, conforme as preferências. Devolve o que foi feito."""
        done: dict[str, Any] = {"os": False, "sound": None}
        if self.settings.os_notify:
            title = f"{SEVERITY_ICON[alert.severity]} {alert.label} — {alert.agent}"
            body = alert.message + (f"\n{alert.project} · {alert.task}" if alert.task else f"\n{alert.project}")
            done["os"] = self.os_notification(title, body, alert.severity)
        kind = SOUND_FOR[alert.severity]
        if sound and self.settings.sound and kind:
            done["sound"] = self.play(kind)
        return done


def dispatch(
    alerts: list[Alert],
    settings: AlertSettings,
    notifier: Notifier,
    store: AlertStore,
    now: datetime | None = None,
) -> list[tuple[Alert, dict[str, Any]]]:
    """Avisa (som e sistema) o que é novo e repete o crítico sem resposta. Só um processo avisa cada alerta."""
    now = now or datetime.now().astimezone()
    fired: list[tuple[Alert, dict[str, Any]]] = []
    quiet = in_quiet_hours(now, settings.quiet_start, settings.quiet_end)
    with store.locked():
        for a in alerts:
            if a.severity == INFO or a.state in ("silenciado", "reconhecido"):
                continue
            record = store.data["notified"].get(a.key)
            iso = a.since.isoformat()
            if record is None or record.get("since") != iso:
                fire, count = True, 1
            elif a.severity == CRIT and record["n"] < settings.repeat_max:
                last = _local(record["last"]) or now
                fire, count = _minutes(last, now) >= settings.repeat_minutes, record["n"] + 1
            else:
                fire, count = False, record["n"]
            if not fire:
                continue
            store.data["notified"][a.key] = {"since": iso, "last": now.isoformat(), "n": count}
            fired.append((a, notifier.notify(a, sound=not quiet)))
    return fired


def refresh(
    projects: list[Project],
    cfg: GlobalConfig,
    services: Services | None,
    store: AlertStore,
    notifier: Notifier | None = None,
    *,
    notify: bool = True,
    now: datetime | None = None,
) -> tuple[list[Alert], list[tuple[Alert, dict[str, Any]]]]:
    """Um ciclo completo: coleta, calcula, aplica o estado e (opcional) avisa. Devolve (alertas, avisados)."""
    now = now or datetime.now().astimezone()
    inputs = gather(projects, cfg, services)
    alerts = compute(inputs, cfg.alerts, now)
    with store.locked():
        alerts = store.apply(alerts, cfg.alerts, now)
    fired = dispatch(alerts, cfg.alerts, notifier or Notifier(cfg.alerts), store, now) if notify else []
    store.prune({a.key for a in alerts})
    return alerts, fired


__all__ = [
    "Alert",
    "AlertStore",
    "Notifier",
    "ProjectInput",
    "compute",
    "dispatch",
    "gather",
    "in_quiet_hours",
    "refresh",
]
