# Modelo de handoff

Use este modelo para delegar uma tarefa. Remova os campos sem uso e mantenha a mensagem curta.

## Mensagem mínima

    TASK: <ID>
    INSTRUCTION: <objetivo, escopo, critérios e restrições>
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
- **Evidências esperadas:**
- **Sessão de retorno:** Orchestrator

## Retorno

Instrua o worker a responder pelo mesmo canal usando templates/result.md. No Codex, a comunicação usa codex queue. O worker deve devolver o resultado sem depender de o usuário copiar mensagens.
