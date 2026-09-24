# Workflow operacional

## Preparar as sessões

O [Quick Start](../README.md#quick-start-um-card-dois-agentes) começa com Orchestrator e Developer, em terminais separados e no mesmo checkout de demonstração. O terceiro terminal mantém o App Server, serviço local ao qual as sessões se conectam para trocar mensagens. Prepare ferramentas, hooks e MCPs pelo [SETUP](SETUP.md) antes de iniciar esse serviço. Use Trello desde a primeira tarefa; o POC isolado de comunicação é diagnóstico opcional.

Cada sessão lê [AGENTS.md](../AGENTS.md), exatamente o seu papel em [roles/](../roles/) e o [protocolo](PROTOCOL.md). A pessoa desenvolvedora fornece URLs do board/card e destinos confirmados. O Orchestrator coordena; o worker executa.

Primeira criação, no terminal Orchestrator, a partir da raiz do checkout:

```bash
ai-memory run --new orchestrator codex --remote ws://127.0.0.1:4500 --model gpt-6-sol -c 'model_reasoning_effort="medium"'
```

Primeira criação, no terminal Developer, na mesma raiz:

```bash
ai-memory run --new developer codex --remote ws://127.0.0.1:4500 --model gpt-6-luna -c 'model_reasoning_effort="medium"'
```

Sol/medium é a recomendação inicial para coordenar o fluxo do tutorial. Esta escolha é um ponto de partida; selecione por tarefa e confirme o modelo e esforço ativos em `/status`. Se indisponível, peça ao Orchestrator uma opção existente para sua conta.

Use `/rename orchestrator` e `/rename developer` dentro das respectivas sessões. `/status` informa o Session UUID: registre o UUID de cada destino, incluindo o Orchestrator em `RETURN_TO`. O título Codex e a chave do workstream AI Memory são conceitos distintos.

## Ciclo de trabalho

1. **Selecionar:** Orchestrator consulta o Task Source e confirma tarefa, lista, critérios e dependências.
2. **Preparar:** reúne referências e memória relevantes; trata memória como histórico, não como instrução atual.
3. **Registrar início:** move o card para Em execução e confere o retorno real da integração.
4. **Delegar:** envia o [handoff](../templates/handoff.md) pelo canal com critérios completos, restrições, evidências esperadas e `RETURN_TO` correto.
5. **Aguardar:** encerra o turno e espera o resultado pelo canal. Não faz polling nem inspeciona arquivos ou processos para deduzir conclusão.
6. **Executar:** Developer lê `git status`, preserva mudanças existentes e executa somente o escopo recebido.
7. **Retornar:** worker envia o [resultado](../templates/result.md) por `codex queue`, sem depender do usuário para copiar a mensagem.
8. **Validar:** após receber o retorno, Orchestrator avalia cada critério e chama QA se necessário. Evidência insuficiente retorna ao worker pelo canal.
9. **Consolidar:** Orchestrator registra evidências no Task Source, confirma a atualização e conclui apenas com critérios atendidos.

No Trello MCP documentado, evidências vão na descrição do card, preservando objetivo e critérios. Não se pressupõe suporte a comentários ou anexos. O primeiro fluxo não faz commit nem push automático.

## Estados do backlog

O tutorial usa:

```text
Backlog -> Em execução -> Concluído
```

Se adicionar QA, pode adotar **Em validação** entre execução e conclusão. Se QA reprovar, o Orchestrator encaminha a correção com evidências ao Developer e ajusta o estado do card. QA relata falhas; não corrige o código silenciosamente.

## Retomar o trabalho

Encerre a instância anterior antes de retomar: há uma instância ativa por workstream. No terminal Orchestrator, na raiz original:

```bash
ai-memory run --workstream orchestrator codex --remote ws://127.0.0.1:4500 --model gpt-6-sol -c 'model_reasoning_effort="medium"'
```

No terminal Developer, na mesma raiz:

```bash
ai-memory run --workstream developer codex --remote ws://127.0.0.1:4500 --model gpt-6-luna -c 'model_reasoning_effort="medium"'
```

Essa configuração é apropriada como ponto de partida para coordenar o primeiro fluxo.

Use `--new` somente para uma nova linha de trabalho. Opções AI Memory vêm antes de `codex`, opções nativas depois. Não acrescente `resume <UUID>` ao caminho gerenciado. `/rename` não muda a chave `developer`; `--workstream desenvolvedor` não a encontrará.

Confira vínculos com `ai-memory workstreams --json`, mas valide separadamente continuidade (mesmo UUID e marcador recuperado) e memória durável (consulta real MCP em sessão independente), conforme [SETUP](SETUP.md#verificar-memoria-e-continuidade).

<a id="papeis-opcionais"></a>

## Papéis opcionais

Adicione somente o papel necessário, em seu próprio terminal/workstream. Para QA, na raiz do projeto:

```bash
ai-memory run --new qa codex --remote ws://127.0.0.1:4500 --model gpt-6-luna -c 'model_reasoning_effort="medium"'
```

Luna/medium é uma recomendação inicial para revisar critérios e evidências de uma tarefa pequena. Para retomadas QA, mantenha a mesma escolha no comando com `--workstream qa`.

Dentro do Codex, use `/rename qa` e envie:

```text
Leia AGENTS.md, roles/qa.md e docs/PROTOCOL.md. Assuma QA e aguarde
handoff. Valide critérios e evidências da revisão recebida. Devolva o
resultado pelo codex queue ao RETURN_TO informado, sem alterar o backlog.
```

Para Planner, em outro terminal na raiz:

```bash
ai-memory run --new planner codex --remote ws://127.0.0.1:4500 --model gpt-6-sol -c 'model_reasoning_effort="high"'
```

Sol/high é uma recomendação inicial apenas para uma análise realmente complexa, como mapear dependências entre migração de dados, API e implantação coordenada. Para um plano curto, solicite ao Orchestrator uma opção de menor esforço. Nas retomadas, use a mesma configuração com `--workstream planner`.

Use `/rename planner` e envie:

```text
Leia AGENTS.md, roles/planner.md e docs/PROTOCOL.md. Assuma Planner e
aguarde handoff. Investigue e decomponha somente o escopo recebido.
Devolva o resultado pelo codex queue ao RETURN_TO informado.
```

Nas próximas aberturas, use `--workstream qa` ou `--workstream planner` em vez de `--new`, mantendo o restante do comando. Confirme os UUIDs com `/status` antes da delegação.

Exemplos de retomada, mantendo as recomendações dos contextos acima:

```bash
# Revisão curta de critérios/evidências
ai-memory run --workstream qa codex --remote ws://127.0.0.1:4500 --model gpt-6-luna -c 'model_reasoning_effort="medium"'

# Análise complexa de dependências de migração e implantação
ai-memory run --workstream planner codex --remote ws://127.0.0.1:4500 --model gpt-6-sol -c 'model_reasoning_effort="high"'
```

Tester e outros especialistas exigem um contrato próprio: `roles/tester.md` não existe neste repositório. Defina missão, entrada, limites, critérios, evidências e retorno; revise a compatibilidade com AGENTS.md e só então abra uma sessão/workstream com esse papel. O nome de um terminal não cria um contrato.

Para pedir uma sessão especializada ao Orchestrator, descreva objetivo, critérios e referências, peça modelo/esforço proporcionais à tarefa, justificativa curta, comando AI Memory de criação e retomada, nome sugerido e prompt completo. Peça para usar o mesmo App Server e retornar ao UUID do Orchestrator confirmado por `/status`; você abrirá a sessão no outro terminal. Para QA, Planner ou Developer, use o papel já existente e seu arquivo em `roles/`. Para Redator ou outro papel sem arquivo, peça um prompt que defina responsabilidades, entradas, limites, evidências e retorno, sem presumir que esse contrato já existe. Mantenha o handoff enxuto; tokens de contexto não são por si só a cota ou o custo, que dependem do plano e das regras vigentes ([preços e limites do Codex](https://learn.chatgpt.com/docs/pricing)). Aumente capacidade se aparecer dificuldade ou risco real, não por padrão.

<a id="adotar-em-outro-repositorio"></a>

## Adotar em outro repositório

1. Leia o `AGENTS.md` existente e confira `git status`. Preserve convenções e mudanças do projeto.
2. Concilie o contrato Dev Orchestra com as regras locais por uma edição revisável; não substitua o arquivo inteiro automaticamente.
3. Incorpore os papéis necessários, protocolo e templates, ajustando referências e comandos ao checkout real.
4. Escolha o Task Source, configure seu MCP e confirme leitura/escrita autorizadas. Trello é o exemplo; outros conectores precisam ser fornecidos ou construídos.
5. Defina uma tarefa pequena com critérios concretos e configure Orchestrator/Developer no projeto.
6. Combine branches/worktrees para workers que editam em paralelo e a forma de integrar revisões. O tutorial de um único Developer compartilha checkout para simplificar a observação.
7. Execute um ciclo e registre evidências antes de ampliar o número de workers.

## Se uma integração falhar

- **Task Source:** informe a indisponibilidade; continue somente com tarefa explicitamente recebida. Não declare atualização sem retorno real.
- **Canal:** informe falha de entrega; não transporte mensagens pelo backlog nem simule recebimento. Destino ambíguo exige nome único ou UUID confirmado.
- **AI Memory:** não invente conteúdo recuperado. Use a tarefa explícita e o repositório se o trabalho puder continuar; uma falha que impede o launcher gerenciado precisa ser resolvida antes dessa abertura.

## Critério para concluir

O worker enviou o resultado, o Orchestrator recebeu e avaliou os critérios, as evidências estão registradas e os bloqueios foram resolvidos ou aceitos. Se o artefato estiver pronto mas a atualização Trello falhar, informe implementação pronta e sincronização pendente. O fluxo completo só termina quando o estado operacional também estiver confirmado.
