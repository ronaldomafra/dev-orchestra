#!/usr/bin/env python3
"""Exemplo: consultar um agente passivo do dev-orchestra pelo MCP do delegador (só biblioteca padrão).

Copie este arquivo para o sistema que vai consultar (por exemplo, um bot de trading). Uso:

    python consultar_agente_passivo.py analista-trade "Par SUI/USDT, RSI 41 -> 58, volume 1,6x. Entrar?"

Como biblioteca:

    from consultar_agente_passivo import consultar
    resposta = consultar("analista-trade", dados_da_transacao, origem="meu-bot", espera=300)
    if resposta["state"] == "done":
        decisao = json.loads(resposta["result"])   # o formato é o definido no perfil do agente

O delegador guarda o pedido numa fila; o executor (`dev-orchestra passive run`) roda o agente e devolve. Quem consulta só
informa o id do agente: qual CLI responde (Claude, Codex, Ollama...) fica a cargo da configuração do agente.
"""

from __future__ import annotations

import itertools
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8787/mcp"
_ids = itertools.count(1)


def _post(payload: dict, session: str | None = None, timeout: float = 15.0) -> tuple[dict, str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - delegador local
        body = response.read().decode()
        new_session = response.headers.get("Mcp-Session-Id")
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
            body = data[-1] if data else "{}"
    return (json.loads(body) if body.strip() else {}), new_session


def chamar(ferramenta: str, argumentos: dict) -> dict:
    """Chama uma ferramenta do delegador e devolve o resultado (levanta RuntimeError se ele recusar)."""
    init = {"jsonrpc": "2.0", "id": next(_ids), "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "exemplo", "version": "1"}}}  # fmt: skip
    _, session = _post(init)
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    call = {
        "jsonrpc": "2.0",
        "id": next(_ids),
        "method": "tools/call",
        "params": {"name": ferramenta, "arguments": argumentos},
    }
    reply, _ = _post(call, session)
    result = reply.get("result") or {}
    if "error" in reply or result.get("isError"):
        texto = " ".join(c.get("text", "") for c in result.get("content", []))
        raise RuntimeError(texto or str(reply.get("error")))
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured if structured is not None else json.loads(result["content"][0]["text"])


def consultar(agente: str, pedido: str, origem: str = "", espera: float = 600.0, intervalo: float = 2.0) -> dict:
    """Envia o pedido e espera a resposta (até `espera` segundos). Devolve o estado final da consulta."""
    enviado = chamar("consult", {"agent_id": agente, "instruction": pedido, "origin": origem})
    limite = time.monotonic() + espera
    while True:
        estado = chamar("consult_result", {"consult_id": enviado["consult_id"]})
        if estado["state"] in ("done", "failed", "cancelled") or time.monotonic() > limite:
            return estado
        time.sleep(intervalo)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    final = consultar(sys.argv[1], " ".join(sys.argv[2:]), origem="exemplo")
    print(final.get("result") or final.get("error") or final["state"])
    raise SystemExit(0 if final["state"] == "done" else 1)
