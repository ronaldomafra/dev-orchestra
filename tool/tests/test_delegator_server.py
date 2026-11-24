"""Servidor MCP do delegador de ponta a ponta (HTTP real, cliente MCP oficial) e API do host."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from dev_orchestra.core.models import Project, Role
from dev_orchestra.delegator.client import DelegatorClient, DelegatorUnavailable
from dev_orchestra.delegator.core import Delegator
from dev_orchestra.delegator.db import Store
from dev_orchestra.delegator.server import build_app


class Registry:
    def __init__(self, tmp_path) -> None:
        roles = {r: Role(cli="claude") for r in ("orchestrator", "developer")}
        self.project = Project(id="demo", name="Demo", path=tmp_path, roles=roles)

    def get(self, project_id):
        return self.project if project_id == "demo" else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = free_port()
    delegator = Delegator(Store(tmp_path / "estado.db"), Registry(tmp_path))
    app = build_app(delegator, host="127.0.0.1", port=port).streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", delegator
    srv.should_exit = True
    thread.join(timeout=5)


async def call(url: str, tool: str, **args):
    async with streamablehttp_client(f"{url}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            return result


def structured(result) -> dict:
    assert not result.isError, result.content
    if result.structuredContent is not None:
        return result.structuredContent.get("result", result.structuredContent)
    import json

    return json.loads(result.content[0].text)


async def test_lista_as_ferramentas_esperadas(server):
    url, _ = server
    async with streamablehttp_client(f"{url}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
    assert names == {
        "register_session", "heartbeat", "delegate", "receive", "report", "ask", "answer", "cancel_task",
        "project_status", "list_agents", "consult", "consult_result",
    }  # fmt: skip


async def test_fluxo_completo_pelo_mcp(server):
    url, _ = server
    orch = structured(
        await call(url, "register_session", project="demo", role="orchestrator", cli="claude", session_ref="o")
    )
    dev = structured(
        await call(url, "register_session", project="demo", role="developer", cli="claude", session_ref="d")
    )
    assert "Developer" in dev["profile"]
    sent = structured(await call(
        url, "delegate", session_id=orch["session_id"], task_id="T-1", to_role="developer", instruction="faça X",
        model="haiku", effort="low", model_reason="simples", user_approved=True,
    ))  # fmt: skip
    assert sent["task_state"] == "delegated"
    got = structured(await call(url, "receive", session_id=dev["session_id"]))
    assert got["message"]["type"] == "delegate" and "faça X" in got["message"]["body"]
    done = structured(await call(url, "report", session_id=dev["session_id"], task_id="T-1", status="DONE",
                                 summary="ok", evidence="arquivo"))  # fmt: skip
    assert done["accepted"]
    back = structured(await call(url, "receive", session_id=orch["session_id"]))
    assert back["message"]["type"] == "result"
    status = (await call(url, "project_status", session_id=orch["session_id"])).content[0].text
    assert "T-1: done" in status


async def test_permissao_negada_vira_erro_da_ferramenta(server):
    url, _ = server
    dev = structured(
        await call(url, "register_session", project="demo", role="developer", cli="claude", session_ref="d")
    )
    result = await call(url, "delegate", session_id=dev["session_id"], task_id="T", to_role="developer",
                        instruction="x", model="m", effort="e", user_approved=True)  # fmt: skip
    assert result.isError and "not allowed to call delegate" in result.content[0].text


async def test_registro_com_cli_errada_e_recusado(server):
    url, _ = server
    result = await call(url, "register_session", project="demo", role="developer", cli="codex", session_ref="d")
    assert result.isError and "configured for cli" in result.content[0].text


# ------------------------------------------------------ API do host (listener/TUI)


async def test_api_do_host(server):
    url, delegator = server
    client = DelegatorClient(url)
    assert client.health() is True
    orch = delegator.register_session("demo", "orchestrator", "claude", "demo-orchestrator")["session_id"]
    delegator.register_session("demo", "developer", "claude", "demo-developer")
    delegator.delegate(orch, "T-9", "developer", "x", "haiku", "low", "r", True)
    pending = client.pending("demo", "demo-developer")
    assert pending["count"] == 1 and pending["types"] == ["delegate"] and "body" not in pending
    status = client.status("demo")
    assert status["tasks"][0]["task_id"] == "T-9" and len(status["sessions"]) == 2
    assert [e["kind"] for e in client.events("demo")][0] == "delegate"
    assert client.cancel("demo", "T-9", "teste")["task_state"] == "cancelled"
    with pytest.raises(RuntimeError, match="only delegated, running or asked"):
        client.cancel("demo", "T-9")
    assert client.push_profile("demo", "developer")["sessions"]
    with pytest.raises(RuntimeError, match="unknown project"):
        client.status("nada")


def test_cliente_sem_servidor():
    client = DelegatorClient(f"http://127.0.0.1:{free_port()}", timeout=0.5)
    assert client.health() is False
    with pytest.raises(DelegatorUnavailable):
        client.status("demo")
