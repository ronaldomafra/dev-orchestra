"""Cliente MCP mínimo (Streamable HTTP, JSON-RPC) só com a biblioteca padrão.

Serve para chamar as ferramentas do delegador como qualquer consumidor faria (por exemplo, `consult` e
`consult_result` dos agentes passivos), sem depender do pacote `mcp` no host.
"""

from __future__ import annotations

import itertools
import json
import time
import urllib.error
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
_ids = itertools.count(1)


class McpError(Exception):
    """A ferramenta recusou (erro do delegador) ou o serviço não respondeu."""


def _post(
    url: str, payload: dict[str, Any], timeout: float, session: str | None = None
) -> tuple[dict[str, Any], str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - delegador local
            body = response.read().decode("utf-8", errors="replace")
            new_session = response.headers.get("Mcp-Session-Id")
            kind = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise McpError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise McpError(f"delegador indisponível em {url}: {exc}") from exc
    if not body.strip():
        return {}, new_session
    if "text/event-stream" in kind:  # SSE: pega o último `data:` com JSON
        data_lines = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        body = data_lines[-1] if data_lines else "{}"
    try:
        return json.loads(body), new_session
    except ValueError as exc:
        raise McpError(f"resposta inválida do MCP: {body[:200]}") from exc


def endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/mcp") else base + "/mcp"


def call_tool(base_url: str, name: str, arguments: dict[str, Any], *, timeout: float = 15.0) -> Any:
    """Chama uma ferramenta e devolve o resultado estruturado (ou o texto, se não houver)."""
    url = endpoint(base_url)
    init = {
        "jsonrpc": "2.0", "id": next(_ids), "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "dev-orchestra", "version": "0.1.0"}},
    }  # fmt: skip
    _, session = _post(url, init, timeout)
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout, session)
    reply, _ = _post(
        url,
        {"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        timeout,
        session,
    )
    if "error" in reply:
        raise McpError(str(reply["error"].get("message") or reply["error"]))
    result = reply.get("result") or {}
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    if result.get("isError"):
        raise McpError(" ".join(texts).strip() or "a ferramenta recusou")
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]  # o FastMCP embrulha listas e textos em {"result": ...}
    if structured is not None:
        return structured
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except ValueError:
        return joined


FINAL_STATES = ("done", "failed", "cancelled")


def consult(base_url: str, agent_id: str, instruction: str, origin: str = "") -> dict[str, Any]:
    return call_tool(base_url, "consult", {"agent_id": agent_id, "instruction": instruction, "origin": origin})


def consult_result(base_url: str, consult_id: int) -> dict[str, Any]:
    return call_tool(base_url, "consult_result", {"consult_id": consult_id})


def wait_result(
    base_url: str,
    consult_id: int,
    *,
    timeout: float = 600.0,
    interval: float = 2.0,
    sleep=time.sleep,
    clock=time.monotonic,
) -> dict[str, Any]:
    """Consulta o estado até ficar pronto (ou o tempo acabar: devolve o último estado visto)."""
    deadline = clock() + timeout
    while True:
        state = consult_result(base_url, consult_id)
        if state.get("state") in FINAL_STATES or clock() >= deadline:
            return state
        sleep(interval)
