# Dev Orchestra

Dev Orchestra é um processo e um conjunto de contratos para organizar desenvolvimento assistido por IA: um backlog, agentes com papéis explícitos, comunicação entre sessões, memória durável e Git. Você combina ferramentas existentes e acompanha a execução em sessões independentes, visíveis em terminais separados. Este repositório fornece os contratos e guias; não há um runtime ou instalador próprio do Dev Orchestra.

## Dos post-its às sessões coordenadas

A ideia nasceu de uma rotina real: bugs, melhorias e features eram anotados em post-its no monitor. Conforme o volume cresceu, essas anotações viraram um Kanban no Trello, com um board por projeto. A organização melhorou, mas ainda era necessário copiar manualmente cada tarefa do Trello para o agente.

Daí veio a pergunta: por que não conectar o Trello ao agente? A experiência com a integração agradou e abriu espaço para o próximo passo: um Orchestrator que consulta o backlog, delega e recebe resultados de outras sessões. A escolha foi tornar esse trabalho observável em outros terminais, em vez de concentrar tudo em uma única sessão com subagentes.

Cada sessão tem um papel e um canal de retorno. A pessoa desenvolvedora acompanha as trocas e continua responsável pelas decisões críticas de produto, arquitetura e risco.

![Arquitetura do Dev Orchestra](docs/assets/dev-orchestra-architecture.webp)

*Configuração ilustrativa: papéis adicionais são opcionais. O primeiro fluxo usa somente Orchestrator e Developer.*

## Como funciona

```mermaid
sequenceDiagram
    participant T as Trello
    participant O as Orchestrator
    participant D as Developer
    T->>O: Card e critérios
    O->>T: Em execução
    O->>D: Handoff via codex queue
    D->>O: Resultado e evidências via codex queue
    O->>T: Evidências e Concluído
```

O Orchestrator lê o card, registra o início e delega pelo canal entre sessões. Depois encerra o turno e aguarda o retorno, sem polling nem inspeção de arquivos ou processos para deduzir conclusão. O Developer executa e devolve evidências pelo mesmo canal. Só então o Orchestrator avalia os critérios e atualiza o backlog.

| Parte | Responsabilidade |
| --- | --- |
| Task Source | Backlog, prioridade, critérios e estado operacional; Trello é o exemplo inicial |
| [Orchestrator](roles/orchestrator.md) | Coordena, delega, avalia resultados e atualiza o backlog |
| [Developer](roles/developer.md) | Executa a tarefa delimitada e retorna evidências |
| [QA](roles/qa.md), opcional | Valida critérios e evidências quando necessário |
| [Planner](roles/planner.md), opcional | Investiga dependências e divide trabalho |
| AI Memory | Preserva decisões e conhecimento durável reutilizável |
| Git | Versiona código, documentação, contratos e evidências |
| Sessão | Mantém o contexto temporário da execução |

Memória recuperada é dado histórico, não instrução vigente. A política do projeto orienta o que promover a conhecimento durável; os hooks do AI Memory também podem capturar eventos técnicos de sessões. São responsabilidades diferentes, descritas no [modelo de contexto](docs/CONTEXT-MODEL.md).

## Maturidade

O transporte Codex entre sessões e o sandbox local foram validados. O App Server é experimental na CLI consultada (`0.156.1`); não foi estabelecida uma versão mínima. O fluxo completo Trello + AI Memory e a retomada gerenciada ainda precisam de validação de ponta a ponta. O tutorial abaixo é o roteiro para essa validação, não uma afirmação de que ela já ocorreu. O [registro do POC](docs/CODEX-QUEUE-POC.md) separa evidências e pendências.

## Quick Start: um card, dois agentes

O caminho principal usa **Linux/Ubuntu + Codex + AI Memory + Trello**. Você precisa de Git, curl, Docker acessível pelo seu usuário, autenticação no Codex, conta Trello e navegador para OAuth. Reserve três terminais: App Server, Orchestrator e Developer. Os comandos de preparação podem ser executados antes nesses mesmos terminais.

### 1. Instalar e preparar o checkout

