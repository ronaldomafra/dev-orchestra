# Workflow operacional

## Preparar as sessões

Cada sessão deve carregar AGENTS.md e exatamente um papel de roles/. O uso de AI Memory é recomendado para compartilhar conhecimento durável e retomar workstreams.

Para sessões paralelas, use workstreams distintos no AI Memory:

    ai-memory run --new orchestrator codex --remote ws://127.0.0.1:4500
    ai-memory run --new developer codex --remote ws://127.0.0.1:4500
    ai-memory run --new qa codex --remote ws://127.0.0.1:4500

Para retomar um workstream:

    ai-memory run --workstream orchestrator codex --remote ws://127.0.0.1:4500

No Codex, use /rename para identificar cada sessão do canal, por exemplo orchestrator, developer ou qa. O nome do workstream e o nome da sessão Codex são conceitos diferentes.

Depois de iniciar cada sessão, leia AGENTS.md e o papel correspondente:

- Orchestrator: roles/orchestrator.md
- Developer: roles/developer.md
- QA: roles/qa.md
- Planner: roles/planner.md

## Ciclo de trabalho

1. **Selecionar:** Orchestrator consulta o Task Source e escolhe uma tarefa elegível.
2. **Preparar:** reúne critérios, dependências, referências e memória realmente relevantes.
3. **Delegar:** envia templates/handoff.md pelo canal entre sessões e informa RETURN_TO.
4. **Aguardar:** encerra o turno e espera o resultado. Não faz polling nem inspeciona arquivos para descobrir se o worker terminou.
5. **Executar:** worker trabalha somente no escopo recebido e reúne evidências.
6. **Retornar:** worker envia templates/result.md pelo mesmo canal.
7. **Validar:** Orchestrator chama QA quando os critérios exigem validação independente.
8. **Consolidar:** Orchestrator atualiza o Task Source com base no resultado e registra conhecimento durável quando necessário.

## Estados do backlog

Adapte os nomes à ferramenta. Um fluxo simples pode ser:

    Backlog -> Em execução -> Em validação -> Concluído

Se QA reprovar, o Orchestrator devolve a tarefa para execução e anexa a evidência. Só marca como concluída quando os critérios estiverem avaliados e houver evidência suficiente.

## Git

Em execução paralela, use branch ou worktree separada por worker quando apropriado. QA valida a revisão recebida e relata falhas; não corrige o código silenciosamente. Coordene integração e merge após revisão.

Leia git status antes de editar e preserve alterações existentes.

## Se uma integração falhar

- **Task Source:** informe a indisponibilidade; continue somente com uma tarefa explicitamente recebida.
- **Canal entre sessões:** não simule entrega nem cole a mensagem no backlog. Informe o bloqueio usando o canal disponível ou à pessoa responsável.
- **AI Memory:** não invente conteúdo recuperado; siga com a tarefa explícita usando o repositório e o contexto disponível.

## Critério para concluir

Uma tarefa termina quando o worker enviou um resultado, os critérios foram avaliados, as evidências estão registradas e os bloqueios foram resolvidos ou aceitos. O Orchestrator então atualiza o Task Source e encerra a execução.
