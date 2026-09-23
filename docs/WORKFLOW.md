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
- ter acesso ao AI Memory;
- ter acesso adequado ao Task Source;
- trabalhar em branch/worktree compatível com seu papel.

## Ciclo básico

### 1. Seleção

Orchestrator consulta o Task Source e escolhe uma tarefa elegível.

### 2. Contextualização

Orchestrator consulta:

- card/tarefa;
- memória relevante;
- documentação;
- dependências.

### 3. Decomposição

Se necessário, chama Planner ou divide em subtarefas.

### 4. Handoff

Orchestrator envia uma mensagem seguindo `templates/handoff.md`.

### 5. Execução

Developer trabalha isoladamente.

### 6. Retorno

Developer responde com `templates/result.md`.

### 7. Validação

Orchestrator decide se o resultado exige QA.

QA recebe o mesmo objetivo, critérios de aceite e evidências da implementação.

### 8. Consolidação

Orchestrator:

- consolida o resultado;
- atualiza Task Source;
- registra links;
- promove conhecimento durável para AI Memory quando necessário;
- solicita decisão humana quando aplicável.

## Regra de contexto

O Orchestrator não deve absorver logs e diffs completos se um resumo verificável for suficiente.

Workers devem devolver síntese + evidência.

## Fluxo de bug

```text
Backlog
  -> Orchestrator
  -> Dev: reproduzir/corrigir
  -> QA: validar reprodução e regressão
  -> Orchestrator
  -> Task Source
```

## Fluxo de feature

```text
Backlog
  -> Orchestrator
  -> Planner (quando necessário)
  -> Dev
  -> QA
  -> Orchestrator
  -> Task Source
```

## Estratégia Git inicial

Durante a fase manual:

- Orchestrator: branch principal de coordenação, sem editar código de feature;
- Dev: worktree/branch própria da tarefa;
- QA: pode validar a branch do Dev ou usar worktree separada;
- merge final somente após validação.

## Quando automatizar

Somente automatize depois de observar algumas execuções e identificar:

- formatos estáveis de handoff;
- regras de transição de status;
- padrão de branch/worktree;
- políticas de memória;
- limites de autonomia.