Em uma instalação Ubuntu nova, comece com:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
curl -fsSL https://chatgpt.com/codex/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
codex login
codex login status
```

O instalador é o da [documentação oficial do Codex](https://learn.chatgpt.com/docs/codex/cli). Instale e verifique Docker seguindo [SETUP: Docker](docs/SETUP.md#docker). O guia traz os comandos completos para uma instalação nova e as verificações de acesso.

Clone um checkout de demonstração e use **este mesmo diretório em todos os terminais**:

```bash
git clone https://github.com/ronaldomafra/dev-orchestra.git "$HOME/dev-orchestra-demo"
cd "$HOME/dev-orchestra-demo"
git status --short
git --version
curl --version
docker info
codex --version
codex queue --help
```

Se o diretório já existir, confira seu conteúdo e use o checkout escolhido, sem sobrescrevê-lo. Neste tutorial há apenas um worker editando; a adoção com vários workers exige coordenar branches/worktrees.

### 2. Configurar AI Memory e Trello

**Antes de iniciar o App Server**, execute [SETUP: AI Memory](docs/SETUP.md#ai-memory): baixar o wrapper para `~/.local/bin` com verificação de checksum, iniciar o servidor Docker em `127.0.0.1:49374` com volume persistente e instalar hooks/MCP. O modo sem chaves LLM opcionais é suficiente. Após instalar o wrapper e o servidor, os comandos essenciais são:

```bash
ai-memory install-mcp --client codex --apply
ai-memory install-hooks --agent codex --apply
codex mcp add trello --url https://mcp.trello.com/v1
```

Conclua o OAuth no navegador, escolhendo o workspace Trello que contém o board. Se a autenticação não tiver sido concluída ao adicionar:

```bash
codex mcp login trello
```

Confira os registros:

```bash
codex mcp list
ai-memory run --help
```

A configuração listada não prova acesso ao board: a leitura pela sessão do Orchestrator será a verificação real. Detalhes e diagnóstico estão em [SETUP: Trello](docs/SETUP.md#trello). Se já há um App Server, confirme que ele usa essa configuração; não inicie outro na mesma porta. Se estiver desatualizado, combine seu encerramento e reabertura com quem usa as sessões antes de continuar.

### 3. Criar a tarefa no Trello

No navegador, crie um board de demonstração no workspace autorizado com as listas **Backlog**, **Em execução** e **Concluído**. Em Backlog, crie o card **DEMO-001: Meu primeiro fluxo**. Copie as URLs reais do board e do card para informar ao Orchestrator.

Use esta descrição no card:

```text
Objetivo: criar docs/demo/primeiro-fluxo.md para demonstrar o fluxo.

Critérios de aceite:
1. Criar somente docs/demo/primeiro-fluxo.md como artefato da tarefa.
2. O arquivo deve conter exatamente o conteúdo esperado abaixo, em UTF-8,
   com uma quebra de linha ao final.
3. Preservar quaisquer mudanças preexistentes. Se o arquivo já existir,
   reportar o conflito antes de sobrescrevê-lo.
4. Ler o conteúdo criado e executar git status --short --untracked-files=all;
   devolver essas evidências com STATUS, TASK, SUMMARY e EVIDENCE.
5. Não fazer commit nem push. Retornar pelo codex queue ao Orchestrator.

Conteúdo esperado:
# Meu primeiro fluxo

- Task Source: mantém o backlog e o estado operacional das tarefas.
- Orchestrator: coordena o trabalho e atualiza o backlog com evidências.
- Developer: executa a tarefa recebida e devolve o resultado ao Orchestrator.
```

O arquivo será criado pelo Developer **quando você executar o tutorial**; ele não acompanha esta reestruturação documental. Nenhuma linguagem de programação é necessária.

### 4. Terminal do App Server

Na raiz do checkout, verifique se o serviço já está ativo:

```bash
cd "$HOME/dev-orchestra-demo"
curl -i http://127.0.0.1:4500/readyz
curl -i http://127.0.0.1:4500/healthz
```

Se ambos responderem HTTP 200 e for o App Server esperado, reutilize-o. Se não houver serviço ativo, inicie neste terminal:

```bash
codex app-server --listen ws://127.0.0.1:4500
```

Mantenha-o aberto. Antes de abrir a sessão Orchestrator, use seu terminal para repetir os dois `curl` e obter HTTP 200. Se houver serviço respondendo com erro, consulte o [diagnóstico](docs/SETUP.md#diagnostico); não inicie uma segunda instância para contornar o problema.

### 5. Terminal do Orchestrator

Primeira criação:

```bash
cd "$HOME/dev-orchestra-demo"
ai-memory run --new orchestrator codex --remote ws://127.0.0.1:4500
```

Dentro do Codex, digite `/rename orchestrator`. Depois envie:

```text
Leia AGENTS.md, roles/orchestrator.md e docs/PROTOCOL.md.
Assuma o papel de Orchestrator. Aguarde eu informar board, card e destino
Developer para iniciar. O canal é codex queue no endpoint
ws://127.0.0.1:4500. Após delegar, encerre o turno e aguarde o resultado.
```

Digite `/status` e anote o **Session UUID do Orchestrator**. Esse é o destino `RETURN_TO` dos resultados, não o UUID do Developer.

### 6. Terminal do Developer

Primeira criação, no mesmo checkout:

```bash
cd "$HOME/dev-orchestra-demo"
ai-memory run --new developer codex --remote ws://127.0.0.1:4500
```

Dentro do Codex, digite `/rename developer`. Depois envie:

```text
Leia AGENTS.md, roles/developer.md e docs/PROTOCOL.md.
Assuma o papel de Developer e aguarde um handoff do Orchestrator.
Execute somente o escopo recebido. Verifique o resultado e envie-o
pela ferramenta codex queue no endpoint ws://127.0.0.1:4500 ao RETURN_TO
informado, com STATUS, TASK, SUMMARY e EVIDENCE. Não basta imprimir o
resultado nesta sessão; envie autonomamente. Não altere o Trello.
```

Digite `/status` e anote o **Session UUID do Developer**. Use o nome exato `developer` se for único e resolvível; o UUID confirmado é o fallback. Não escolha entre sessões ambíguas por tentativa. Não é necessário abrir uma sessão extra para testar o transporte.

### 7. Pedir a execução ao Orchestrator

Volte ao terminal do Orchestrator. Substitua os campos entre `<...>` antes de enviar:

```text
Execute DEMO-001: Meu primeiro fluxo.
Board: <URL real do board>
Card: <URL real do card>
Developer: developer
UUID confirmado do Developer: <Session UUID obtido no /status do Developer>
RETURN_TO: <Session UUID obtido no /status do Orchestrator>
Canal: codex queue --remote ws://127.0.0.1:4500

