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

Trello é o primeiro Task Source. Codex app-server + codex queue é o primeiro transporte validado. São ferramentas usadas para cumprir os contratos do processo. O repositório não implementa um runtime próprio nem adapters para outros Task Sources.

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

## Escopo atual

O POC valida o canal entre sessões Codex. O primeiro fluxo documentado inclui Trello e AI Memory, com Orchestrator e Developer em terminais separados. A integração de ponta a ponta ainda precisa de validação; QA e Planner são extensões opcionais. Consulte o [Quick Start](../README.md#quick-start-um-card-dois-agentes) e o [histórico local](CODEX-QUEUE-POC.md#historico-local-preservado).

O processo deve continuar portátil: responsabilidades ficam nos contratos do repositório; diferenças de ferramentas ficam nos adapters.
