# Modo delegador (MCP)

Quando o cadastro do projeto está em modo delegador, as sessões **não** trocam mensagens diretamente: tudo passa pelo **delegador**, um serviço MCP (`dev-orchestra`) que confere quem pode falar com quem, guarda cada mensagem e o estado de execução das tarefas, e registra o histórico. Este documento **tem precedência** sobre `docs/PROTOCOL.md`, `docs/ADAPTER-*.md` e os `roles/*.md` em tudo o que se refere ao **transporte** (SendMessage, `codex queue`, `RETURN_TO`). Handoff, resultado, aprovação de modelo e Task Source continuam como nesses documentos.

## Regras

1. Ao abrir a sessão, chame `register_session` (project, role, cli, session_ref). Guarde o `session_id` devolvido: as outras ferramentas o exigem. O retorno inclui o **perfil** do seu papel.
2. Fale com outras sessões **somente** pelas ferramentas do delegador. Não use SendMessage, `codex queue` nem a caixa de mensagens do AI Memory.
3. Um aviso de "mensagem pendente" não traz conteúdo. Chame `receive` para obtê-lo. O que `receive` entrega é a **tarefa vigente** do seu papel.
4. Depois de `delegate`, `report` ou `ask`, **encerre o turno**. Respostas e novas tarefas chegam como nova mensagem. Não use `sleep` nem polling.
5. O delegador recusa o que o seu papel não pode fazer. Não tente contornar.

## Ferramentas

| Ferramenta | Quem | O que faz |
| --- | --- | --- |
| `register_session` | todos | registra a sessão e devolve `session_id` e o perfil do papel |
| `heartbeat` | todos | sinal de vida; devolve quantas mensagens esperam |
| `delegate` | só Orchestrator | entrega uma tarefa a um papel worker (`task_id`, `to_role`, `instruction`, `model`, `effort`, `model_reason`, `user_approved=true`) |
| `receive` | todos | entrega a próxima mensagem endereçada ao seu papel (uso único) |
| `report` | só worker, na própria tarefa | devolve o resultado (`status` DONE, PARTIAL, BLOCKED, FAILED ou NEEDS_REVIEW, `summary`, `evidence`, e opcionais) |
| `ask` | só worker, na própria tarefa | pergunta ao Orchestrator em vez de supor; a tarefa fica `asked` |
| `answer` | só Orchestrator | responde a uma pergunta (a resposta vai ao mesmo worker) |
| `cancel_task` | Orchestrator (e a pessoa, pela TUI) | cancela uma tarefa delegada; o worker é avisado |
| `project_status` | todos (worker só vê a própria tarefa) | texto simples com sessões e tarefas |

`user_approved=true` registra que a pessoa aprovou modelo e esforço (docs/MODEL-SELECTION.md). O delegador não consegue verificar isso: é um registro, não uma prova. Sem ele, `delegate` é recusado.

## Fluxo

1. A pessoa entrega a tarefa ao Orchestrator. Ele recomenda modelo e esforço, **pede a aprovação** e chama `delegate` com instrução autossuficiente. Encerra o turno.
2. O worker é avisado, chama `receive`, executa só o escopo recebido e termina com `report` (ou `ask`, se algo for ambíguo). Encerra o turno.
3. O Orchestrator é avisado, chama `receive`, avalia critério por critério, atualiza o arquivo da tarefa (`estado` e `## Resultado`) e, se houver Trello, o cartão. Lacunas voltam ao mesmo papel com novo `delegate` da mesma tarefa (nova tentativa).
4. Uma pergunta chega ao Orchestrator como mensagem `question`; ele responde com `answer` (ou leva à pessoa, se for decisão de produto, arquitetura ou risco).

Estados da tarefa no delegador: `delegated` → `running` (worker fez `receive`) → `asked` (aguarda resposta) → `done`, `partial`, `blocked`, `failed`, `needs_review`; ou `cancelled`. Os arquivos de tarefa do projeto continuam sendo a fonte de trabalho; o delegador guarda o estado de execução.

## Tipos de mensagem

`delegate` (tarefa), `result`, `question`, `answer`, `cancel` (a tarefa foi cancelada: pare, não chame `report`), `profile` (perfil atualizado: releia).

## Agentes passivos

Além dos papéis do projeto, o delegador atende **agentes passivos**: auxiliares sem sessão aberta e sem projeto, consultados pelo id.

| Ferramenta | O que faz |
| --- | --- |
| `list_agents` | lista os agentes passivos (id, descrição, se o executor está ativo) |
| `consult` | envia um pedido autossuficiente a um agente (`agent_id`, `instruction`, `origin`); devolve `consult_id` |
| `consult_result` | estado (`pending`, `running`, `done`, `failed`, `cancelled`), resposta e uso de tokens |

O formato da resposta é definido pelo perfil do agente. Os pedidos ao mesmo agente entram em fila. Não faça polling apertado:
espere alguns segundos entre chamadas de `consult_result`. Estas ferramentas não substituem `delegate`: não criam tarefa nem
mudam o estado do backlog.

## Instalação (resumo)

`dev-orchestra delegator docker up` (serviço), `dev-orchestra delegator install <projeto>` (servidor MCP, permissões e hooks do Claude Code) e `delegador = true` no cadastro. As sessões abrem com `DEV_ORCHESTRA_AGENT=<nome>` (o comando de subida da TUI já inclui). Detalhes em `tool/README.md`.
