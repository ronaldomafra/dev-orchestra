"""Agentes passivos no delegador: fila, executor, ferramentas MCP de ponta a ponta e linha de comando."""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn

from dev_orchestra.cli import main
from dev_orchestra.core import mcp_http, passive
from dev_orchestra.core.passive_runner import PassiveRunner
from dev_orchestra.delegator.consults import Consults
from dev_orchestra.delegator.core import Delegator
from dev_orchestra.delegator.db import Store
from dev_orchestra.delegator.errors import DelegatorError
from dev_orchestra.delegator.server import build_app

AGENTS = {"analista": {"id": "analista", "cli": "claude", "model": "haiku", "effort": None, "description": "Analisa"}}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def consults(tmp_path, clock):
    return Consults(Store(tmp_path / "estado.db"), clock, lambda: dict(AGENTS))


# ---------------------------------------------------------------- regras


def test_consulta_fila_execucao_e_resultado(consults, clock):
    first = consults.consult("analista", "analise A", "meu-bot")
    assert first["consult_id"] == 1 and first["ahead_in_queue"] == 0 and "note" in first  # sem executor
    second = consults.consult("analista", "analise B", "meu-bot")
    assert second["ahead_in_queue"] == 1
    assert [c["consult_id"] for c in consults.pending()] == [1, 2]
    consults.claim(1, "host:1")
    with pytest.raises(DelegatorError) as busy:
        consults.claim(2, "host:1")  # um por vez por agente
    assert busy.value.code == "agent_busy"
    clock.advance(seconds=12)
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 100, "cost_usd": 0.02}
    consults.finish(1, "done", result='{"decisao": "ENTRAR"}', usage=usage, duration_ms=12000, model="claude-haiku")
    public = consults.result(1)
    assert public["state"] == "done" and public["result"] == '{"decisao": "ENTRAR"}' and public["usage"] == usage
    assert "cli" not in public and "model" not in public and "instruction" not in public  # quem consulta não vê a CLI
    full = consults.get(1)
    assert full["cli"] == "claude" and full["model"] == "claude-haiku" and full["instruction"] == "analise A"
    with pytest.raises(DelegatorError):
        consults.finish(1, "done")  # já terminou


def test_consulta_recusada(consults):
    with pytest.raises(DelegatorError) as unknown:
        consults.consult("nao-existe", "x")
    assert "known: analista" in str(unknown.value)
    with pytest.raises(DelegatorError):
        consults.consult("analista", "   ")
    with pytest.raises(DelegatorError):
        consults.consult("analista", "x" * 60_001)


def test_cancelar_so_pendente_e_requeue(consults, clock):
    consults.consult("analista", "a")
    consults.consult("analista", "b")
    consults.cancel(2)
    assert consults.get(2)["state"] == "cancelled"
    consults.claim(1)
    with pytest.raises(DelegatorError):
        consults.cancel(1)
    clock.advance(minutes=31)
    assert consults.requeue_stale(30) == 1 and consults.get(1)["state"] == "pending"


def test_executor_vivo_e_list_agents(consults, clock):
    assert consults.list_agents() == [{"id": "analista", "description": "Analisa", "runner_alive": False}]
    consults.heartbeat("host:1", 42, ["analista"])
    assert consults.list_agents()[0]["runner_alive"] is True
    assert "note" not in consults.consult("analista", "x")
    clock.advance(seconds=31)
    assert consults.runner()["alive"] is False


