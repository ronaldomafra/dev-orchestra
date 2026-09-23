# Protocolo entre agentes

## Objetivo

Padronizar a troca de trabalho entre Orchestrator e workers sem transportar contexto excessivo.

## Handoff

Toda delegação deve conter no mínimo:

- Task ID;
- objetivo;
- escopo;
- critérios de aceite;
- restrições;
- evidências esperadas;
- referências necessárias.

Use `templates/handoff.md`.

## Resultado

Todo worker deve responder com:

- status;
- resumo;
- mudanças realizadas;
- validações executadas;
- evidências;
- riscos ou pendências;
- memória sugerida, se houver;
- recomendação do próximo passo.

Use `templates/result.md`.

## Status do protocolo

Valores sugeridos:

- `DONE`
- `PARTIAL`
- `BLOCKED`
- `FAILED`
- `NEEDS_REVIEW`

## Regras

### Contexto mínimo

Não envie o histórico inteiro da sessão para outro agente.

Envie somente:

- objetivo;
- arquivos ou áreas relevantes;
- decisões já tomadas;
- critérios;
- restrições;
- links necessários.

### Evidência

Um resultado não deve ser apenas “feito”.

Sempre que aplicável, inclua:

- testes executados;
- arquivos modificados;
- commit/branch;
- reprodução do bug;
- saída relevante;
- screenshot;
- link de PR.

### Bloqueios

Ao bloquear:

1. descreva o bloqueio;
2. diga o que já foi tentado;
3. diga qual informação ou ação destrava;
4. evite continuar inventando requisitos.

### Memória sugerida

Workers podem sugerir fatos para memória durável.

O Orchestrator ou uma política futura decide se a informação deve realmente ser persistida.

## Exemplo

```text
Orchestrator
  -> Dev: implementar card TSK-42
Dev
  -> Orchestrator: DONE + arquivos + testes + risco
Orchestrator
  -> QA: validar TSK-42 com base no resultado
QA
  -> Orchestrator: DONE + evidências
Orchestrator
  -> Task Source: atualizar status
  -> AI Memory: persistir decisão durável, se houver
```