1. Consulte o board e o card pelo MCP Trello, confirme título, lista e
   critérios. Se o acesso falhar, informe o bloqueio; não invente o conteúdo
   nem afirme que atualizou o card.
2. Mova o card de Backlog para Em execução e confira o retorno real da operação.
3. Delegue ao Developer via codex queue --thread com nome único ou UUID
   confirmado e --message com TASK: DEMO-001, INSTRUCTION contendo todos
   os critérios e conteúdo esperado, referência ao card e RETURN_TO acima.
   Inclua o endpoint e a obrigação de enviar o resultado pelo mesmo canal.
   Não implemente o arquivo você mesmo. Não faça commit nem push.
4. Após o enfileiramento, encerre o turno e aguarde a resposta pelo canal.
   Não faça polling nem inspecione arquivos ou processos para inferir conclusão.
5. Ao receber o resultado, avalie cada critério e as evidências de leitura
   do arquivo e git status. Se faltar algo, devolva ao Developer pelo canal.
6. Com os critérios atendidos, acrescente à descrição do card um resumo
   de resultado e evidências, preservando o objetivo e todos os critérios.
   Mova para Concluído e confirme o estado por uma leitura real do card.
   Só afirme atualização após retorno real do Trello. Se falhar, reporte
   implementação pronta e sincronização pendente, sem declarar o fluxo concluído.
