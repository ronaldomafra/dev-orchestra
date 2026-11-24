"""Alertas na TUI (sino, cartões, central, configuração, avisos) e na CLI (list, test, watch, serviço)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta

import pytest
from textual.widgets import DataTable, Static

from dev_orchestra import alerts_cli, cli
from dev_orchestra.core import alerts as al
from dev_orchestra.core import config
from dev_orchestra.core.liveness import ClaudeSession, Services
from dev_orchestra.core.models import AlertSettings, Project, Role
from dev_orchestra.tui.alerts import AlertSettingsScreen, AlertsScreen
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.forms import ConfirmScreen
from dev_orchestra.tui.menus import MenuBar
from dev_orchestra.tui.screens import ProjectScreen
from dev_orchestra.tui.widgets import ProjectCard

WIDE = (190, 54)


def utc_iso(minutes_ago: float) -> str:
    return (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def make(tmp_path, pid="demo", delegator=True) -> Project:
    folder = tmp_path / pid
    folder.mkdir(exist_ok=True)
    roles = {r: Role(cli="claude") for r in ("orchestrator", "developer")}
    return Project(id=pid, name=pid.title(), path=folder, roles=roles, delegator=delegator)


class Spy:
    """Notificador de mentira: guarda o que seria avisado (nenhum som, nenhuma notificação de verdade)."""

    def __init__(self):
        self.settings = AlertSettings()
        self.sent: list[al.Alert] = []

    def notify(self, alert, *, sound=True):
        self.sent.append(alert)
        return {"os": True, "sound": "pw-play" if sound else None}


class Env:
    def __init__(self, tmp_path):
        self.signals = [
            {"kind": "permission", "agent": "demo-developer", "message": "Bash: python3 -I - reescrever index.html",
             "task_id": "T-1", "created_at": utc_iso(2), "detail": '{"command": "python3"}'},
            {"kind": "question", "agent": "demo-orchestrator", "message": "Qual modelo aprovar?", "task_id": None,
             "created_at": utc_iso(5)},
        ]  # fmt: skip
        running = {"task_id": "T-1", "state": "running", "to_role": "developer"}
        running |= {"last_event": utc_iso(1), "updated_at": utc_iso(1)}
        self.status = {"project": "demo", "sessions": [], "open_questions": [], "tasks": [running]}
        self.sessions = [
            ClaudeSession(1, str(tmp_path / "demo"), "demo-orchestrator", "s", "waiting", "interactive"),
            ClaudeSession(2, str(tmp_path / "demo"), "demo-developer", "s", "waiting", "interactive"),
        ]
        self.up = True

    def services(self) -> Services:
        return Services(
            claude_sessions=lambda: self.sessions,
            codex_threads=lambda u, c: None,
            workstreams=lambda n, c: [],
            ai_memory_projects=lambda: [("default", "demo")],
            delegator_status=lambda url, project: self.status if self.up else None,
            delegator_events=lambda url, project: [],
            delegator_alerts=lambda url, project: list(self.signals),
            delegator_timeline=lambda url, project: [],
        )


async def settle(app, pilot, times=3):
    for _ in range(times):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


async def boot(tmp_path, env=None, spy=None):
    config.save_project(make(tmp_path))
    env = env or Env(tmp_path)
    spy = spy or Spy()
    return env, spy, DevOrchestraApp(env.services(), notifier=spy)


async def test_alertas_aparecem_no_sino_nos_cartoes_e_avisam_uma_vez(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        assert {a.kind for a in app.alerts.items} == {"permission", "question"}
        bar = str(app.screen.query_one(MenuBar).render())
        assert "▲ 2 alertas" in bar
        card = app.screen.query_one(ProjectCard)
        assert card.has_class("alert-crit") and "▲ 2 alertas" in str(card.border_subtitle)
        body = str(card.render())
        assert "▲ aguardando permissão" in body and "▲ pergunta para a pessoa" in body
        assert len(spy.sent) == 2  # som e notificação para cada crítico, uma vez
        app.alerts.tick()
        await settle(app, pilot)
        assert len(spy.sent) == 2  # o próximo ciclo não repete


async def test_alerta_some_quando_o_hook_limpa(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        env.signals.clear()  # a pessoa aprovou: PostToolUse limpou os sinais
        env.sessions = [ClaudeSession(s.pid, s.cwd, s.name, s.session_id, "idle", s.kind) for s in env.sessions]
        app.alerts.tick()
        await settle(app, pilot)
        assert app.alerts.items == [] and "alerta" not in str(app.screen.query_one(MenuBar).render())
        assert not app.screen.query_one(ProjectCard).has_class("alert-crit")


async def test_delegador_fora_do_ar_gera_alerta_de_atencao(tmp_path):
    env, spy, app = await boot(tmp_path)
    env.signals.clear()
    env.up = False
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        [alert] = app.alerts.items
        assert alert.kind == "delegator_down" and alert.severity == al.WARN and len(spy.sent) == 1
        assert app.screen.query_one(ProjectCard).has_class("alert-warn")


async def test_sem_projeto_em_modo_delegador_nao_ha_alertas_nem_consultas(tmp_path):
    config.save_project(make(tmp_path, delegator=False))
    spy = Spy()
    app = DevOrchestraApp(Env(tmp_path).services(), notifier=spy)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        assert app.alerts.items == [] and spy.sent == []


async def open_alerts(app, pilot):
    await pilot.press("f3")
    await settle(app, pilot)
    assert isinstance(app.screen, AlertsScreen)
    return app.screen


async def test_central_lista_detalha_e_marca_como_visto(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        screen = await open_alerts(app, pilot)
        table = screen.query_one("#al-table", DataTable)
        assert table.row_count == 2
        first = screen.rows[0]
        assert first.kind == "permission"  # o mais recente dos críticos
        assert (
            table.get_row(first.key)[4].plain == "aguardando permissão" and table.get_row(first.key)[5].plain == "T-1"
        )
        detail = str(screen.query_one("#al-detail", Static).render())
        assert "demo-developer" in detail and "O QUE ACONTECEU" in detail and "reescrever index.html" in detail
        assert "nunca executado" in detail and "O QUE FAZER" in detail
        strip = str(screen.query_one("#al-strip", Static).render())
        assert "2 críticos" in strip and "Som ● ligado" in strip and "Silêncio 22:00–07:00" in strip
        center = app.alerts
        assert center.store.data["seen"].get(first.key)  # selecionar = ver
        screen.render_all()
        assert {a.state for a in screen.rows} == {"visto", "novo"} or all(
            a.state in ("novo", "visto") for a in screen.rows
        )


async def test_reconhecer_silenciar_e_filtros(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        screen = await open_alerts(app, pilot)
        await pilot.press("a")  # reconhece o selecionado
        await settle(app, pilot)
        assert [a.kind for a in screen.rows] == ["question"]  # reconhecidos saem da lista padrão
        start, end = screen._chip_ranges["reconhecido"]
        await pilot.click("#al-strip", offset=((start + end) // 2 + 1, 2))
        await settle(app, pilot)
        assert {a.kind for a in screen.rows} == {"permission", "question"}
        await pilot.press("s")  # silencia o selecionado por 30 min
        await settle(app, pilot)
        assert any(a.state == "silenciado" for a in app.alerts.items)
        await pilot.press("m")  # silencia o tipo todo
        await settle(app, pilot)
        assert config.load_global().alerts.muted_kinds
        assert "silenciados:" in str(screen.query_one("#al-rules", Static).render())


async def test_ir_ao_agente_e_ao_monitor(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await open_alerts(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert isinstance(app.screen, ProjectScreen) and app.screen.tab == "agents" and app.screen.project_id == "demo"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("o")
        await settle(app, pilot)
        from dev_orchestra.tui.monitor import MonitorScreen

        assert isinstance(app.screen, MonitorScreen) and app.screen._selected_id() == "demo"


async def test_cancelar_a_tarefa_do_alerta(tmp_path, monkeypatch):
    from dev_orchestra.delegator import client as client_mod

    calls = []

    def fake_cancel(self, project, task, reason=""):
        calls.append((project, task))
        return {"task_id": task, "task_state": "cancelled"}

    monkeypatch.setattr(client_mod.DelegatorClient, "cancel", fake_cancel)
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await open_alerts(app, pilot)
        await pilot.press("x")  # o primeiro tem a tarefa T-1
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await settle(app, pilot)
        assert calls == [("demo", "T-1")]


async def test_configuracao_salva_e_valida(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await open_alerts(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AlertSettingsScreen)
        screen.query_one("#a-volume").value = "35"
        screen.query_one("#a-stalled_minutes").value = "15"
        screen.query_one("#a-quiet_start").value = ""
        screen.query_one("#a-sound").value = False
        screen.action_save()
        await settle(app, pilot)
        saved = config.load_global().alerts
        assert saved.volume == 35 and saved.stalled_minutes == 15 and saved.quiet_start is None and saved.sound is False
        await pilot.press("c")
        await pilot.pause()
        app.screen.query_one("#a-quiet_end").value = "25:99"
        app.screen.action_save()
        await pilot.pause()
        assert isinstance(app.screen, AlertSettingsScreen)  # inválido: continua aberta, com o erro
        assert "horário inválido" in str(app.screen.query_one("#a-error", Static).render())


async def test_botao_de_teste_usa_o_notificador(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await open_alerts(app, pilot)
        before = len(spy.sent)
        await pilot.press("c")
        await pilot.pause()
        app.screen.action_test()
        await pilot.pause()
        assert len(spy.sent) == before + 2  # um crítico e um de atenção de exemplo


async def test_menu_alertas_e_atalho(tmp_path):
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("alt+l")
        await pilot.pause()
        labels = [str(o.prompt) for o in app.screen.query_one("#menu-list").options if o is not None]
        assert any("Abrir a central de alertas" in x for x in labels) and not any("Reconhecer" in x for x in labels)
        await pilot.press("enter")
        await settle(app, pilot)
        assert isinstance(app.screen, AlertsScreen)


# --------------------------------------------------------------------------- CLI


def run(*argv, services=None):
    out = io.StringIO()
    code = cli.main(list(argv), services=services, out=out)
    return code, out.getvalue()


def test_cli_list_mostra_os_alertas(tmp_path):
    config.save_project(make(tmp_path))
    code, out = run("alerts", "list", services=Env(tmp_path).services())
    assert code == 0 and "CRÍTICO" in out and "aguardando permissão" in out and "[T-1]" in out and "(NOVO)" in out


def test_cli_list_sem_alertas(tmp_path):
    config.save_project(make(tmp_path, delegator=False))
    code, out = run("alerts", "list", services=Env(tmp_path).services())
    assert (code, out.strip()) == (0, "Nenhum alerta.")


def test_cli_watch_um_ciclo_avisa_pelo_notificador(tmp_path, monkeypatch):
    config.save_project(make(tmp_path))
    spy = Spy()
    monkeypatch.setattr(alerts_cli.al, "Notifier", lambda settings: spy)
    code, out = run("alerts", "watch", "--once", "--interval", "0.1", services=Env(tmp_path).services())
    assert code == 0 and "aguardando permissão" in out and len(spy.sent) == 2
    code, out = run("alerts", "watch", "--once", services=Env(tmp_path).services())
    assert "aguardando permissão" not in out and len(spy.sent) == 2  # já avisados: o segundo ciclo fica quieto


def test_cli_test_dispara_as_tres_gravidades(tmp_path, monkeypatch):
    spy = Spy()
    monkeypatch.setattr(alerts_cli.al, "Notifier", lambda settings: spy)
    monkeypatch.setattr(alerts_cli.time, "sleep", lambda s: None)
    code, out = run("alerts", "test")
    assert (
        code == 0
        and [a.severity for a in spy.sent] == [al.CRIT, al.WARN, al.INFO]
        and out.count("notificação=sim") == 3
    )
    spy.sent.clear()
    run("alerts", "test", "--only", "crítico")
    assert [a.severity for a in spy.sent] == [al.CRIT]


def test_cli_install_service_nao_habilita_sozinho(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts_cli.Path, "home", classmethod(lambda cls: tmp_path))
    code, out = run("alerts", "install-service")
    unit = tmp_path / ".config" / "systemd" / "user" / "dev-orchestra-alerts.service"
    assert (
        code == 0 and unit.is_file() and "alerts watch" in unit.read_text() and "systemctl --user enable --now" in out
    )
    code, shown = run("alerts", "install-service", "--print")
    assert "ExecStart=" in shown and "WantedBy=default.target" in shown


@pytest.mark.parametrize("flag", ["DEV_ORCHESTRA_DISABLE_NOTIFY"])
def test_notificador_real_fica_desligado_nos_testes(flag):
    import os

    assert os.environ.get(flag) == "1"
    notifier = al.Notifier(AlertSettings(), which=lambda n: f"/usr/bin/{n}")
    assert notifier.play("alarme") in ("pw-play", "paplay", "canberra-gtk-play", None)  # nada é executado de verdade


async def test_sinal_velho_some_quando_a_sessao_deixa_de_esperar(tmp_path):
    """Esc numa pergunta não dispara Stop: o status ao vivo (`waiting` -> `idle`) derruba o alerta."""
    env, spy, app = await boot(tmp_path)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        assert len(app.alerts.items) == 2
        env.sessions = [ClaudeSession(s.pid, s.cwd, s.name, s.session_id, "idle", s.kind) for s in env.sessions]
        app.alerts.tick()  # os sinais continuam ativos no delegador, mas ninguém espera mais
        await settle(app, pilot)
        assert app.alerts.items == []


async def test_sessao_esperando_sem_hook_aparece_como_aguardando_voce(tmp_path):
    env, spy, app = await boot(tmp_path)
    env.signals.clear()  # sessão aberta antes de instalar os hooks de alerta: só o status ao vivo avisa
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        assert {a.kind for a in app.alerts.items} == {"waiting"} and len(spy.sent) == 2
        assert "aguardando você" in str(app.screen.query_one(ProjectCard).render())
