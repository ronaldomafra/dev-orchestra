# Dev Orchestra

Dev Orchestra é uma arquitetura de trabalho para desenvolvimento assistido por IA com múltiplas sessões especializadas.

A proposta é manter um agente principal como **Orchestrator**, responsável por ler o backlog, planejar, delegar, acompanhar e consolidar o trabalho, enquanto agentes especializados executam implementação, testes, análise e outras tarefas em sessões separadas.

## Visão

```text
                         +----------------------+
                         |      Developer       |
                         | supervises / decides |
                         +----------+-----------+
                                    |
                                    v
+-------------------+     +---------+----------+     +----------------------+
|   Task Source     |<--->|     Orchestrator   |<--->|   Shared AI Memory   |
| Trello initially  |     | plan / delegate    |     | durable context      |
+-------------------+     +----+-----------+----+     +----------+-----------+
                               |           |                     ^
                     delegates |           | delegates           |
                               v           v                     |
                         +-----+---+   +---+------+              |
                         |   Dev   |   |    QA    |--------------+
                         | session |   | session  |
                         +---------+   +----------+
                               \           /
                                \---------/
                                 Task results
```

## Princípios

- **Human in control:** o desenvolvedor continua responsável pelas decisões finais.
- **Task Source como backlog operacional:** inicialmente Trello, mas a arquitetura não depende dele.
- **Orchestrator coordena; workers executam:** o contexto de planejamento fica separado do contexto pesado de implementação e testes.
- **Memória persistente compartilhada:** decisões e conhecimento durável ficam fora da sessão temporária.
- **Papéis explícitos:** cada terminal/sessão recebe uma responsabilidade bem definida.
- **Portabilidade entre CLIs:** regras do projeto e memória não devem ficar presas a uma única ferramenta.
- **Contexto mínimo necessário:** cada agente recebe apenas o que precisa para cumprir a tarefa.
- **Git como registro técnico:** código, documentação e contratos de agentes vivem no repositório.

## Componentes

| Componente | Responsabilidade |
| --- | --- |
| Developer | Define prioridades, aprova decisões importantes e supervisiona o fluxo |
| Orchestrator | Lê backlog, quebra trabalho, delega, acompanha, consolida e atualiza estado |
| Developer Agent | Implementa código e executa verificações técnicas relacionadas |
| QA Agent | Valida critérios de aceite, regressões e evidências |
| Planner Agent | Opcional; pesquisa, decomposição e desenho técnico antes da implementação |
| Task Source | Backlog e estado operacional das tarefas |
| AI Memory | Contexto durável compartilhado entre sessões e CLIs |
| Git | Código, documentação, histórico e artefatos técnicos |

## Fonte de tarefas

O primeiro adapter será **Trello via MCP**. Toda sessão que participa do fluxo deve conseguir acessar a fonte de tarefas.

A arquitetura, porém, usa o conceito genérico de **Task Source**, permitindo futuramente adapters para Jira, GitHub Issues/Projects ou outras plataformas.

## Modelo de contexto

Dev Orchestra separa quatro tipos de informação:

1. **Task Source** — estado vivo do trabalho: backlog, prioridade, aceite e andamento.
2. **AI Memory** — conhecimento durável: decisões, padrões, descobertas e contexto reutilizável.
3. **Repository** — código, arquitetura, instruções e contratos versionados.
4. **Session Context** — contexto temporário necessário para executar uma tarefa específica.

Essa separação é central para evitar que o Orchestrator acumule todo o contexto técnico das tarefas executadas.

## Estrutura inicial

```text
.
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CONTEXT-MODEL.md
│   ├── PROTOCOL.md
│   ├── TASK-SOURCE.md
│   └── WORKFLOW.md
├── roles/
│   ├── orchestrator.md
│   ├── developer.md
│   ├── qa.md
│   └── planner.md
└── templates/
    ├── handoff.md
    └── result.md
```

## Estado do projeto

Este repositório começa pela **documentação do protocolo e dos papéis**. A automação virá depois que o fluxo manual com múltiplos terminais estiver estável.

Próximas etapas:

- validar o contrato de cada papel;
- definir o adapter Trello/MCP;
- validar integração do AI Memory em todas as sessões;
- padronizar handoff e retorno dos agentes;
- testar o fluxo manual com Orchestrator + Dev + QA;
- somente depois automatizar bootstrap, tmux, worktrees e delegação.

## Objetivo

Criar um fluxo de desenvolvimento assistido por IA que seja **observável, portátil, organizado e controlável**, sem transformar uma única sessão em um contexto gigante.
