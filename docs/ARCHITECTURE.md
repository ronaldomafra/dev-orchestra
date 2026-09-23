# Arquitetura

## Objetivo

Dev Orchestra organiza desenvolvimento assistido por IA em sessões especializadas, com um agente coordenador e workers focados.

A arquitetura busca:

- reduzir inflação de contexto;
- permitir execução paralela;
- tornar o trabalho observável;
- preservar conhecimento entre sessões;
- facilitar troca entre CLIs e modelos;
- manter o desenvolvedor humano no controle.

## Componentes

### Developer

Responsável por prioridade, supervisão e decisões finais.

### Orchestrator

Camada de coordenação.

Responsabilidades:

- consultar o Task Source;
- selecionar o próximo trabalho;
- reunir contexto mínimo;
- decompor quando necessário;
- delegar;
- acompanhar dependências;
- receber resultados;
- atualizar o estado da tarefa;
- decidir quando chamar QA ou Planner;
- escalar dúvidas ao humano.

### Worker: Developer

Executa implementação.

Não deve ser responsável por coordenar o sistema inteiro.

### Worker: QA

Valida comportamento, critérios de aceite e regressões.

### Worker: Planner

Papel opcional para análise técnica, decomposição ou investigação antes da implementação.

### Task Source

Interface conceitual para o backlog operacional.

Primeira implementação: Trello via MCP.

Possíveis futuras implementações:

- Jira;
- GitHub Issues;
- GitHub Projects;
- Linear;
- outras plataformas.

### Shared AI Memory

Camada externa de memória persistente.

Serve para preservar conhecimento entre:

- sessões;
- terminais;
- modelos;
- CLIs;
- reinícios.

Não substitui Task Source nem Git.

## Diagrama lógico

```text
                       +-------------------+
                       |     Developer     |
                       +---------+---------+
                                 |
                                 v
+----------------+      +--------+---------+      +------------------+
|  Task Source   |<---->|   Orchestrator   |<---->| Shared AI Memory |
| Trello adapter |      +---+----------+---+      +------------------+
+----------------+          |          |
                            |          |
                         handoff    handoff
                            |          |
                            v          v
                      +-----+---+  +---+-----+
                      | Dev     |  | QA      |
                      | Worker  |  | Worker  |
                      +----+----+  +----+----+
                           |            |
                           +-----+------+
                                 |
                              result
```

## Controle de estado

A recomendação inicial é que o **Orchestrator seja o proprietário das transições de estado do Task Source**.

Workers podem ter acesso de leitura e, dependendo do adapter, podem anexar evidências ou comentários, mas não devem mover cards livremente entre estados sem contrato definido.

Isso reduz corrida de estado e mantém um ponto claro de coordenação.

## Execução paralela

Para tarefas de código paralelas:

- uma sessão por papel;
- preferencialmente uma branch/worktree por worker;
- handoff explícito;
- merge/rebase somente com coordenação.

## Evolução

Fase 1: operação manual observável.

Fase 2: scripts para bootstrap das sessões.

Fase 3: delegação parcialmente automatizada.

Fase 4: adapters adicionais de Task Source e CLIs.
