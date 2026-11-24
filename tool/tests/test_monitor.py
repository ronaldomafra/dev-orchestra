"""Tela Monitor: linha do tempo do delegador e da TUI, projetos, filtros, busca, pausa, detalhe e ações."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from textual.widgets import DataTable, Static

from dev_orchestra.core import config, state, tasks
from dev_orchestra.core import monitor as mon
from dev_orchestra.core.liveness import Services
from dev_orchestra.core.models import Project, Role
from dev_orchestra.delegator import client as client_mod
from dev_orchestra.tui import monitor as monitor_screen
from dev_orchestra.tui.app import DevOrchestraApp
from dev_orchestra.tui.forms import ConfirmScreen
from dev_orchestra.tui.monitor import MonitorScreen
from dev_orchestra.tui.screens import ProjectScreen

WIDE = (190, 52)
NOW = datetime.now(UTC)


def iso(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


def make(tmp_path, pid, delegator=True) -> Project:
    folder = tmp_path / pid
    folder.mkdir(exist_ok=True)
    roles = {r: Role(cli="claude") for r in ("orchestrator", "developer")}
    return Project(id=pid, name=pid.title(), path=folder, roles=roles, delegator=delegator)


def events(project):
    base = dict(project=project)
    return [  # do mais novo ao mais antigo, como o delegador entrega
        dict(id=5, ts=iso(5), kind="receive", text="orchestrator recebeu question #4", role_from="delegator",
             role_to="orchestrator", task_id="REFAC-001", state="asked", attempt=1, message_id=4,
             latency_ms=2000, **base),
        dict(id=4, ts=iso(8), kind="ask", text="pergunta #2: layout da grade?", role_from="developer",
             role_to="orchestrator", task_id="REFAC-001", state="asked", attempt=1, message_id=4,
             latency_ms=None, **base),
        dict(id=3, ts=iso(60), kind="refused", text="delegate negado: papel 'developer' não pode chamar delegate",
             role_from="developer", role_to="delegator", task_id=None, state=None, attempt=None, message_id=None,
             latency_ms=None, **base),
        dict(id=2, ts=iso(70), kind="delegate", text="haiku / low · refatorar", role_from="orchestrator",
             role_to="developer", task_id="REFAC-001", state="delegated", attempt=1, message_id=1,
             latency_ms=None, **base),
    ]  # fmt: skip


class Fake:
    def __init__(self) -> None:
        self.timelines = {
            "demo": events("demo"),
            "other": [e | {"id": e["id"] + 100, "project": "other"} for e in events("other")[:2]],
        }
        self.stats = {"sessions": 2, "alive": 2, "executing": 0, "open_questions": 1, "pending": 0,
                      "refusals_today": 1, "events_today": 4, "avg_latency_ms": 2400}  # fmt: skip

    def services(self) -> Services:
        return Services(
            claude_sessions=lambda: [],
            codex_threads=lambda u, c: None,
            workstreams=lambda n, c: [],
            ai_memory_projects=lambda: [],
            delegator_timeline=lambda url, project: self.timelines.get(project),
            delegator_stats=lambda url, project: self.stats,
            delegator_message=lambda url, project, mid: {
                "type": "question",
                "body": "QUESTION_ID: 2\nQUESTION: layout da grade?\nOPTIONS: a | b",
            },
            delegator_task=lambda url, project, task: [
                {"ts": iso(70), "kind": "delegate", "state": "delegated"},
                {"ts": iso(8), "kind": "ask", "state": "asked"},
            ],
        )


async def settle(app, pilot, times=2):
    for _ in range(times):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


async def open_monitor(app, pilot):
    await settle(app, pilot)
    await pilot.press("a")
    await settle(app, pilot, 3)
    assert isinstance(app.screen, MonitorScreen)
    return app.screen


def cell(table, key, column):
    return table.get_row(key)[column].plain


async def test_tecla_a_abre_o_monitor_com_a_linha_do_tempo(tmp_path):
    config.save_project(make(tmp_path, "demo"))
    config.save_project(make(tmp_path, "other"))
    app = DevOrchestraApp(Fake().services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        table = screen.query_one("#timeline", DataTable)
        assert table.row_count == 6  # 4 de demo + 2 de other
        assert cell(table, "demo:5", 2) == "↓ recebeu" and cell(table, "demo:4", 2) == "? pergunta"
        assert cell(table, "demo:4", 3) == "developer → orchestrator" and cell(table, "demo:4", 4) == "REFAC-001"
        assert cell(table, "demo:4", 5) == "● asked" and cell(table, "demo:5", 8) == "+2 s"
        assert "delegate negado" in cell(table, "demo:3", 7)
        stats = str(screen.query_one("#mon-stats", Static).render())
        assert "ao vivo" in stats and "no ar" in stats and "perguntas abertas 1" in stats and "2,4 s" in stats


async def test_coluna_de_projetos_e_troca_com_as_setas(tmp_path):
    config.save_project(make(tmp_path, "demo"))
    config.save_project(make(tmp_path, "other"))
    config.save_project(make(tmp_path, "plain", delegator=False))
    state.log_activity("plain", "Instruções enviadas", "ok")
    app = DevOrchestraApp(Fake().services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        pane = str(screen.query_one("#mon-projects", Static).render())
        assert "Todos os projetos" in pane and "DELEGADOR" in pane and "sessões 2/2" in pane
        assert "1 pergunta · 0 exec" in pane and "sem delegador" in pane and "só atividade da TUI" in pane
        await pilot.press("right")  # primeiro projeto (demo)
        await settle(app, pilot)
        table = screen.query_one("#timeline", DataTable)
        assert table.row_count == 4 and {e.project for e in screen.rows} == {"demo"}
        await pilot.press("right", "right")  # plain: só atividade da TUI
        await settle(app, pilot)
        assert [e.kind for e in screen.rows] == ["tui"]
        await pilot.press("right")  # volta a "todos" (circular)
        await settle(app, pilot)
        assert screen.selected == 0 and table.row_count == 7


async def test_filtros_por_clique_por_tecla_e_busca(tmp_path):
    config.save_project(make(tmp_path, "demo"))
    app = DevOrchestraApp(Fake().services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        table = screen.query_one("#timeline", DataTable)
        assert table.row_count == 4
        await pilot.press("f")  # preset "atenção": pergunta, recusado, cancelar
        await settle(app, pilot)
        assert [e.kind for e in screen.rows] == ["ask", "refused"]
        await pilot.press("f")  # preset "fluxo"
        await settle(app, pilot)
        assert {e.kind for e in screen.rows} == {"ask", "delegate"}
        await pilot.press("f")  # volta a "todos"
        await settle(app, pilot)
        assert table.row_count == 4
        start, end = screen._chip_ranges["receive"]
        await pilot.click("#mon-stats", offset=((start + end) // 2 + 1, 2))  # clicar no chip "recebeu" o desliga
        await settle(app, pilot)
        assert "receive" not in screen.kinds and table.row_count == 3
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press(*"grade", "enter")
        await settle(app, pilot)
        assert [e.kind for e in screen.rows] == ["ask"] and screen.text == "grade"


async def test_detalhe_mostra_a_mensagem_e_a_linha_do_tempo_da_tarefa(tmp_path):
    config.save_project(make(tmp_path, "demo"))
    app = DevOrchestraApp(Fake().services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        table = screen.query_one("#timeline", DataTable)
        table.move_cursor(row=1)  # a pergunta
        await settle(app, pilot, 3)
        detail = str(screen.query_one("#mon-detail", Static).render())
        assert "developer → orchestrator" in detail and "mensagem #4" in detail
        assert "PERGUNTA" in detail and "layout da grade?" in detail
        assert "● delegated" in detail and "● asked +62 s" in detail
        assert "nunca executado" in detail


async def test_pausar_congela_a_lista_e_continuar_atualiza(tmp_path):
    config.save_project(make(tmp_path, "demo"))
    fake = Fake()
    app = DevOrchestraApp(fake.services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        table = screen.query_one("#timeline", DataTable)
        await pilot.press("p")
        assert screen.paused and "pausado" in str(screen.query_one("#mon-stats", Static).render())
        fake.timelines["demo"] = [events("demo")[0] | {"id": 9, "text": "novo"}, *fake.timelines["demo"]]
        screen.reload()
        await settle(app, pilot)
        screen.paused = True  # o recarregar manual despausa; congela de novo para provar
        screen._render_table()
        assert table.row_count == 4
        await pilot.press("p")
        await settle(app, pilot, 3)
        assert table.row_count == 5 and not screen.paused


async def test_atualizacao_automatica_traz_evento_novo_e_mantem_a_selecao(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_screen, "REFRESH_SECONDS", 0.3)
    config.save_project(make(tmp_path, "demo"))
    fake = Fake()
    app = DevOrchestraApp(fake.services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        table = screen.query_one("#timeline", DataTable)
        table.move_cursor(row=2)
        await pilot.pause()
        selected = screen.current_entry().key
        fake.timelines["demo"] = [
            events("demo")[0] | {"id": 9, "kind": "report", "text": "DONE · pronto"},
            *fake.timelines["demo"],
        ]
        await pilot.pause(1.2)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert table.row_count == 5 and cell(table, "demo:9", 2) == "◀ resultado"
        assert screen.current_entry().key == selected  # quem está lendo uma linha não perde o lugar


async def test_ir_para_a_tarefa_e_copiar(tmp_path):
    p = make(tmp_path, "demo")
    config.save_project(p)
    tasks.create_task(p, "REFAC-001", "Refatoração", "developer")
    app = DevOrchestraApp(Fake().services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        screen.query_one("#timeline", DataTable).move_cursor(row=1)
        await pilot.pause()
        await pilot.press("c")
        await pilot.press("t")
        await settle(app, pilot, 3)
        assert isinstance(app.screen, ProjectScreen) and app.screen.tab == "tasks" and app.screen.project_id == "demo"


async def test_cancelar_a_tarefa_do_evento_pede_confirmacao(tmp_path, monkeypatch):
    config.save_project(make(tmp_path, "demo"))
    calls = []

    def fake_cancel(self, project, task, reason=""):
        calls.append((project, task))
        return {"task_id": task, "task_state": "cancelled"}

    monkeypatch.setattr(client_mod.DelegatorClient, "cancel", fake_cancel)
    app = DevOrchestraApp(Fake().services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        screen.query_one("#timeline", DataTable).move_cursor(row=1)
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await settle(app, pilot, 3)
        assert calls == [("demo", "REFAC-001")]
        assert "Delegação de REFAC-001 cancelada" in state.recent_activity("demo", 1)[0]["texto"]


async def test_cancelar_sem_tarefa_avisa(tmp_path):
    config.save_project(make(tmp_path, "demo"))
    app = DevOrchestraApp(Fake().services())
    async with app.run_test(size=WIDE) as pilot:
        screen = await open_monitor(app, pilot)
        screen.query_one("#timeline", DataTable).move_cursor(row=2)  # a recusa não tem tarefa
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)


async def test_menu_monitor_e_delegador_fora_do_ar(tmp_path):
    config.save_project(make(tmp_path, "demo"))
    svc = Fake().services()
    svc.delegator_timeline = lambda url, project: None  # serviço sem resposta
    svc.delegator_stats = lambda url, project: None
    app = DevOrchestraApp(svc)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("alt+m")
        await pilot.pause()
        labels = [str(o.prompt) for o in app.screen.query_one("#menu-list").options if o is not None]
        assert any("Abrir o monitor" in x for x in labels) and not any("Pausar" in x for x in labels)
        await pilot.press("enter")  # abre o monitor
        await settle(app, pilot, 3)
        assert isinstance(app.screen, MonitorScreen)
        assert "fora do ar" in str(app.screen.query_one("#mon-stats", Static).render())
        assert "delegador fora do ar" in str(app.screen.query_one("#mon-projects", Static).render())


def test_filtros_do_nucleo():
    e = lambda kind, **kw: mon.Entry("k", datetime.now().astimezone(), "p", kind, kw.pop("text", ""), **kw)  # noqa: E731
    entries = [e("ask", text="Layout", task="T-1"), e("report", text="ok", state="done"), e("tui")]
    assert len(mon.apply_filters(entries, None, set(mon.FILTER_ORDER))) == 3
    assert [x.kind for x in mon.apply_filters(entries, None, mon.PRESETS["atenção"])] == ["ask"]
    assert [x.kind for x in mon.apply_filters(entries, None, set(mon.FILTER_ORDER), "layout")] == ["ask"]
    assert [x.kind for x in mon.apply_filters(entries, None, set(mon.FILTER_ORDER), "DONE")] == ["report"]
    assert mon.apply_filters(entries, "outro", set(mon.FILTER_ORDER)) == []
    assert e("ask", latency_ms=300).latency == "+300 ms" and e("ask", latency_ms=2400).latency == "+2 s"
