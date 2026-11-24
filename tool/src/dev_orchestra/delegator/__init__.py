"""Delegador: serviço que guarda, confere e entrega mensagens entre as sessões de um projeto.

Não é um agente. Aplica a tabela de permissões por papel, mantém o estado de execução das tarefas em SQLite
e acorda as sessões ociosas sem levar conteúdo (o conteúdo só sai por `receive`).
"""

DEFAULT_PORT = 8787
DEFAULT_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
MCP_NAME = "dev-orchestra"
