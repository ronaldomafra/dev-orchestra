# Validação do modo Claude Code

Registro do que foi verificado no modo Claude Code e do que ainda falta. Atualize este arquivo a cada teste, com data, versões e evidências.

Versões consultadas: Claude Code `2.1.322`, AI Memory `3.4.3`.

## Evidências registradas

| Data | Verificação | Resultado |
| --- | --- | --- |
| 2026-11-06 | Abrir o Claude Code com `ai-memory run --workstream <workstream criado no Codex> claude` | A sessão recebeu `AI_MEMORY_WORKSTREAM_ID` e `AI_MEMORY_RUN_ID` e apresentou o handoff pendente da sessão Codex anterior |
| 2026-11-06 | Sessão aberta com `claude` direto | Sem variáveis `AI_MEMORY_*` e sem vínculo `claude` no workstream; confirma que o vínculo depende do `ai-memory run` |
| 2026-11-06 | Projeto deduzido pelo remote Git | Erro 404 de projeto inexistente; resolvido com `--project` |
| 2026-11 | Adoção em projeto privado | `AGENTS.md`, papéis em `.claude/agents/` e regra de aprovação de modelo configurados |

## Pendente

| ID | Teste | Critério de sucesso |
| --- | --- | --- |
| CC-001 | Ping-pong por SendMessage entre `orchestrator` e `developer` | O Developer recebe `PING`, responde `PONG` com SendMessage e o Orchestrator recebe como novo turno, sem alteração de arquivos |
| CC-002 | Retorno com o Orchestrator em *working* | O retorno chega quando o turno do Orchestrator termina |
| CC-003 | Retomada gerenciada | `ai-memory run --workstream developer claude ...` reabre a mesma sessão Claude; um marcador pedido antes do encerramento é recuperado |
| CC-004 | Memória durável | Decisão gravada pelo MCP em uma sessão e recuperada por consulta MCP em outra |
| CC-005 | Fluxo completo DEMO-001 | Card lido, modelo aprovado, handoff entregue, resultado recebido e Trello atualizado |

## Roteiro do CC-001

1. Abra as duas sessões no mesmo checkout e no mesmo modo de permissão:

   ```bash
   ai-memory run --workstream orchestrator claude --name orchestrator
   ai-memory run --workstream developer claude --agent developer --name developer
   ```

2. Na sessão Developer, confira o próprio nome com `/list-agents`.
3. Na sessão Orchestrator, envie:

   ```text
   Teste de transporte, sem tarefa real. Use SendMessage para enviar a "developer":
   "PING CC-001. Não edite arquivos. Responda com SendMessage para orchestrator:
   PONG CC-001 <seu AI_MEMORY_WORKSTREAM_ID>. Depois encerre o turno."
   Depois de enviar, encerre seu turno sem esperar.
   ```

4. Resultado esperado: o Orchestrator recebe `PONG CC-001 <id>` como novo turno e `git status` não mostra alterações.

Registre o resultado na tabela de evidências, com data e qualquer diferença observada.
