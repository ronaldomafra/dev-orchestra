# Instalação e configuração — Linux/Ubuntu

Este guia prepara as ferramentas usadas pelo Dev Orchestra. Os comandos são executados por você. Não existe um instalador próprio do projeto. A sequência é: dependências, Codex, checkout, AI Memory, Trello e só então App Server e sessões. O [Quick Start](../README.md#quick-start-um-card-dois-agentes) contém o card e os prompts do primeiro fluxo; todas as etapas de instalação estão aqui.

## Requisitos

Use Ubuntu com Bash, acesso administrativo para instalar dependências, navegador para login, uma conta com acesso ao Codex e uma conta Trello com acesso de leitura e escrita ao board escolhido. O Docker precisa funcionar com o mesmo usuário que executará `ai-memory`; não execute o Codex como root para contornar permissões.

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
git --version
curl --version
```

## Docker

Se `docker info` já funcionar com seu usuário, reutilize a instalação. Para uma instalação nova no Ubuntu, siga os comandos abaixo, baseados no [guia oficial Docker](https://docs.docker.com/engine/install/ubuntu/). Se já houver pacotes Docker de outra origem, consulte primeiro a seção de conflitos desse guia; não misture instalações.

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl status docker
```

Se o serviço instalado estiver parado, inicie-o:

```bash
sudo systemctl start docker
```

O wrapper AI Memory precisa de acesso ao Docker sem `sudo`. Para uma estação pessoal, a [pós-instalação oficial](https://docs.docker.com/engine/install/linux-postinstall/) apresenta o grupo `docker` (que concede privilégios equivalentes a root) e alternativas. Se escolher esse grupo:

```bash
sudo usermod -aG docker "$USER"
```

Saia da sessão do sistema e entre novamente para aplicar o grupo em todos os terminais. Então verifique:

```bash
docker info
docker run --rm hello-world
```

O primeiro deve mostrar acesso ao daemon; o segundo deve imprimir a mensagem de sucesso do container. Não prossiga com o wrapper enquanto houver `permission denied` no socket.

## Codex

Instale pelo método Linux da [documentação oficial](https://learn.chatgpt.com/docs/codex/cli):

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
codex --version
codex login
codex login status
```

Complete o login no navegador. Para tornar o PATH persistente no Bash, adicione `export PATH="$HOME/.local/bin:$PATH"` ao seu `~/.bashrc` se ainda não estiver presente e abra um novo terminal. Confira as capacidades da CLI instalada:

```bash
codex queue --help
codex app-server --help
codex mcp add --help
codex mcp login --help
```

O caminho documentado exige `queue --thread --message --remote` e `app-server --listen`. A versão consultada nesta revisão foi `0.156.1`; o App Server é marcado experimental. Não há versão mínima demonstrada pelo projeto. Se as opções não existirem, confira a versão e a documentação antes de continuar.

## Checkout de demonstração

```bash
git clone https://github.com/ronaldomafra/dev-orchestra.git "$HOME/dev-orchestra-demo"
cd "$HOME/dev-orchestra-demo"
git status --short
```

Use um diretório novo ou confira um checkout já existente. Todas as sessões do tutorial usam esse mesmo caminho absoluto. Não copie a tarefa para outro checkout sem ajustar também as sessões e o escopo de memória.

## AI Memory

O procedimento abaixo adapta o [Quick Start oficial](https://github.com/akitaonrails/ai-memory#quick-start) e o [guia de instalação](https://github.com/akitaonrails/ai-memory/blob/main/docs/install.md) para Codex local. Conclua-o antes de iniciar o App Server.

### Wrapper com checksum

O wrapper executa componentes do AI Memory em container e gerencia a integração com o host. Instale-o no seu usuário. O bloco roda em um subshell para que a falha de checksum não encerre o terminal:

```bash
(
    set -eu
    mkdir -p "$HOME/.local/bin"
    ai_memory_tmp="$(mktemp -d)"
    trap 'rm -rf "$ai_memory_tmp"' EXIT
    ai_memory_release=https://github.com/akitaonrails/ai-memory/releases/latest/download/ai-memory-wrapper
    curl -fsSL "$ai_memory_release" -o "$ai_memory_tmp/ai-memory-wrapper"
    curl -fsSL "$ai_memory_release.sha256" -o "$ai_memory_tmp/ai-memory-wrapper.sha256"
    ai_memory_expected="$(awk 'NR == 1 { print $1 }' "$ai_memory_tmp/ai-memory-wrapper.sha256")"
    ai_memory_actual="$(sha256sum "$ai_memory_tmp/ai-memory-wrapper" | awk '{ print $1 }')"
    [ -n "$ai_memory_expected" ] && [ "$ai_memory_actual" = "$ai_memory_expected" ] || {
        echo 'Checksum do wrapper AI Memory não confere' >&2
        exit 1
    }
    install -m 0755 "$ai_memory_tmp/ai-memory-wrapper" "$HOME/.local/bin/ai-memory"
)
export PATH="$HOME/.local/bin:$PATH"
command -v ai-memory
```

Só continue se o bloco terminar com sucesso. Se já houver uma instalação, revise sua origem antes de substituir o executável. Mantenha `~/.local/bin` no PATH também nos terminais das sessões.

### Servidor local persistente

Confira primeiro se já existe um container/servidor para reutilizar:

```bash
docker ps -a --filter name=ai-memory
curl -i http://127.0.0.1:49374/mcp
```

Um erro JSON-RPC na consulta simples de `/mcp` pode indicar que o servidor está alcançável; não é uma consulta MCP completa nem prova de memória recuperada. Se não houver servidor ou container existente, crie:

```bash
docker run -d --name ai-memory \
    --restart unless-stopped \
    -p 127.0.0.1:49374:49374 \
    -v ai-memory-data:/data \
    docker.io/akitaonrails/ai-memory:latest
```

Se o container `ai-memory` já existir e estiver parado, confira sua configuração e use `docker start ai-memory`, em vez de criar outro. O volume `ai-memory-data` preserva os dados; o bind em localhost mantém o caminho local. O modo inicial não configura chaves de LLM ou embeddings: busca textual e captura básica são suficientes para o tutorial. Não remova o volume para resolver problemas de instalação.

### Hooks e MCP para Codex

No shell do mesmo usuário, com o servidor disponível:

```bash
ai-memory install-mcp --client codex --apply
ai-memory install-hooks --agent codex --apply
codex mcp list
ai-memory run --help
```

Os comandos usam o servidor local padrão `http://127.0.0.1:49374`. Se seu ambiente já define `AI_MEMORY_SERVER_URL`, confirme o destino antes de instalar. Para instalação local não é necessário um token AI Memory; configurações remotas/autenticadas estão no guia oficial. O MCP usa `/mcp`; hooks usam a origem sem esse sufixo.

Os instaladores oficiais preservam configurações alheias e geram backups; revise os resultados e eventuais solicitações de confiança em hooks no Codex. Embora `ai-memory run` tenha autoconfiguração, aqui a instalação é explícita para que o App Server já inicie com hooks/MCP disponíveis. Falha na instalação não deve ser interpretada como integração concluída.

A política do Dev Orchestra promove decisões e convenções à memória durável. A captura técnica do AI Memory pode registrar prompts, ferramentas e eventos de ciclo de vida conforme sua configuração. Consulte o [modelo de contexto](CONTEXT-MODEL.md) e as [regras de captura da ferramenta](https://github.com/akitaonrails/ai-memory/blob/main/DATA_HANDLING.md); não confunda essa política com uma promessa de ausência de logs.

## Trello

No navegador, entre no Trello e escolha o workspace do projeto. Cadastre no Codex o [endpoint oficial Trello MCP](https://support.atlassian.com/trello/docs/connect-trello-to-ai-assistants-with-trello-mcp/):

```bash
codex mcp add trello --url https://mcp.trello.com/v1
```

Conclua o OAuth escolhendo o workspace e concedendo as permissões necessárias de leitura, busca e escrita. Se o login não tiver sido concluído ao adicionar:

```bash
codex mcp login trello
```

Confira a configuração:

```bash
codex mcp list
```

No Trello, crie o board, as listas **Backlog**, **Em execução**, **Concluído** e o card descrito no [Quick Start](../README.md#3-criar-a-tarefa-no-trello). Guarde suas URLs reais. O cadastro MCP não comprova autorização: quando a sessão Orchestrator abrir, peça a leitura do board e do card, conferindo nome, lista e critérios retornados. Conta/workspace incorretos ou políticas administrativas podem impedir o acesso.

O tutorial usa leitura, atualização e movimentação de cards. Para registrar evidências, atualize a **descrição**, preservando os critérios. Comentários e anexos constam como recursos futuros na fonte consultada. O [guia Task Source](TASK-SOURCE.md) explica como substituir o serviço.

## App Server e sessões

Antes de iniciar, confirme Git, Docker, Codex autenticado, hooks instalados e ambos os MCPs registrados. Verifique o serviço existente:

```bash
curl -i http://127.0.0.1:4500/readyz
curl -i http://127.0.0.1:4500/healthz
```

Se ambos responderem HTTP 200 no App Server esperado, reutilize-o. Se não houver serviço ativo, use um terminal dedicado na raiz do checkout:

```bash
cd "$HOME/dev-orchestra-demo"
codex app-server --listen ws://127.0.0.1:4500
```

No terminal que usará para o Orchestrator, repita os dois `curl` antes de abrir a sessão. Ambos devem responder HTTP 200. Não inicie outra instância quando já houver um serviço respondendo. Se a configuração de um servidor existente anteceder a instalação MCP/hooks, coordene seu encerramento/reabertura com as sessões que o utilizam.

Primeira criação, terminal Orchestrator:

```bash
cd "$HOME/dev-orchestra-demo"
ai-memory run --new orchestrator codex --remote ws://127.0.0.1:4500
```

Primeira criação, terminal Developer:

```bash
cd "$HOME/dev-orchestra-demo"
ai-memory run --new developer codex --remote ws://127.0.0.1:4500
```

Dentro das sessões, use `/rename orchestrator` e `/rename developer` respectivamente; carregue `AGENTS.md`, o papel correspondente e `docs/PROTOCOL.md`. Os [prompts do Quick Start](../README.md#5-terminal-do-orchestrator) já incluem espera e canal de retorno. Use `/status` para copiar o Session UUID de cada sessão e confira o destino antes de enviar mensagens.

Para workstreams existentes, siga os [comandos de retomada](../README.md#criar-e-retomar-sessoes): `--workstream` com a chave original e uma instância ativa por chave. `/rename` não renomeia workstreams; não adicione `resume <UUID>` ao comando gerenciado.

<a id="verificar-memoria-e-continuidade"></a>

## Verificar memória e continuidade

São verificações diferentes, que devem ser registradas separadamente:

| Verificação | Evidência necessária |
| --- | --- |
| Configuração | MCP listado e instalação dos hooks concluída |
| Associação | `ai-memory workstreams --json` com `linked_harnesses` contendo `codex` |
| Continuidade | Mesmo Session UUID e marcador da conversa recuperado após encerrar/retomar |
| Conhecimento durável | Decisão útil registrada e recuperada em sessão independente por consulta MCP, com fonte e escopo |
| Fluxo completo | Card real lido, handoff recebido, resultado devolvido e atualização Trello confirmada |

Para continuidade, envie na sessão: “Registre apenas nesta conversa o marcador CONTINUIDADE-DEMO-001; não o promova a memória durável”. Anote o UUID via `/status`, encerre a sessão e aguarde o launcher terminar. Retome com o `--workstream` correspondente; confira `/status` e peça o marcador. Repita separadamente para cada papel. Se o UUID mudar ou o marcador não for recuperado, registre a retomada como não validada.

Para memória durável, peça explicitamente ao agente para registrar pelo MCP AI Memory uma convenção real do projeto, com origem em um contrato vigente. Exemplo: “Registre a decisão de que evidências de DEMO-001 ficam na descrição do card, preservando critérios, conforme README.md; associe ao projeto atual e informe a confirmação da ferramenta”. Em outra sessão independente do mesmo projeto, peça: “Consulte pelo MCP AI Memory a decisão sobre evidências de DEMO-001; informe a origem e compare com o contrato atual”. Exija o retorno real da ferramenta; repetir o texto do README ou lembrar da conversa não demonstra recuperação da memória. Conteúdo recuperado continua sendo histórico sujeito às instruções atuais.

<a id="diagnostico"></a>

## Diagnóstico

| Sintoma | Verificação e próximo passo |
| --- | --- |
| `codex` ou `ai-memory` não encontrado | Confira `command -v`, PATH e instalação no usuário dos terminais |
| `permission denied` no socket Docker | Confira `docker info` como esse usuário; resolva o acesso conforme a instalação Docker e reabra a sessão do sistema |
| Wrapper/checksum falhou | Interrompa a instalação; baixe novamente wrapper e checksum da mesma release e confira a origem |
| Porta 49374 ocupada ou container já existe | Confira container/configuração existente e reutilize o servidor apropriado; não crie outro nem apague dados |
| MCP listado mas board inacessível | Confira OAuth, conta, workspace, permissões e políticas; reporte a indisponibilidade |
| MCP/hooks ausentes na sessão | Confira instalação anterior ao App Server, usuário e configuração; coordene reabertura se necessário |
| App Server sem HTTP 200 | Confira terminal do servidor, endpoint e erro; não crie uma segunda instância na porta ocupada |
| Nome de destino não resolve | Use `/status` na sessão de destino e informe o UUID confirmado; não tente UUIDs aleatórios |
| `managed workstream not found` | Liste `ai-memory workstreams --json` no mesmo checkout; use a chave original, não o título `/rename` |
| Retomada falha com `resume UUID` | Remova esse complemento do caminho gerenciado; use o comando `--workstream` documentado |
| Fila aceita, mas não há resultado | Aceitação só confirma enfileiramento; Orchestrator aguarda pelo canal, sem polling |
| Sandbox falha no Ubuntu | Consulte o [procedimento local documentado](CODEX-QUEUE-POC.md#se-o-sandbox-falhar-no-ubuntu-2404) se o erro for o mesmo |

O [POC do transporte](CODEX-QUEUE-POC.md) é um diagnóstico opcional. Não é pré-requisito para criar terminais adicionais no primeiro fluxo. Se uma integração falhar, informe exatamente o que não foi confirmado; nunca afirme que o card foi atualizado sem resposta real.

## Fontes e alcance da verificação

Fontes oficiais consultadas em 2026-09-23: [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), [Docker Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [AI Memory Quick Start](https://github.com/akitaonrails/ai-memory#quick-start), [instalação AI Memory](https://github.com/akitaonrails/ai-memory/blob/main/docs/install.md), [workstreams](https://github.com/akitaonrails/ai-memory/blob/main/docs/managed-workstreams.md) e [Trello MCP](https://support.atlassian.com/trello/docs/connect-trello-to-ai-assistants-with-trello-mcp/).

Os comandos de instalação e o fluxo runtime não foram executados nesta revisão documental. Consulte o [registro de validação](DOCS-001-VALIDATION.md) para distinguir leitura de fontes, verificações estáticas e evidências históricas.
