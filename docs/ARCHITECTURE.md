# Arquitetura

## Objetivo

Dev Orchestra organiza trabalho assistido por IA em torno de responsabilidades explícitas. Separa a coordenação da execução e mantém o estado do trabalho fora das conversas.

## Componentes

| Componente | Responsabilidade |
| --- | --- |
| Pessoa desenvolvedora | Define prioridades e decide questões críticas |
| Orchestrator | Seleciona tarefas, coordena dependências e atualiza o Task Source |
| Workers | Executam análise, implementação ou validação |
| Task Source | Registra backlog e estado operacional |
| Session Transport | Entrega handoffs e resultados entre sessões |
| AI Memory | Preserva conhecimento durável entre sessões |
| Git | Versiona código, documentação, contratos e evidências |

Trello é o primeiro Task Source. O Session Transport tem duas implementações: no Codex, o `codex app-server` recebe conexões das sessões e `codex queue` entrega mensagens a uma sessão identificada (validado); no Claude Code, cada papel é uma sessão própria e SendMessage entrega mensagens entre sessões locais (em validação). São ferramentas usadas para cumprir os contratos do processo. O repositório não implementa um runtime próprio nem adapters para outros Task Sources.

## Fluxo

    Pessoa desenvolvedora
             |
             v
    Task Source <----> Orchestrator <----> AI Memory
                          |
                     handoff pelo
                    Session Transport
                          |
                 +--------+--------+
                 |                 |
             Developer             QA
                 |                 |
                 +---- resultados -+

O Orchestrator consulta o backlog e envia uma tarefa ao worker. O worker devolve um resultado estruturado pelo mesmo canal. O Orchestrator consolida as evidências e atualiza o estado da tarefa. QA participa quando os critérios exigem validação independente.

## Limites

- O Task Source guarda o estado do trabalho; não transporta mensagens entre agentes.
- O transporte entrega mensagens; não é backlog nem memória durável.
- AI Memory guarda decisões e aprendizados; não é backlog e não substitui Git.
- Workers não mudam o estado do backlog sem uma regra explícita.
- O Orchestrator não declara uma tarefa concluída sem evidência suficiente.
- O Orchestrator não delega sem aprovação do modelo e do esforço do worker.
- Workers são sessões próprias, não subagents dentro da sessão do Orchestrator.

## Escopo atual

O POC valida o canal entre sessões Codex. O primeiro fluxo documentado inclui Trello e AI Memory, com Orchestrator e Developer em terminais separados. A integração de ponta a ponta está validada com Codex em uso real; o modo Claude Code está documentado e em validação ([registro](CLAUDE-CODE-VALIDATION.md)). Um mesmo workstream AI Memory pode ser usado pelas duas CLIs. QA e Planner são extensões opcionais. Consulte o [Quick Start](../README.md#quick-start-um-card-dois-agentes) e o [histórico local](CODEX-QUEUE-POC.md#historico-local-preservado).

O processo deve continuar portátil: responsabilidades ficam nos contratos do repositório; diferenças de ferramentas ficam nos adapters ([Codex](ADAPTER-CODEX.md) e [Claude Code](ADAPTER-CLAUDE-CODE.md)). Um serviço Task Source pode ser conectado por um servidor MCP já disponível ou por um conector MCP próprio, desde que as operações e estados necessários sejam configurados e validados. Este repositório ainda não fornece esses conectores alternativos; veja [Task Source](TASK-SOURCE.md#outro-servico-via-mcp).
