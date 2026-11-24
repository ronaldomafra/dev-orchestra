"""TUI dos agentes passivos: cartões na tela inicial, formulário, tela do agente e remoção."""

from __future__ import annotations

import pytest
from textual.widgets import Checkbox, DataTable, Input, Static, TextArea

from dev_orchestra.core import config, passive
from dev_orchestra.core.liveness import Services
from dev_orchestra.tui import passive as tp
from dev_orchestra.tui.app import DevOrchestraApp

SIZE = (190, 60)


def servicos() -> Services:
    return Services(
        claude_sessions=lambda: [],
        codex_threads=lambda u, c: [],
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [],
    )


OVERVIEW = {
    "agents": {
        "analista": {
            "pending": 1,
            "running": {"consult_id": 8, "origin": "meu-bot", "since": "2026-11-15T12:00:00+00:00"},
            "today": {"consults": 5, "failed": 1, "cancelled": 0, "input_tokens": 1000, "output_tokens": 200,
                      "cache_read_tokens": 3000, "cost_usd": 0.42, "median_ms": 9000, "p90_ms": 20000},
            "week_cost_usd": 1.5,
            "last": {"consult_id": 7, "state": "done", "origin": "meu-bot", "finished_at": "2026-11-15T11:59:00+00:00",
                     "preview": "ENTRAR"},
        }
    },
    "runner": {"name": "h:1", "alive": True, "started_at": "2026-11-15T09:00:00+00:00", "agents": ["analista"]},
}  # fmt: skip

CONSULTS = [
    {"consult_id": 8, "agent": "analista", "origin": "meu-bot", "state": "running", "usage": None,
     "duration_ms": None, "created_at": "2026-11-15T12:00:00+00:00", "claimed_at": "2026-11-15T12:00:01+00:00",
     "finished_at": None, "preview": "SAIR PEPE?"},
    {"consult_id": 9, "agent": "analista", "origin": "meu-bot", "state": "pending", "usage": None,
     "duration_ms": None, "created_at": "2026-11-15T12:00:05+00:00", "claimed_at": None, "finished_at": None,
     "preview": "ENTRAR SUI?"},
    {"consult_id": 7, "agent": "analista", "origin": "meu-bot", "state": "done",
     "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.05}, "duration_ms": 12000,
     "created_at": "2026-11-15T11:58:00+00:00", "claimed_at": "2026-11-15T11:58:01+00:00",
     "finished_at": "2026-11-15T11:59:00+00:00", "preview": "ENTRAR"},
]  # fmt: skip


class FakeClient:
    def __init__(self) -> None:
        self.cancelled: list[int] = []

    def passive_overview(self, today=None, week=None):
        return OVERVIEW

    def passive_consults(self, agent=None, limit=30):
        return [c for c in CONSULTS if c["consult_id"] not in self.cancelled]

    def passive_timeline(self, agent, limit=40):
        return [{"ts": "2026-11-15T12:00:01+00:00", "state": "running", "text": "consulta #8: analista começou"}]

    def passive_consult(self, consult_id):
        base = next(c for c in CONSULTS if c["consult_id"] == consult_id)
        return {**base, "cli": "claude", "model": "haiku", "runner": "h:1", "instruction": f"pedido {consult_id}",
                "result": "resposta" if base["state"] == "done" else None, "error": None}  # fmt: skip

    def passive_cancel(self, consult_id):
        self.cancelled.append(consult_id)
        return {"consult_id": consult_id, "state": "cancelled"}


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tp, "client", lambda: client)
    monkeypatch.setattr(tp, "fetch_overview", lambda: OVERVIEW)
    return client


def texto(widget) -> str:
    return str(widget.render())


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_tela_inicial_sem_agentes_orienta_a_criar():
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert "tecle p para criar" in texto(app.screen.query_one("#passive-head", Static))
        assert list(app.screen.query(tp.PassiveCard)) == []


async def test_cartao_mostra_estado_fila_e_consumo(fake):
    passive.save(passive.PassiveAgent(id="analista", description="Analisa trades", model="haiku"), "p")
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        card = app.screen.query_one(tp.PassiveCard)
        body = texto(card)
        assert "Analisa trades" in body and "respondendo #8" in body and "fila 1" in body
        assert "5 consultas" in body and "US$ 0,42" in body
        assert card.has_class("p-busy")
        tile = texto(app.screen.query_one("#tile-passive"))
        assert "fila 2" in tile and "executor ● ativo" in tile
        card.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert isinstance(app.screen, tp.PassiveScreen)


