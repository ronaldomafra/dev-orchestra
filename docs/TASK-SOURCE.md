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

Na primeira versão, **o Orchestrator é a sessão que precisa obrigatoriamente de acesso ao Task Source**.

A política recomendada é:

- Orchestrator: leitura + criação/atualização + transição de estado;
- Developer: acesso direto opcional; normalmente trabalha a partir do handoff;
- QA: acesso direto opcional; normalmente trabalha a partir de critérios + evidências;
- Planner: acesso direto opcional quando a análise exigir leitura do backlog.

Isso mantém um único proprietário do estado operacional e reduz alterações concorrentes.

Workers podem receber acesso direto ao Task Source quando houver um caso de uso real, mas esse acesso não é requisito para o fluxo básico.

## Falha do adapter

Se o Task Source estiver indisponível:

- não inventar estado;
- não marcar tarefa como concluída localmente;
- não afirmar que o backlog foi atualizado;
- permitir execução somente se houver um handoff completo independente do adapter;
- devolver ao Orchestrator o bloqueio de integração quando a sincronização for necessária.

## Futuro

Adapters possíveis:

- `trello`
- `jira`
- `github-issues`
- `github-projects`
- `linear`

O restante da arquitetura deve funcionar sem conhecer detalhes internos do adapter.
