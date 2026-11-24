"""Erros do delegador (mensagens curtas, em inglês: aparecem como erro da ferramenta MCP para o agente)."""

from __future__ import annotations


class DelegatorError(Exception):
    """Pedido recusado pelo delegador (permissão, estado ou dados inválidos)."""

    def __init__(self, message: str, code: str = "rejected") -> None:
        super().__init__(message)
        self.code = code
