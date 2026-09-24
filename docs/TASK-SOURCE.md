# Task Source

## Papel

Task Source é a abstração para a ferramenta que guarda backlog e estado operacional. Trello é o primeiro adapter documentado; o processo não depende de uma plataforma específica.

O Task Source responde a duas perguntas: o que precisa ser feito e em que estado está?

## Campos úteis

Uma tarefa pode incluir:

- identificador e título;
- descrição e critérios de aceite;
- status e prioridade;
- dependências, rótulos e referências;
- metadados próprios do adapter.

Nem toda plataforma precisa suportar todos os campos.

## Responsabilidades

No fluxo inicial, o Orchestrator é responsável por:

- consultar tarefas elegíveis;
- selecionar trabalho e considerar dependências;
- registrar início, validação e conclusão;
- anexar referências e evidências;
- manter o estado do backlog coerente com o resultado recebido.

Workers trabalham a partir do handoff. Seu acesso direto ao backlog é opcional e não os autoriza a mudar o status sem regra explícita.

## O que o Task Source não é

- canal de comunicação entre sessões;
- fila de mensagens;
- memória técnica durável;
- repositório de código ou documentação completa.

Handoffs e resultados passam pelo canal configurado para a CLI. Conhecimento durável vai para AI Memory ou para documentação versionada, conforme o caso.

## Indisponibilidade

Se o Task Source não estiver acessível, informe o bloqueio e não invente tarefas ou estados. Continue somente se o usuário ou o Orchestrator já forneceu a tarefa explicitamente. Não afirme que o backlog foi atualizado quando a integração falhou.

## Trello

Mapeamento inicial sugerido:

| Trello | Task Source |
| --- | --- |
| Board | Espaço do projeto |
| List | Estado da tarefa |
| Card | Tarefa |
| Labels | Prioridade ou tipo |
| Checklist | Critérios ou subtarefas |
| Comments e links | Histórico resumido e evidências |

A URL MCP documentada para o adapter Trello é https://mcp.trello.com/v1. Consulte a configuração atual da sua CLI e do serviço antes de conectar.
