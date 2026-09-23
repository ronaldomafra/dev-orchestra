# Workflow operacional

## Objetivo

Começar manualmente, com sessões visíveis, antes de automatizar.

## Sessões iniciais

Abra três terminais:

1. Orchestrator
2. Developer
3. QA

Opcionalmente abra um quarto:

4. Planner

Cada terminal deve:

- estar no mesmo projeto;
- carregar `AGENTS.md`;
- carregar seu arquivo em `roles/`;
- ser iniciado através do AI Memory;
- trabalhar em branch/worktree compatível com seu papel quando necessário.

Como o AI Memory permite um escritor ativo por workstream, use nomes simples para as sessões paralelas:

- `orchestrator`
- `developer`
- `qa`

Isso é apenas separação operacional das sessões; não cria uma nova camada de arquitetura.

## Trello / Task Source

No fluxo inicial, o **Orchestrator é o responsável por manter o Task Source sincronizado com o trabalho real**.

Com Trello, isso inclui:

- consultar o backlog;
- selecionar cards;
- mover o card para execução;
- mover para teste/validação quando a implementação terminar;
- devolver para execução quando QA encontrar falha;
- mover para concluído quando houver evidência suficiente;
- criar ou atualizar cards quando isso fizer parte do trabalho de coordenação.

Developer e QA não precisam administrar o quadro. Eles recebem a tarefa, executam seu papel e devolvem resultado e evidências ao Orchestrator.

Isso evita múltiplas sessões alterando o mesmo backlog ao mesmo tempo.

## Ciclo básico

### 1. Seleção

Orchestrator consulta o Task Source e escolhe uma tarefa elegível.

### 2. Início

Orchestrator move a tarefa para o estado de execução apropriado.

### 3. Contextualização

Orchestrator consulta:

- card/tarefa;
- memória relevante;
- documentação;
- dependências.

### 4. Handoff

Orchestrator envia uma mensagem seguindo `templates/handoff.md`.

### 5. Execução

Developer trabalha no escopo recebido.

### 6. Retorno

Developer responde com `templates/result.md`.

### 7. Validação

Orchestrator move a tarefa para teste/validação e envia os critérios e evidências para QA.

QA valida a implementação e devolve o resultado.

### 8. Consolidação

Se QA aprovar, Orchestrator:

- consolida o resultado;
- move a tarefa para concluído;
- registra links relevantes;
- promove conhecimento durável para AI Memory quando necessário.

Se QA encontrar problema, Orchestrator devolve a tarefa para execução com a evidência recebida.

## Regra de contexto

O Orchestrator não deve absorver logs e diffs completos se um resumo verificável for suficiente.

Workers devem devolver síntese + evidência.

## Fluxo de bug

```text
Backlog
  -> Orchestrator move para execução
  -> Developer reproduz/corrige
  -> Orchestrator move para teste
  -> QA valida reprodução e regressão
  -> Orchestrator atualiza o Task Source
```

## Fluxo de feature

```text
Backlog
  -> Orchestrator move para execução
  -> Planner (quando necessário)
  -> Developer
  -> Orchestrator move para teste
  -> QA
  -> Orchestrator atualiza o Task Source
```

## Estratégia Git inicial

Durante a fase manual:

- Orchestrator: coordena e evita editar código de feature;
- Developer: usa branch/worktree própria quando houver paralelismo;
- QA: valida a branch do Developer ou usa worktree separada quando necessário;
- merge final somente após validação.

## Quando automatizar

Somente automatize depois de observar algumas execuções e identificar um problema recorrente que realmente justifique automação.

A regra é simples: não adicionar infraestrutura antes de existir necessidade real.
