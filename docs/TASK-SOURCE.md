# Task Source

## Conceito

`Task Source` é a abstração para qualquer sistema que represente backlog e estado operacional do trabalho.

O Dev Orchestra começa com Trello, mas o protocolo não deve depender de termos específicos como board, list ou card.

## Modelo genérico

Uma tarefa deve poder fornecer:

- `id`
- `title`
- `description`
- `status`
- `priority`
- `acceptanceCriteria`
- `labels`
- `dependencies`
- `references`
- `metadata`

Nem todo adapter precisa suportar todos os campos.

## Operações conceituais

Um adapter deve tentar oferecer:

- listar tarefas;
- buscar tarefa por ID;
- ler detalhes;
- mudar estado;
- adicionar comentário/evidência;
- relacionar referências;
- identificar bloqueios.

## Trello

Mapeamento inicial sugerido:

- Board -> workspace operacional do projeto;
- List -> estado da tarefa;
- Card -> tarefa;
- Labels -> prioridade/tipo;
- Checklist -> critérios ou subtarefas;
- Comments -> histórico resumido/evidências;
- Attachments/links -> PRs, logs ou documentos.

## Regra de acesso

Para a primeira versão, Codex CLI e Claude Code devem ter acesso ao conector/MCP necessário para consultar o Task Source.

A política recomendada é:

- Orchestrator: leitura + atualização de estado;
- Dev: leitura; comentário opcional;
- QA: leitura; comentário/evidência opcional;
- Planner: leitura.

Essa política poderá ser relaxada depois, mas começar restritivo reduz inconsistências.

## Falha do adapter

Se o Task Source estiver indisponível:

- não inventar estado;
- não marcar tarefa como concluída localmente;
- devolver bloqueio de integração;
- permitir execução somente se houver um handoff completo independente do adapter.

## Futuro

Adapters possíveis:

- `trello`
- `jira`
- `github-issues`
- `github-projects`
- `linear`

O restante da arquitetura deve funcionar sem conhecer detalhes internos do adapter.