```

O canal aceita mensagens como descrito no [protocolo](docs/PROTOCOL.md#transporte-codex). O agente executa o comando de fila; você não precisa copiar o handoff e o resultado entre terminais. A [documentação do Trello MCP](https://support.atlassian.com/trello/docs/connect-trello-to-ai-assistants-with-trello-mcp/) lista comentários e anexos como funcionalidades futuras; por isso este fluxo usa a descrição do card para evidências.

### 8. Observar e conferir a conclusão

| Fase | Resultado esperado |
| --- | --- |
| Preparação | MCPs registrados; Orchestrator consegue ler o card real |
| Início | Card em Em execução, confirmado pelo Trello |
| Delegação | Fila aceita o handoff e a sessão Developer recebe DEMO-001 |
| Execução | Developer cria o arquivo, lê o conteúdo e coleta git status |
| Retorno | Orchestrator recebe STATUS, TASK, SUMMARY e EVIDENCE pelo canal |
| Conclusão | Critérios avaliados, descrição com evidências e card em Concluído |

Após o retorno, você também pode conferir no shell do checkout:

```bash
cat docs/demo/primeiro-fluxo.md
git status --short --untracked-files=all
```

Em um checkout limpo, o status deve incluir `?? docs/demo/primeiro-fluxo.md`. Mudanças técnicas de configuração eventualmente geradas pelas ferramentas devem ser identificadas separadamente na evidência. `git diff` sozinho não mostra o conteúdo de um arquivo novo ainda não rastreado. O tutorial não faz commit automático.

- [ ] O arquivo contém exatamente o texto do card.
- [ ] O Developer enviou o resultado e o Orchestrator o recebeu.
- [ ] O Orchestrator avaliou todos os critérios.
- [ ] O Trello confirmou a descrição preservada com evidências e a lista Concluído.
- [ ] Nenhum commit ou push foi realizado pelo tutorial.

<a id="criar-e-retomar-sessoes"></a>

## Criar e retomar sessões

Use `--new` apenas na primeira criação. Depois que a instância anterior daquele workstream encerrar, retome no mesmo checkout, com uma instância ativa por workstream.

Terminal Orchestrator:

```bash
cd "$HOME/dev-orchestra-demo"
ai-memory run --workstream orchestrator codex --remote ws://127.0.0.1:4500
```

Terminal Developer:

```bash
cd "$HOME/dev-orchestra-demo"
ai-memory run --workstream developer codex --remote ws://127.0.0.1:4500
```

O App Server deve estar disponível no mesmo endpoint. As opções AI Memory ficam antes de `codex`; `--remote` fica depois. No caminho gerenciado, não acrescente `resume <UUID>`: o AI Memory seleciona a sessão vinculada. `/rename` altera o título Codex, não a chave do workstream. Se a chave é `developer`, usar `--workstream desenvolvedor` resulta em `managed workstream not found`, mesmo que esse seja o título da sessão.

Para verificar os vínculos, no shell:

```bash
ai-memory workstreams --json
```

`linked_harnesses` contendo `codex` comprova um vínculo, não a retomada completa ou memória durável. Teste **continuidade da sessão** registrando um marcador único na conversa e seu UUID por `/status`, encerrando a instância, retomando com `--workstream` e conferindo o mesmo UUID e a recuperação do marcador.

Teste **conhecimento durável** separadamente: registre uma decisão útil pelo MCP AI Memory com fonte e escopo do projeto; em uma sessão independente, peça uma busca explícita pelo MCP e confira a decisão, sua origem e relevância. Não use o marcador transitório como conhecimento durável. O [guia de instalação](docs/SETUP.md#verificar-memoria-e-continuidade) detalha as evidências; a [fonte oficial de workstreams](https://github.com/akitaonrails/ai-memory/blob/main/docs/managed-workstreams.md) explica a seleção gerenciada.

## Expandir e adotar em um projeto existente

Depois do primeiro fluxo, adicione QA ou Planner quando houver necessidade. Crie workstreams separados `qa` e `planner`, um terminal por sessão, e carregue respectivamente [roles/qa.md](roles/qa.md) ou [roles/planner.md](roles/planner.md). O [workflow](docs/WORKFLOW.md#papeis-opcionais) traz os comandos e o retorno esperado. **Tester** é um exemplo de especialização: `roles/tester.md` não existe. Para usá-lo, escreva seu próprio contrato com missão, entrada, escopo, evidências e retorno antes de iniciar a sessão.

Trello é um exemplo de Task Source. Você pode conectar outro serviço via MCP existente ou criar um conector MCP próprio, definindo operações e estados conforme [TASK-SOURCE](docs/TASK-SOURCE.md). Isso não significa que este repositório já implemente adapters para outros serviços.

Em um projeto existente, leia seu `AGENTS.md` e concilie estas regras com as convenções locais, sem sobrescrevê-lo. Incorpore os papéis, protocolo e templates necessários, adapte o board e os critérios ao projeto, e verifique `git status` antes de editar. Para execução paralela, combine branches/worktrees e a integração dos resultados. Use o [workflow de adoção](docs/WORKFLOW.md#adotar-em-outro-repositorio).

## Mapa de documentos

| Documento | Quando consultar |
| --- | --- |
| [SETUP](docs/SETUP.md) | Instalação Ubuntu, configuração, verificações e diagnóstico |
| [AGENTS.md](AGENTS.md) | Contrato global vigente |
| [Papéis](roles/) | Responsabilidade de cada sessão |
| [WORKFLOW](docs/WORKFLOW.md) | Ciclo operacional e expansão |
| [TASK-SOURCE](docs/TASK-SOURCE.md) | Backlog, Trello e outros conectores |
| [PROTOCOL](docs/PROTOCOL.md) | Handoffs, resultados e destinos |
| [Templates](templates/) | [Handoff](templates/handoff.md) e [resultado](templates/result.md) |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Componentes e limites |
| [CONTEXT-MODEL](docs/CONTEXT-MODEL.md) | Onde cada informação vive |
| [CODEX-QUEUE-POC](docs/CODEX-QUEUE-POC.md) | Diagnóstico do transporte e histórico local |
| [Validação DOCS-001](docs/DOCS-001-VALIDATION.md) | Verificações desta revisão e limites |

## Agradecimentos

Um agradecimento especial ao [Akita](https://github.com/akitaonrails) pelo [AI Memory](https://github.com/akitaonrails/ai-memory), uma peça importante da proposta do Dev Orchestra. Seu trabalho oferece a base para preservar conhecimento durável e apoiar a continuidade entre sessões, aspectos fundamentais para a colaboração que queremos construir.
