# Protocolo entre agentes

## Objetivo

Padronizar a troca de trabalho entre Orchestrator e workers sem transportar contexto excessivo.

O protocolo define **o conteúdo** das mensagens. O transporte depende da CLI utilizada.

No Codex, o POC validado usa:

- `codex app-server`;
- `codex queue`;
- uma sessão por papel conectada ao mesmo App Server.

O Task Source não é canal de comunicação entre agentes.

## Handoff

Toda delegação deve conter no mínimo:

- Task ID;
- objetivo;
- escopo;
- critérios de aceite;
- restrições;
- evidências esperadas;
- referências necessárias;
- sessão para retorno.

Use `templates/handoff.md`.

No Codex, o Orchestrator envia o handoff ao worker usando `codex queue`.

## Resultado

Todo worker deve responder ao Orchestrator pelo mesmo canal de comunicação usado para receber a tarefa.

O resultado deve conter:

- status;
- Task ID;
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

## Comportamento assíncrono

Depois de delegar uma tarefa, o Orchestrator:

- não executa a tarefa delegada;
- não verifica arquivos para descobrir se o worker terminou;
- não faz polling da sessão do worker;
- encerra seu turno e aguarda uma mensagem de retorno.

Quando o worker termina, ele envia o resultado ao Orchestrator pelo canal de comunicação entre sessões.

No Codex:

```text
Orchestrator
    |
    | codex queue
    v
Developer
    |
    | execução
    | codex queue
    v
Orchestrator
```

## Resolução de sessão no Codex

Prefira nomes de sessão específicos, por exemplo:

```text
orchestrator-poc
developer-poc
qa-poc
```

Se `codex queue` não conseguir confirmar a unicidade do nome, a sessão emissora deve usar o UUID informado pelo próprio Codex e repetir o envio.

O usuário não deve precisar copiar UUIDs entre sessões.

Se houver múltiplas sessões realmente candidatas e não for possível determinar a correta, não escolher aleatoriamente.

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
4. evite continuar inventando requisitos;
5. envie o status `BLOCKED` ao Orchestrator pelo canal de comunicação.

### Memória sugerida

Workers podem sugerir fatos para memória durável.

O Orchestrator ou uma política futura decide se a informação deve realmente ser persistida.

## Exemplo

```text
Orchestrator
  -> Developer: TSK-42 via canal de comunicação

Developer
  -> Orchestrator: DONE + arquivos + testes + risco

Orchestrator
  -> QA: validar TSK-42 via canal de comunicação

QA
  -> Orchestrator: resultado + evidências

Orchestrator
  -> Task Source: atualizar status
  -> AI Memory: persistir decisão durável, se houver
```

Para reproduzir o POC Codex passo a passo, consulte [CODEX-QUEUE-POC.md](CODEX-QUEUE-POC.md).
