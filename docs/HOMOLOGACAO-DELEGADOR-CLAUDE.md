# Homologação do delegador com Claude Code (autohomologação do desenvolvimento)

> Realizada em 2026-11-15 no projeto de teste `teste-dev-o`, com sessões interativas reais do Claude Code (orquestrador em
> Sonnet 5.5, developer em Haiku 5.5, esforço baixo) e o delegador em container Docker. **Codex: implementado e testado só
> com um App Server de mentira; não homologado** (a conta Codex estava sem plano). A homologação final é da pessoa autora.

## O que foi exercitado, com sessões reais

As tarefas foram criadas com conteúdo inventado para melhorar a página `index.html` (loja fictícia "Trama & Estilo"). Todas
foram entregues ao orquestrador pelo caminho da própria ferramenta (`dev-orchestra tasks send`); a pessoa (eu, nos testes)
aprovou modelo e esforço e cada comando de Bash pedido pelas sessões.

| # | Cenário | Resultado |
| --- | --- | --- |
| 1 | PAG-001, fluxo normal: orquestrador recomenda modelo (pergunta estruturada), `delegate`, developer acorda sozinho, edita, valida, `report` DONE; orquestrador acorda sozinho, confere o diff de forma independente e atualiza o arquivo da tarefa | **OK**: seção "Coleção Primavera" com 3 cards e grade responsiva, diff só com linhas adicionadas |
| 2 | PAG-002, `ask`/`answer`: tarefa com mensagem e cor omitidas. O orquestrador percebeu a lacuna; a pessoa mandou delegar assim mesmo. O developer usou `ask`, o orquestrador levou a pergunta à pessoa e respondeu com `answer`; o developer terminou | **OK** (tarefa `asked` → `running` → `done`) |
| 3 | PAG-003, cancelamento pela pessoa (`dev-orchestra delegator cancel`) com o developer no meio do trabalho, parado num pedido de permissão | **OK com ressalva**: o orquestrador recebeu o aviso na hora; o developer só recebeu o cancelamento ao reabrir a sessão (ver achado 4) |
| 4 | PAG-004, cancelamento no meio do turno, depois de corrigir o listener | **OK**: o aviso chegou ao developer no meio do turno (a ferramenta em curso terminou 3 s antes); ele parou, não criou nada e não chamou `report` |
| 5 | PAG-004 de novo: nova tentativa de uma tarefa cancelada (`delegate` da mesma tarefa, tentativa 2) | **OK**: tentativa 2 concluída (`sobre.html` com 4 parágrafos e link no rodapé) |
| 6 | PAG-005, com critério de captura de tela em navegador | **OK**: o developer usou o Chrome headless; o orquestrador abriu as capturas e só então fechou a tarefa |
| 7 | Permissões, com os `session_id` reais, direto no MCP: developer chamando `delegate`, `answer` e `cancel_task`; orquestrador chamando `report` e `ask`; `delegate` sem `user_approved`; `delegate` ao próprio orquestrador; sessão inexistente; mensagem já entregue | **10 de 10 recusados** com mensagem clara |
| 8 | Defesa em profundidade: pedi ao developer, em linguagem natural, que chamasse `delegate` | A ferramenta nem aparece para ele (a definição do agente só libera as ferramentas de worker) |
| 9 | Persistência: container reiniciado e sessões reabertas | Estado mantido (volume SQLite); mensagem pendente de um papel é entregue quando a sessão volta |
| 10 | TUI com os dados reais: "Delegador :8787 no ar", selo DELEGADOR, linha "Delegador" nos cartões de agente, eventos do delegador na atividade, cancelar delegação (`x`) | **OK** |

Latências medidas (do evento no delegador ao `receive` da sessão ociosa): delegar → developer 1 a 2 s; resultado → orquestrador
2 a 4 s; pergunta → orquestrador 3 s; resposta → developer 3 s; aviso de cancelamento → orquestrador 3 s. O listener custa
0,13 s por execução.

## Achados e correções feitas durante a homologação

1. **A atualização automática da TUI nunca disparava** (nome de método igual a um atributo interno do Textual). Corrigido, com testes.
2. **O contrato do projeto ainda mandava usar SendMessage**: o developer (corretamente) recusou trocar de canal por uma mensagem
   de outra sessão. Criados `docs/DELEGATOR.md` (com precedência sobre o transporte), linha e parágrafo no `AGENTS.md`, nota nos
   `roles/*.md`, no `docs/PROTOCOL.md` e nas definições de agente. Para um projeto que já tinha `AGENTS.md`, o texto de abertura do
   orquestrador pede a incorporação com aprovação da pessoa: **funcionou** (o orquestrador propôs só as duas inserções e gravou
   depois do "aprovado").
3. **Definições de agente com `tools:` explícito não enxergam o MCP**: o instalador (`delegator install`) acrescenta as ferramentas
   do delegador (de worker para developer/qa/planner; de orquestrador para definições de orquestrador).
4. **Cancelar uma sessão ocupada, ou interrompida por Esc, ficava sem aviso**: o listener só rodava entre turnos. Agora o hook
   rearma o listener em `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop` e `Notification` (com trava por agente e aviso
   específico de CANCELAMENTO).
5. A pasta com `permissions.allow`/hooks gera o **aviso de confiança** do Claude ao abrir (precisa de "Yes, I trust this folder",
   uma vez por projeto). O instalador avisa.
