# Modelo de handoff

Use este modelo para delegar uma tarefa. Remova os campos sem uso e mantenha a mensagem curta.

## Mensagem mínima

    TASK: <ID>
    INSTRUCTION: <objetivo, escopo, critérios e restrições>
    MODEL: <modelo aprovado>
    EFFORT: <esforço aprovado>
    MODEL_REASON: <justificativa curta>
    RETURN_TO: <nome exato ou UUID confirmado da sessão Orchestrator>

## Detalhes da tarefa

- **Título:**
- **Fonte:** Task Source ou solicitação explícita
- **Objetivo:**
- **Escopo:**
- **Fora do escopo:**
- **Critérios de aceite:**
  - [ ]
- **Restrições:**
- **Referências necessárias:**
- **Validação esperada:** comandos/testes
- **Evidências esperadas:**
- **Sessão de retorno:** Orchestrator

## Retorno

Instrua o worker a responder pelo mesmo transporte usando templates/result.md: `codex queue` no Codex ou SendMessage no Claude Code. O worker deve devolver o resultado sem depender de o usuário copiar mensagens. O handoff deve ser autossuficiente: o worker não vê a conversa do Orchestrator.