async def test_formulario_cria_o_agente(fake):
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        form = app.screen
        assert isinstance(form, tp.PassiveFormScreen)
        form.query_one("#pa-id", Input).value = "revisor"
        form.query_one("#pa-desc", Input).value = "Revisa diffs"
        form.query_one("#pa-cmds", Input).value = "git diff, git log"
        form.query_one("#pa-web", Checkbox).value = True
        form.query_one("#pa-profile", TextArea).text = "# Agente passivo: revisor\nResponda em JSON."
        await settle(app, pilot)
        summary = texto(form.query_one("#pf-summary", Static))
        assert "Bash(git diff:*)" in summary and 'agent_id="revisor"' in summary
        assert "comandos liberados" in summary
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        agent = passive.load("revisor")
        assert agent.permissions.commands == ["git diff", "git log"] and agent.permissions.web
        assert passive.read_profile("revisor").startswith("# Agente passivo: revisor")
        assert isinstance(app.screen, tp.PassiveScreen)  # abre o agente recém-criado


async def test_formulario_recusa_id_repetido_e_ollama_sem_modelo(fake):
    passive.save(passive.PassiveAgent(id="analista"), "p")
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        app.push_screen(tp.PassiveFormScreen())
        await settle(app, pilot)
        form = app.screen
        form.query_one("#pa-id", Input).value = "analista"
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert "já existe" in texto(form.query_one("#f-error", Static))
        form.query_one("#pa-id", Input).value = "local"
        form.query_one("#pa-cli", tp.ChoiceChips).set_value("ollama")
        form.query_one("#pa-model", Input).value = ""
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert "modelo" in texto(form.query_one("#f-error", Static)) and not passive.exists("local")


async def test_tela_do_agente_mostra_consultas_detalhe_e_cancela(fake):
    passive.save(passive.PassiveAgent(id="analista", model="haiku"), "perfil")
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        app.push_screen(tp.PassiveScreen("analista"))
        await settle(app, pilot)
        screen = app.screen
        table = screen.query_one("#pv-table", DataTable)
        assert table.row_count == 3
        assert "respondendo #8" in texto(screen.query_one("#pv-state"))
        assert "US$ 0,42" in texto(screen.query_one("#pv-cost"))
        assert "pedido 8" in texto(screen.query_one("#pv-detail-body", Static))
        assert "analista começou" in texto(screen.query_one("#pv-events-body", Static))
        assert "haiku" in texto(screen.query_one("#pv-config", Static))
        table.move_cursor(row=1)  # a pendente (#9)
        await settle(app, pilot)
        await pilot.press("c")
        await settle(app, pilot)
        assert fake.cancelled == [9]
        table.move_cursor(row=0)  # a em execução não pode ser cancelada
        await settle(app, pilot)
        await pilot.press("c")
        assert fake.cancelled == [9]


async def test_remover_agente_pela_tela(fake):
    passive.save(passive.PassiveAgent(id="analista"), "perfil")
    app = DevOrchestraApp(servicos())
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        app.push_screen(tp.PassiveScreen("analista"))
        await settle(app, pilot)
        await pilot.press("D")
        await settle(app, pilot)
        assert isinstance(app.screen, tp.RemovePassiveScreen)
        await pilot.click("#rp-yes")
        await settle(app, pilot)
        assert not passive.exists("analista")
        assert not isinstance(app.screen, tp.PassiveScreen)


def test_formatacao():
    assert tp.num(950) == "950" and tp.num(9100) == "9,1 mil" and tp.num(281_000) == "281 mil"
    assert tp.money(0.0123) == "US$ 0,012" and tp.money(1.5) == "US$ 1,50"
    assert tp.secs(None) == "—" and tp.secs(3500) == "3,5 s" and tp.secs(12000) == "12 s"
    assert tp.tokens_of({"input_tokens": 1, "cache_read_tokens": 2, "output_tokens": 3}) == 6


def test_delegator_url_vem_da_configuracao():
    cfg = config.load_global()
    cfg.delegator_url = "http://127.0.0.1:9999"
    config.save_global(cfg)
    assert tp.delegator_url() == "http://127.0.0.1:9999"
