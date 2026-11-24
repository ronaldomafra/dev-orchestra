"""Alertas: sinais dos hooks, motor de regras, estado (visto/reconhecido/silenciado), avisos e notificador."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta

import pytest

from dev_orchestra.core import alerts as al
from dev_orchestra.core import config
from dev_orchestra.core.liveness import Services
from dev_orchestra.core.models import AlertSettings, Project, Role
from dev_orchestra.delegator.core import Delegator
from dev_orchestra.delegator.db import Store
from dev_orchestra.delegator.errors import DelegatorError

NOW = datetime(2026, 11, 15, 12, 0, 0).astimezone()


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def make(tmp_path) -> Project:
    roles = {r: Role(cli="claude") for r in ("orchestrator", "developer")}
    return Project(id="demo", name="Demo", path=tmp_path, roles=roles, delegator=True)


def task(tid, state, *, role="developer", last=1.0, updated=1.0):
    return {"task_id": tid, "state": state, "to_role": role, "last_event": iso(last), "updated_at": iso(updated)}


def item(tmp_path, *, status=None, signals=(), refusals=(), roles=None, local=()):
    status = {"tasks": [], "open_questions": [], "sessions": []} if status is None else status
    return al.ProjectInput(make(tmp_path), status, list(signals), list(refusals), roles or {}, local_tasks=list(local))


def kinds(alerts):
    return sorted(a.kind for a in alerts)


# --------------------------------------------------------------------- motor


def test_sem_nada_a_dizer_nao_ha_alertas(tmp_path):
    assert al.compute([item(tmp_path)], AlertSettings(), NOW) == []


def test_delegador_fora_do_ar(tmp_path):
    out = al.compute([al.ProjectInput(make(tmp_path), None)], AlertSettings(), NOW)
    assert kinds(out) == ["delegator_down"] and out[0].severity == al.WARN


def test_sinais_dos_hooks_viram_alertas_criticos_com_a_tarefa(tmp_path):
    signals = [
        {"kind": "permission", "agent": "demo-developer", "message": "Bash: rm x", "task_id": "T-1",
         "created_at": iso(2), "detail": "{}"},
        {"kind": "question", "agent": "demo-orchestrator", "message": "Qual modelo?", "task_id": None,
         "created_at": iso(5)},
    ]  # fmt: skip
    out = al.compute([item(tmp_path, signals=signals)], AlertSettings(), NOW)
    assert [a.kind for a in out] == ["permission", "question"]  # críticos primeiro, o mais recente antes
    assert out[0].task == "T-1" and out[0].agent == "demo-developer" and out[0].severity == al.CRIT
    assert out[0].key == "permission|demo|demo-developer" and out[0].age_text(NOW) == "2 min"


def test_pergunta_de_worker_so_alerta_depois_do_limite(tmp_path):
    status = {"tasks": [task("T-1", "asked")], "sessions": [],
              "open_questions": [{"id": 3, "task_id": "T-1", "question": "layout?", "created_at": iso(4)}]}  # fmt: skip
    assert al.compute([item(tmp_path, status=status)], AlertSettings(), NOW) == []
    status["open_questions"][0]["created_at"] = iso(6)
    out = al.compute([item(tmp_path, status=status)], AlertSettings(), NOW)
    assert kinds(out) == ["worker_question"] and out[0].agent == "demo-developer" and "layout?" in out[0].message


def test_tarefa_parada_nao_recebida_e_sessao_fechada(tmp_path):
    status = {
        "sessions": [], "open_questions": [],
        "tasks": [task("A", "running", last=11), task("B", "running", last=3), task("C", "delegated", updated=3),
                  task("D", "delegated", updated=1), task("E", "running", last=1)],
    }  # fmt: skip
    out = al.compute([item(tmp_path, status=status, roles={"developer": "ociosa"})], AlertSettings(), NOW)
    assert sorted((a.kind, a.task) for a in out) == [("not_received", "C"), ("stalled", "A")]
    closed = al.compute([item(tmp_path, status=status, roles={"developer": "fechada"})], AlertSettings(), NOW)
    assert {(a.kind, a.task) for a in closed if a.kind == "session_closed"} == {("session_closed", t) for t in "ABCDE"}
    cfg = AlertSettings(stalled_minutes=2)
    assert ("stalled", "B") in {(a.kind, a.task) for a in al.compute([item(tmp_path, status=status)], cfg, NOW)}


def test_concluida_aguardando_conferencia_so_se_o_arquivo_nao_estiver_final(tmp_path):
    import dev_orchestra.core.tasks as tasks_mod

    status = {
        "sessions": [],
        "open_questions": [],
        "tasks": [task("T-1", "done", updated=3), task("T-2", "done", updated=3)],
    }
    local = [
        tasks_mod.Task("T-1", "x", "developer", state="ready"),
        tasks_mod.Task("T-2", "x", "developer", state="done"),
    ]
    out = al.compute([item(tmp_path, status=status, local=local)], AlertSettings(), NOW)
    assert [(a.kind, a.task, a.severity) for a in out] == [("done_unchecked", "T-1", al.INFO)]


def test_recusas_recentes_agrupadas_por_papel(tmp_path):
    refusals = [
        {"ts": iso(5), "role_from": "developer", "text": "delegate negado: papel 'developer' não pode"},
        {"ts": iso(30), "role_from": "developer", "text": "answer negado"},
        {"ts": iso(120), "role_from": "developer", "text": "antiga"},  # fora da janela de 1 h
    ]
    out = al.compute([item(tmp_path, refusals=refusals)], AlertSettings(), NOW)
    assert kinds(out) == ["refused"] and out[0].count == 2 and "2 vezes" in out[0].message
    assert out[0].since == NOW - timedelta(minutes=30)


# ------------------------------------------------------------ estado persistente


@pytest.fixture
def store(tmp_path):
    return al.AlertStore(tmp_path / "alertas.json")


def one(kind="permission", since=None, key="permission|demo|a", **kw):
    return al.Alert(key, kind, "demo", "a", since or NOW, "m", **kw)


def test_estados_novo_visto_reconhecido_e_silenciado(store):
    a = one()
    cfg = AlertSettings()
    assert store.apply([a], cfg, NOW)[0].state == "novo"
    store.mark_seen([a])
    assert store.apply([a], cfg, NOW)[0].state == "visto"
    store.acknowledge(a)
    assert store.apply([a], cfg, NOW)[0].state == "reconhecido"
    again = one(since=NOW + timedelta(minutes=5))  # o mesmo alerta voltou: é um episódio novo
    assert store.apply([again], cfg, NOW)[0].state == "novo"
    store.snooze(again, 30, NOW)
    assert store.apply([again], cfg, NOW)[0].state == "silenciado"
    assert store.apply([again], cfg, NOW + timedelta(minutes=31))[0].state == "novo"  # a soneca acabou
    assert store.apply([one()], AlertSettings(muted_kinds=["permission"]), NOW)[0].state == "silenciado"


def test_estado_sobrevive_a_outro_processo(tmp_path, store):
    a = one()
    store.acknowledge(a)
    other = al.AlertStore(tmp_path / "alertas.json")
    assert other.apply([a], AlertSettings(), NOW)[0].state == "reconhecido"
    other.prune(set())
    assert al.AlertStore(tmp_path / "alertas.json").data["ack"] == {}


def test_delegador_fora_do_ar_conta_a_partir_da_primeira_vez_que_foi_visto(store):
    a = al.Alert("delegator_down|demo", "delegator_down", "demo", "delegador", NOW, "x")
    first = store.apply([a], AlertSettings(), NOW)[0]
    later = store.apply([al.replace(a, since=NOW + timedelta(minutes=3))], AlertSettings(), NOW)[0]
    assert later.since == first.since  # não "rejuvenesce" a cada ciclo
    store.apply([], AlertSettings(), NOW)  # voltou ao ar: esquece
    assert store.data["first"] == {}


# --------------------------------------------------------------------- avisos


class Spawn:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        self.calls.append(args)


def notifier(tmp_path, spawn=None, tools=("notify-send", "pw-play", "paplay", "canberra-gtk-play"), **cfg):
    sounds = tmp_path / "sons"
    sounds.mkdir(exist_ok=True)
    for name in ("alarm-clock-elapsed.oga", "message.oga"):
        (sounds / name).write_bytes(b"x")
    spawn = spawn or Spawn()
    return al.Notifier(AlertSettings(**cfg), spawn=spawn, which=lambda n: f"/usr/bin/{n}" if n in tools else None,
                       sound_dir=sounds), spawn  # fmt: skip


def test_notificador_usa_urgencia_e_som_conforme_a_severidade(tmp_path):
    n, spawn = notifier(tmp_path, volume=50)
    done = n.notify(one(kind="permission", task="T-1"))
    assert done == {"os": True, "sound": "pw-play"}
    os_call, sound_call = spawn.calls
    assert os_call[:2] == ["notify-send", "-a"] and "critical" in os_call and "T-1" in os_call[-1]
    assert os_call[-2].startswith("▲ aguardando permissão")
    assert (
        sound_call[0] == "pw-play"
        and sound_call[1] == "--volume=0.50"
        and sound_call[2].endswith("alarm-clock-elapsed.oga")
    )
    spawn.calls.clear()
    n.notify(al.Alert("k", "stalled", "demo", "a", NOW, "m"))
    assert "normal" in spawn.calls[0] and spawn.calls[1][2].endswith("message.oga")  # atenção: som de mensagem
    spawn.calls.clear()
    assert n.notify(al.Alert("k", "done_unchecked", "demo", "a", NOW, "m")) == {
        "os": True,
        "sound": None,
    }  # info: sem som


def test_notificador_respeita_as_preferencias_e_os_players_disponiveis(tmp_path):
    n, spawn = notifier(tmp_path, sound=False, os_notify=False)
    assert n.notify(one()) == {"os": False, "sound": None} and spawn.calls == []
    n, spawn = notifier(tmp_path, tools=("paplay",))
    assert n.notify(one()) == {"os": False, "sound": "paplay"}
    n, spawn = notifier(tmp_path, tools=("canberra-gtk-play",))
    assert n.play("alarme") == "canberra-gtk-play" and spawn.calls[-1] == [
        "canberra-gtk-play",
        "-i",
        "alarm-clock-elapsed",
    ]
    n, _ = notifier(tmp_path, tools=())
    assert n.play("alarme") is None and n.os_notification("t", "b", al.CRIT) is False
    n, _ = notifier(tmp_path)
    assert n.notify(one(), sound=False)["sound"] is None  # horário de silêncio: só a notificação


def test_horario_de_silencio_atravessa_a_meia_noite():
    at = lambda h, m=0: NOW.replace(hour=h, minute=m)  # noqa: E731
    assert al.in_quiet_hours(at(23), "22:00", "07:00") and al.in_quiet_hours(at(3), "22:00", "07:00")
    assert not al.in_quiet_hours(at(12), "22:00", "07:00") and not al.in_quiet_hours(at(7), "22:00", "07:00")
    assert al.in_quiet_hours(at(13), "12:00", "14:00") and not al.in_quiet_hours(at(15), "12:00", "14:00")
    assert not al.in_quiet_hours(at(23), None, None)


def test_dispatch_avisa_uma_vez_repete_o_critico_e_respeita_estados(tmp_path, store):
    n, spawn = notifier(tmp_path)
    cfg = AlertSettings(quiet_start=None, quiet_end=None, repeat_minutes=5, repeat_max=2)
    crit, warn, info = one(), one("stalled", key="stalled|demo|T"), one("done_unchecked", key="done_unchecked|demo|T")
    first = al.dispatch([crit, warn, info], cfg, n, store, NOW)
    assert [a.kind for a, _ in first] == ["permission", "stalled"]  # info nunca avisa
    assert al.dispatch([crit, warn], cfg, n, store, NOW + timedelta(minutes=1)) == []  # não repete logo
    again = al.dispatch([crit, warn], cfg, n, store, NOW + timedelta(minutes=6))
    assert [a.kind for a, _ in again] == ["permission"]  # só o crítico repete
    assert al.dispatch([crit], cfg, n, store, NOW + timedelta(minutes=12)) == []  # repeat_max = 2
    quiet_ack = al.replace(crit, state="reconhecido", since=NOW + timedelta(hours=1))
    assert al.dispatch([quiet_ack, al.replace(warn, state="silenciado")], cfg, n, store, NOW + timedelta(hours=2)) == []
    newer = al.replace(crit, since=NOW + timedelta(hours=3))  # novo episódio do mesmo alerta
    assert len(al.dispatch([newer], cfg, n, store, NOW + timedelta(hours=3))) == 1


def test_dispatch_no_silencio_so_notifica(tmp_path, store):
    n, spawn = notifier(tmp_path)
    cfg = AlertSettings(quiet_start="00:00", quiet_end="23:59")
    [(_, done)] = al.dispatch([one()], cfg, n, store, NOW.replace(hour=12))
    assert done == {"os": True, "sound": None}


def test_dois_processos_nao_avisam_o_mesmo_alerta(tmp_path, store):
    n, spawn = notifier(tmp_path)
    other = al.AlertStore(tmp_path / "alertas.json")  # o watcher, por exemplo
    cfg = AlertSettings(quiet_start=None, quiet_end=None)
    assert len(al.dispatch([one()], cfg, n, store, NOW)) == 1
    assert al.dispatch([one()], cfg, n, other, NOW) == []


# ------------------------------------------------ sinais no delegador (hooks)


class Registry:
    def __init__(self, tmp_path):
        self.p = make(tmp_path)

    def get(self, pid):
        return self.p if pid == "demo" else None


@pytest.fixture
def d(tmp_path):
    return Delegator(Store(tmp_path / "estado.db"), Registry(tmp_path))


def test_sinal_cria_atualiza_e_limpa(d):
    sid = d.register_session("demo", "developer", "claude", "demo-developer")["session_id"]
    orch = d.register_session("demo", "orchestrator", "claude", "demo-orchestrator")["session_id"]
    d.delegate(orch, "T-1", "developer", "x", "haiku", "low", "r", True)
    d.receive(sid)
    first = d.signal("demo", "demo-developer", "permission", "Bash: ls", "{}")
    again = d.signal("demo", "demo-developer", "permission", "Bash: pwd", "{}")  # a mesma espera: atualiza
    assert first["alert_id"] == again["alert_id"]
    [alert] = d.active_signals("demo")
    assert alert["message"] == "Bash: pwd" and alert["task_id"] == "T-1" and alert["role"] == "developer"
    assert d.active_signals("other") == [] and d.clear_signals("demo", "demo-developer") == {"cleared": 1}
    assert d.active_signals("demo") == []
    assert d.clear_signals("demo", "demo-developer") == {"cleared": 0}
    ev = [e for e in d.timeline("demo") if e["kind"] == "alert"]
    assert len(ev) == 1 and "aguardando permissão" in ev[0]["text"]  # um evento por episódio, não por atualização


def test_sinal_de_agente_ainda_nao_registrado_usa_o_nome_do_cadastro(d):
    d.signal("demo", "demo-orchestrator", "question", "Qual modelo?")
    [alert] = d.active_signals()
    assert alert["role"] == "orchestrator" and alert["task_id"] is None


def test_sinal_invalido(d):
    with pytest.raises(DelegatorError, match="signal kind"):
        d.signal("demo", "x", "outra")
    with pytest.raises(DelegatorError, match="agent is required"):
        d.signal("demo", " ", "permission")
    with pytest.raises(DelegatorError, match="unknown project"):
        d.signal("nada", "x", "permission")


def test_status_traz_idade_da_pergunta_e_ultimo_evento_da_tarefa(d):
    orch = d.register_session("demo", "orchestrator", "claude", "demo-orchestrator")["session_id"]
    dev = d.register_session("demo", "developer", "claude", "demo-developer")["session_id"]
    d.delegate(orch, "T-1", "developer", "x", "haiku", "low", "r", True)
    d.receive(dev)
    d.ask(dev, "T-1", "layout?")
    data = d.status_data("demo")
    assert data["open_questions"][0]["created_at"] and data["tasks"][0]["last_event"]


# ----------------------------------------------------- comando dos hooks (signal)


def test_comando_do_hook_descreve_permissao_pergunta_e_erro():
    from dev_orchestra.delegator.signals import describe

    msg, detail = describe("permission", {"tool_name": "Bash", "tool_input": {"command": "rm -rf  /tmp/x\n"}})
    assert msg == "Bash: rm -rf /tmp/x" and "rm -rf" in detail
    msg, detail = describe("question", {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
        {"question": "Qual modelo?", "options": []}, {"question": "Qual cor?"}]}})  # fmt: skip
    assert msg == "Qual modelo? · Qual cor?"
    assert describe("permission", {"message": "Claude needs your permission to use Bash"})[0].startswith("Claude needs")
    assert describe("turn_error", {"error": "rate_limit"})[0] == "rate_limit"
    assert describe("permission", {})[0] == "a sessão espera a sua aprovação"
    assert describe("turn_error", {})[0] == "o turno terminou com erro"


def test_comando_do_hook_nunca_escreve_no_stdout_e_sempre_sai_com_zero(monkeypatch, capsys):
    from dev_orchestra.delegator import signals

    calls = []

    class FakeClient:
        def __init__(self, url, timeout=0):
            pass

        def signal(self, *a):
            calls.append(("signal", a))

        def clear_signals(self, *a):
            calls.append(("clear", a))

    monkeypatch.setattr(signals, "DelegatorClient", FakeClient)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})))
    assert signals.main(["--project", "demo", "--kind", "permission", "--agent", "demo-developer"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert signals.main(["--project", "demo", "--kind", "clear", "--agent", "demo-developer"]) == 0
    assert calls[0][0] == "signal" and calls[0][1][:3] == ("demo", "demo-developer", "permission")
    assert calls[1] == ("clear", ("demo", "demo-developer"))
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(signals, "DelegatorClient", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fora do ar")))
    assert signals.main(["--project", "demo", "--kind", "permission", "--agent", "x"]) == 0  # falha silenciosa
    monkeypatch.delenv("DEV_ORCHESTRA_AGENT", raising=False)
    monkeypatch.setattr("dev_orchestra.delegator.signals.agent_from_env", lambda: None)
    assert signals.main(["--project", "demo", "--kind", "permission"]) == 0  # sem identidade


# --------------------------------------------------------------- configuração


def test_configuracao_dos_alertas_vai_e_volta_pelo_toml(tmp_path):
    cfg = config.load_global()
    cfg.alerts = AlertSettings(sound=False, volume=40, quiet_start=None, quiet_end=None, stalled_minutes=15,
                               muted_kinds=["done_unchecked"])  # fmt: skip
    config.save_global(cfg)
    back = config.load_global().alerts
    assert back.sound is False and back.volume == 40 and back.quiet_start is None and back.stalled_minutes == 15
    assert back.muted_kinds == ["done_unchecked"] and back.os_notify is True
    assert config.load_global().alerts == back
    assert AlertSettings(volume=500).volume == 100
    with pytest.raises(ValueError, match="horário inválido"):
        AlertSettings(quiet_start="25:00")
    assert AlertSettings().quiet_start == "22:00"


def test_pergunta_a_pessoa_nao_vira_dois_alertas(monkeypatch):
    """AskUserQuestion dispara PreToolUse e também PermissionRequest: o hook de permissão a trata como pergunta."""
    from dev_orchestra.delegator import signals

    sent = []

    class FakeClient:
        def __init__(self, url, timeout=0):
            pass

        def signal(self, project, agent, kind, message, detail):
            sent.append((kind, message))

    monkeypatch.setattr(signals, "DelegatorClient", FakeClient)
    payload = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Qual cor?"}]}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    signals.main(["--project", "demo", "--kind", "permission", "--agent", "a"])
    assert sent == [("question", "Qual cor?")]


def test_notificacao_de_permissao_durante_uma_pergunta_nao_duplica(d):
    d.signal("demo", "demo-orchestrator", "question", "Qual cor?")
    merged = d.signal("demo", "demo-orchestrator", "permission", "Claude needs your permission")
    assert merged["merged"] == "question"
    assert [a["kind"] for a in d.active_signals("demo")] == ["question"]
    d.clear_signals("demo", "demo-orchestrator")
    d.signal("demo", "demo-orchestrator", "permission", "Bash: ls")  # sem pergunta ativa: alerta de permissão normal
    assert [a["kind"] for a in d.active_signals("demo")] == ["permission"]


def test_ociosidade_tambem_limpa_os_sinais():
    from dev_orchestra.delegator.install import SIGNAL_HOOKS

    assert ("Notification", "idle_prompt", "clear") in SIGNAL_HOOKS


# ------------------------------------- o status ao vivo do Claude manda (waiting = espera a pessoa)


def test_sinal_de_hook_velho_e_ignorado_se_a_sessao_nao_espera_mais(tmp_path):
    signals = [{"kind": "question", "agent": "demo-orchestrator", "message": "Qual cor?", "created_at": iso(3)}]
    waiting = al.compute([item(tmp_path, signals=signals)], AlertSettings(), NOW)
    assert kinds(waiting) == ["question"]  # sem status ao vivo (None): o sinal vale
    it = item(tmp_path, signals=signals)
    it.roles_raw = {"orchestrator": "idle", "developer": "idle"}  # depois de um Esc a sessão voltou a "idle"
    assert al.compute([it], AlertSettings(), NOW) == []
    it.roles_raw = {"orchestrator": "waiting", "developer": "idle"}
    assert kinds(al.compute([it], AlertSettings(), NOW)) == ["question"]  # ainda espera: vale, com o detalhe do hook


def test_sessao_esperando_sem_hook_vira_alerta_generico(tmp_path):
    it = item(tmp_path)
    it.roles_raw = {"orchestrator": "idle", "developer": "waiting"}
    [alert] = al.compute([it], AlertSettings(), NOW)
    assert alert.kind == "waiting" and alert.agent == "demo-developer" and alert.severity == al.CRIT
    assert alert.key == "waiting|demo|demo-developer"
    signals = [{"kind": "permission", "agent": "demo-developer", "message": "Bash: ls", "created_at": iso(1)}]
    it = item(tmp_path, signals=signals)
    it.roles_raw = {"developer": "waiting"}
    assert kinds(al.compute([it], AlertSettings(), NOW)) == ["permission"]  # o hook explica: não duplica


def test_erro_de_turno_nao_depende_do_status(tmp_path):
    signals = [{"kind": "turn_error", "agent": "demo-developer", "message": "rate_limit", "created_at": iso(4)}]
    it = item(tmp_path, signals=signals)
    it.roles_raw = {"developer": "idle"}
    assert kinds(al.compute([it], AlertSettings(), NOW)) == ["turn_error"]


def test_espera_sem_hook_conta_do_primeiro_momento_visto(store):
    a = al.Alert("waiting|demo|a", "waiting", "demo", "a", NOW, "x")
    first = store.apply([a], AlertSettings(), NOW)[0]
    later = store.apply([al.replace(a, since=NOW + timedelta(minutes=2))], AlertSettings(), NOW)[0]
    assert later.since == first.since


def test_status_cru_do_claude_chega_ao_status_dos_papeis(tmp_path):
    from dev_orchestra.core.liveness import ClaudeSession, collect_status

    project = make(tmp_path)
    sessions = [ClaudeSession(1, str(project.path), "demo-developer", "s", "waiting", "interactive")]
    svc = Services(claude_sessions=lambda: sessions, codex_threads=lambda u, c: None, workstreams=lambda n, c: [],
                   ai_memory_projects=lambda: [], delegator_status=lambda url, p: {"tasks": []})  # fmt: skip
    report = collect_status(project, config.load_global(), svc)
    assert {r.role: r.raw_status for r in report.roles} == {"orchestrator": None, "developer": "waiting"}
