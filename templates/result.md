# Modelo de resultado

Use este modelo para devolver o resultado ao Orchestrator pelo mesmo canal usado para receber a tarefa. Mantenha STATUS, TASK, SUMMARY e EVIDENCE mesmo em uma resposta curta.

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

No Codex, envie o resultado ao Orchestrator via codex queue. Uma resposta aceita pelo App Server confirma o enfileiramento; o resultado deve chegar à sessão de destino. Não dependa do usuário para transportar a mensagem.