6. Uma sessão ocupada por muito tempo aparecia como "sem sinal" na TUI; agora aparece "trabalhando".

## Limites conhecidos

- **Acordar é por turno ou por ferramenta**: uma sessão em geração longa sem chamar ferramentas só vê o aviso no fim. O aviso nunca
  interrompe uma ferramenta em curso.
- **Janela de escuta**: cada listener vive no máximo 59 min; qualquer evento acima o rearma, mas uma sessão parada por mais tempo
  fica sem escuta até a próxima interação.
- **Sessão interrompida e ociosa** só volta a ser escutada no próximo turno ou na notificação de ociosidade do Claude.
- `user_approved` é um registro, não uma prova (o delegador não vê a conversa).
- Instruções entregues pelo relay (`dev-orchestra send`) chegam como mensagem de outra sessão: o Claude as segue se o contrato do
  projeto concorda, e as questiona se não concordar (comportamento desejável).
- O hook usa o Python da instalação que rodou `delegator install`; rode de novo se reinstalar a ferramenta (`uv tool install`).

## O que ficou no ambiente da pessoa (para a homologação)

- Cadastro `teste-dev-o`: `delegador = true` (prefixo restaurado para `dev-o`; cópia anterior do TOML na pasta de trabalho da sessão).
- Pasta `~/projetos/pessoal/teste-dev-o`: `.claude/settings.json` (permissão e hooks do delegador), `.claude/agents/*.md` com as
  ferramentas do delegador, `docs/DELEGATOR.md`, `.dev-orchestra/contrato/AGENTS.md`, `AGENTS.md` com as duas inserções aprovadas,
  tarefas PAG-001 a PAG-005 (a PAG-003 ficou cancelada), `index.html` melhorado e `sobre.html` novo.
- Servidor MCP `dev-orchestra` registrado com `claude mcp add --scope local` (arquivo `~/.claude.json`, só para essa pasta).
- Container `dev-orchestra-delegator` rodando (rede do host, `127.0.0.1:8787`) com o volume `dev-orchestra-delegator-data`.
- AI Memory: workstreams `hom-orchestrator` e `hom-developer` criados no projeto `teste-dev-o`. As duas sessões que a pessoa já
  tinha abertas (`dev-o-orchestrator` e `dev-o-developer`) **não foram tocadas** e precisam ser reabertas para carregar o MCP e os hooks.

## Roteiro para a homologação da pessoa (Claude)

1. `uv tool install --reinstall './tool[delegator]'` (ou rode a partir do checkout) e `dev-orchestra delegator docker up`.
2. `dev-orchestra delegator install teste-dev-o` (confirma permissões, hooks e definições de agente) e confira `claude mcp list`.
3. Feche as sessões antigas e abra as duas com os comandos do `dev-orchestra up teste-dev-o` (têm `DEV_ORCHESTRA_AGENT=...`).
4. Cole nas sessões o texto de abertura que a TUI mostra (ou use `s`/`i` na TUI) e confirme em `dev-orchestra delegator status teste-dev-o`.
5. Envie uma tarefa ao orquestrador, aprove o modelo, acompanhe os cartões de agente e o kanban; teste `ask`, o cancelamento (`x`)
   e a nova tentativa.
6. Tente, como pessoa, o que o papel não pode (peça ao developer que delegue): deve ser recusado.

## Pendente para o Codex (implementado, não homologado)

- `CodexWaker`: `turn/start` por cliente do App Server (com `thread/resume` se a thread estiver descarregada), testado só com servidor de mentira.
- Registro no Codex com o UUID da thread em `session_ref`; o despertar depende do App Server e da rede do host no container.
- A confirmar com plano ativo: aceitação do `turn/start` em thread ociosa e em thread ativa, ordem das mensagens e se o aviso curto chega sem quebrar o turno.


## Monitor e alertas (verificação com sessões reais, 2026-11-15)

- **Permissão:** o hook `PermissionRequest` avisou o delegador no mesmo instante em que o Claude pediu a aprovação de um comando Bash (o alerta
  trouxe o comando e o payload completo foi registrado); o `Notification` de permissão chegou 6 s depois e apenas atualizou o mesmo alerta. Depois da
  aprovação, `PostToolUse` e `Stop` limparam o alerta.
- **Pergunta à pessoa (`AskUserQuestion`):** o `PreToolUse` gerou o alerta com o texto da pergunta e as opções. A mesma pergunta dispara
  também `PermissionRequest` e `Notification` ("Claude needs your permission"): foram unificados num só alerta de pergunta.
- **Esc na pergunta:** não dispara `Stop` e o `idle_prompt` demorou mais de 2 min. A solução foi usar o status ao vivo do `claude agents --json`
  (`waiting` enquanto espera a pessoa, `idle` depois do Esc): um sinal de hook sem essa espera é descartado, e uma sessão `waiting` sem hook vira
  o alerta genérico "aguardando você". Só vale para agentes com o nome do cadastro.
- **Som e notificação:** `notify-send` (urgência crítica ou normal) e `pw-play` com os sons do tema do sistema funcionaram no GNOME/Wayland
  desta máquina (`dev-orchestra alerts test`).
- **Não verificado ao vivo:** `StopFailure` (erro de turno: limite de uso, API ou autenticação), porque não consegui provocar o erro; o hook está
  instalado e o tratamento coberto por testes com payloads simulados. O watcher em segundo plano (`alerts watch` como serviço) foi testado em
  um ciclo (`--once`), sem instalar o serviço.
