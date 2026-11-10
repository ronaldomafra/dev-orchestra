# Modelo de resultado

Use este modelo para devolver o resultado ao Orchestrator pelo mesmo transporte usado para receber a tarefa. Mantenha STATUS, TASK, SUMMARY e EVIDENCE mesmo em uma resposta curta.

## Formato mínimo entre sessões

    STATUS: DONE | PARTIAL | BLOCKED | FAILED | NEEDS_REVIEW
    TASK: <ID>
    SUMMARY: <resumo curto>
    EVIDENCE: <resultado verificável, incluindo se nenhum arquivo foi alterado>

## Detalhes opcionais

- **Mudanças:** arquivos e mudanças realizadas.
- **Validação:** comandos/testes executados e resultado.
- **Riscos / pendências:** riscos ou trabalho pendente.
- **Memória candidata:** conhecimento durável que talvez mereça registro.
- **Próximo passo recomendado:** próxima ação.
- **Git:** branch e commit, quando aplicável.

## Entrega

No Codex, envie o resultado ao Orchestrator via `codex queue`; uma resposta aceita pelo App Server confirma o enfileiramento. No Claude Code, envie com SendMessage ao nome da sessão do Orchestrator. Em ambos, o resultado deve chegar à sessão de destino; não dependa do usuário para transportar a mensagem. Depois de enviar, encerre o turno.