def test_painel_do_dia(consults, clock):
    for n in range(3):
        consults.consult("analista", f"p{n}", "meu-bot")
    consults.claim(1)
    consults.finish(1, "done", result="r", usage={"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.5},
                    duration_ms=2000)  # fmt: skip
    consults.claim(2)
    consults.finish(2, "failed", error="tempo limite")
    consults.claim(3)
    view = consults.overview(today=(clock.now - timedelta(hours=1)).isoformat())
    item = view["agents"]["analista"]
    assert item["running"]["consult_id"] == 3 and item["pending"] == 0
    day = item["today"]
    assert (day["consults"], day["failed"], day["input_tokens"], day["output_tokens"]) == (3, 1, 100, 10)
    assert day["cost_usd"] == 0.5 and day["median_ms"] == 2000 and item["week_cost_usd"] == 0.5
    assert item["last"]["consult_id"] == 2 and item["last"]["state"] == "failed"
    events = consults.timeline("analista")
    assert events[0]["state"] == "running" and any("falhou" in e["text"] for e in events)
    assert [c["consult_id"] for c in consults.recent(agent="analista")] == [3, 2, 1]


# ---------------------------------------------------------------- executor


class FakeClient:
    """O executor fala com o delegador por HTTP; aqui, direto com as regras."""

    def __init__(self, consults: Consults) -> None:
        self.c = consults

    def passive_heartbeat(self, name, pid, agents):
        return self.c.heartbeat(name, pid, agents)

    def passive_pending(self):
        return self.c.pending()

    def passive_claim(self, consult_id, runner=""):
        try:
            return self.c.claim(consult_id, runner)
        except DelegatorError as exc:
            raise RuntimeError(str(exc)) from exc

    def passive_finish(self, consult_id, state, **fields):
        return self.c.finish(consult_id, state, **fields)

    def passive_requeue(self, minutes=30.0):
        return self.c.requeue_stale(minutes)


def test_executor_roda_em_fila_e_devolve(consults, capsys):
    consults.consult("analista", "um")
    consults.consult("analista", "dois")
    agents = [passive.PassiveAgent(id="analista")]
    seen = []

    def execute(agent, prompt):
        seen.append(prompt)
        return passive.Outcome(True, result=f"ok {prompt}", usage={"input_tokens": 3}, duration_ms=5)

    runner = PassiveRunner(FakeClient(consults), name="t", load_agents=lambda: agents, execute=execute)
    assert runner.tick(wait=True) == 1  # um por vez por agente
    assert runner.tick(wait=True) == 1
    assert seen == ["um", "dois"]
    assert consults.get(1)["result"] == "ok um" and consults.get(2)["state"] == "done"
    assert consults.runner()["name"] == "t"


def test_executor_falha_agente_removido_e_excecao(consults):
    consults.consult("analista", "x")
    runner = PassiveRunner(FakeClient(consults), name="t", load_agents=lambda: [])
    runner.tick(wait=True)
    assert consults.get(1)["state"] == "failed" and "não existe mais" in consults.get(1)["error"]

    consults.consult("analista", "y")

    def boom(agent, prompt):
        raise ValueError("quebrou")

    runner = PassiveRunner(
        FakeClient(consults), name="t", load_agents=lambda: [passive.PassiveAgent(id="analista")], execute=boom
    )
    runner.tick(wait=True)
    assert consults.get(2)["state"] == "failed" and "quebrou" in consults.get(2)["error"]


# --------------------------------------------------------- MCP de ponta a ponta


class Registry:
    def get(self, project_id):
        return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = free_port()
    delegator = Delegator(Store(tmp_path / "d.db"), Registry(), passive_agents=lambda: dict(AGENTS))
    app = build_app(delegator, host="127.0.0.1", port=port).streamable_http_app()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", delegator
    srv.should_exit = True
    thread.join(timeout=5)


def test_mcp_consulta_e_resultado_com_executor(server):
    url, delegator = server
    from dev_orchestra.delegator.client import DelegatorClient

    assert mcp_http.call_tool(url, "list_agents", {}) == [
        {"id": "analista", "description": "Analisa", "runner_alive": False}
    ]
    sent = mcp_http.consult(url, "analista", "dados da transação", "meu-bot")
    assert sent["consult_id"] == 1 and sent["state"] == "pending"
    with pytest.raises(mcp_http.McpError, match="unknown agent"):
        mcp_http.consult(url, "outro", "x")
    client = DelegatorClient(url)
    runner = PassiveRunner(
        client,
        name="t",
        load_agents=lambda: [passive.PassiveAgent(id="analista")],
        execute=lambda a, p: passive.Outcome(True, result='{"decisao":"SAIR"}', usage={"input_tokens": 1}),
    )
    runner.tick(wait=True)
    final = mcp_http.wait_result(url, 1, timeout=5, interval=0.1)
    assert final["state"] == "done" and json.loads(final["result"]) == {"decisao": "SAIR"}
    assert "cli" not in final
    overview = client.passive_overview()
    assert overview["agents"]["analista"]["last"]["consult_id"] == 1 and overview["runner"]["alive"]
    assert client.passive_consult(1)["cli"] == "claude"
    assert client.passive_timeline("analista")[0]["state"] == "done"


# ------------------------------------------------------------ linha de comando


def run_cli(*args) -> tuple[int, str]:
    import io

    out = io.StringIO()
    code = main(list(args), out=out)
    return code, out.getvalue()


def test_cli_add_show_list_remove():
    code, out = run_cli("passive", "add", "revisor", "--cli", "claude", "-m", "haiku", "-d", "Revisa diffs",
                        "--allow-command", "git diff,git log", "--dir", "/tmp")  # fmt: skip
    assert code == 0 and "salvo" in out
    agent = passive.load("revisor")
    assert agent.permissions.commands == ["git diff", "git log"] and agent.permissions.read_dirs == ["/tmp"]
    assert "## Formato de resposta" in passive.read_profile("revisor")  # modelo de perfil para editar
    assert run_cli("passive", "add", "revisor")[0] == 2  # já existe
    code, out = run_cli("passive", "show", "revisor")
    assert "Bash(git diff:*)" in out and "--resume" in out
    code, out = run_cli("passive", "list")
    assert "revisor" in out and "comandos: 2" in out
    code, out = run_cli("passive", "remove", "revisor")
    assert "nada foi alterado" in out and passive.exists("revisor")
    code, out = run_cli("passive", "remove", "revisor", "--yes")
    assert code == 0 and not passive.exists("revisor")


def test_cli_exige_modelo_no_ollama_e_comando():
    assert run_cli("passive", "add", "local", "--cli", "ollama")[0] == 2
    assert run_cli("passive", "add", "meu", "--cli", "comando")[0] == 2


def test_cli_send_pelo_mcp(server, monkeypatch):
    url, delegator = server
    delegator.consults.heartbeat("t", 1, ["analista"])
    sent = {}

    def fake_wait(base, cid, timeout=0):
        sent["cid"] = cid
        return {"state": "done", "result": "resposta final", "usage": {"input_tokens": 1}, "duration_ms": 10}

    monkeypatch.setattr(mcp_http, "wait_result", fake_wait)
    code, out = run_cli("passive", "send", "analista", "olá", "--url", url, "--origin", "teste")
    assert code == 0 and out.strip() == "resposta final" and sent["cid"] == 1
    assert delegator.consults.get(1)["origin"] == "teste"
    code, out = run_cli("passive", "send", "analista", "x", "--url", url, "--no-wait", "--json")
    assert json.loads(out)["consult_id"] == 2
