# Dev Orchestra

**Dev Orchestra** é uma forma simples de organizar desenvolvimento assistido por IA usando múltiplas sessões com papéis claros, um backlog visual e memória compartilhada.

Não é um framework multiagente fechado, nem um serviço novo. A proposta é organizar melhor ferramentas que já existem — Codex, Claude Code, Trello, AI Memory e Git — mantendo o desenvolvedor no controle.

![Arquitetura do Dev Orchestra](docs/assets/dev-orchestra-architecture.svg)

## Por que isso existe?

A ideia nasceu de um problema bem comum em projetos pessoais: bugs, melhorias, ideias e decisões começaram a crescer mais rápido do que eu conseguia organizar dentro das próprias conversas com agentes de IA.

A primeira solução foi simples: **post-its no monitor**.

Eles ajudavam a lembrar o que precisava ser feito, mas não serviam como uma fonte de contexto para os agentes. Depois comecei a organizar o trabalho no Trello e surgiu uma pergunta:

> Se eu já tenho um backlog visual organizado, por que o agente que coordena o desenvolvimento não pode conversar diretamente com ele?

Foi daí que nasceu o Dev Orchestra.

A ideia é manter uma solução simples, barata e fácil de observar:

- Trello como backlog visual;
- uma sessão de IA coordenando o fluxo;
- sessões separadas para implementação e testes;
- AI Memory preservando contexto entre sessões e entre CLIs diferentes;
- Git mantendo código, documentação e histórico versionados.

A simplicidade é parte da arquitetura. Se algo não ajuda diretamente a **organizar tarefas, preservar contexto ou separar responsabilidades**, provavelmente não precisa fazer parte do Dev Orchestra.

## A ideia em 30 segundos

```text
             Task Source
          Trello inicialmente
                  |
                  v
            Orchestrator
              /      \
             v        v
        Developer     QA
             \        /
              resultados

AI Memory -> memória compartilhada entre sessões e CLIs
Git       -> código, documentação e histórico
```

### Orchestrator

É a sessão que coordena o trabalho.

Ela:

- consulta o backlog;
- escolhe o próximo card;
- entende dependências;
- monta o handoff;
- delega implementação;
- envia o resultado para validação;
- recebe evidências;
- mantém o Task Source sincronizado;
- registra conhecimento durável quando necessário.

No fluxo inicial, **o Orchestrator é o dono das alterações de estado no backlog**.

### Developer

Recebe uma tarefa delimitada, implementa e devolve:

- resumo do que foi feito;
- arquivos alterados;
- testes executados;
- branch/commit quando aplicável;
- riscos ou observações relevantes.

O Developer não precisa administrar o backlog.

### QA

Recebe os critérios de aceite e a implementação produzida pelo Developer.

Valida o resultado, testa regressões relevantes e devolve evidências ao Orchestrator.

O QA também não precisa mover cards no Trello.

## Fluxo básico

Um fluxo simples pode usar estas colunas:

```text
Backlog -> Em execução -> Em teste -> Concluído
```

Os nomes das colunas não são importantes. O importante é o Orchestrator manter o quadro sincronizado com o estado real do trabalho.

Exemplo:

1. Orchestrator lê o backlog.
2. Escolhe um card e move para **Em execução**.
3. Envia a tarefa para o Developer.
4. Developer implementa e devolve o resultado.
5. Orchestrator move o card para **Em teste**.
6. QA valida a implementação.
7. Se aprovado, Orchestrator move para **Concluído**.
8. Se houver falha, Orchestrator devolve para **Em execução** com a evidência do QA.

## Requisitos

Para o fluxo inicial você precisa de:

- Git;
- **AI Memory**;
- pelo menos uma CLI de desenvolvimento com IA:
  - Codex CLI; ou
  - Claude Code;
- um Task Source acessível por MCP ou outro adapter:
  - inicialmente Trello + Trello MCP.

> [!IMPORTANT]
> **Todas as sessões do Dev Orchestra devem ser iniciadas através do AI Memory.**
>
> Abrir `codex` ou `claude` diretamente pode funcionar tecnicamente, mas quebra a camada de memória compartilhada e continuidade entre sessões que faz parte do modelo do Dev Orchestra.

## 1. Instale uma CLI de IA

Você pode usar apenas Codex, apenas Claude Code ou misturar os dois.

### Codex CLI

Instalação recomendada no Linux/macOS:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Alternativamente:

```bash
npm install -g @openai/codex
```

Verifique:

```bash
codex --version
```

Documentação oficial: https://github.com/openai/codex

### Claude Code

Instalação recomendada no Linux/macOS:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Verifique:

```bash
claude --version
```

Documentação oficial: https://github.com/anthropics/claude-code

## 2. Instale e configure o AI Memory

Projeto oficial:

https://github.com/akitaonrails/ai-memory

O AI Memory é a camada que permite:

- preservar memória entre sessões;
- compartilhar contexto entre Codex e Claude Code;
- recuperar handoffs;
- manter linhas de trabalho independentes através de **workstreams**.

Verifique a instalação:

```bash
ai-memory --help
```

O modo recomendado para iniciar uma CLI é:

```bash
ai-memory run codex
```

ou:

```bash
ai-memory run claude
```

Na primeira execução de uma CLI, o `ai-memory run` pode configurar automaticamente hooks e MCP necessários para captura e recuperação da memória.

### Por que usamos workstreams separados?

O AI Memory permite uma sessão escritora ativa por workstream. Como queremos Orchestrator, Developer e QA funcionando ao mesmo tempo, cada papel recebe seu próprio workstream:

```text
orchestrator
developer
qa
```

Eles continuam pertencendo ao mesmo projeto e compartilham a camada de memória, mas não disputam o mesmo estado de sessão.

## 3. Configure o Trello MCP

Se o Trello for o seu Task Source, use o servidor MCP oficial:

```text
https://mcp.trello.com/v1
```

Segundo a documentação do Trello, o MCP pode ser usado com contas Trello em qualquer plano. A autorização é feita por OAuth e, atualmente, cada conexão escolhe um workspace.

### Codex

```bash
codex mcp add trello --url https://mcp.trello.com/v1
```

Verifique:

```bash
codex mcp list
```

### Claude Code

```bash
claude mcp add --transport http trello https://mcp.trello.com/v1
```

Na primeira conexão, conclua a autorização no navegador e selecione o workspace que poderá ser acessado.

No modelo inicial do Dev Orchestra, **somente o Orchestrator precisa obrigatoriamente de acesso de escrita ao Task Source**. Developer e QA recebem o contexto necessário pelo handoff.

## 4. Clone o Dev Orchestra

```bash
git clone https://github.com/ronaldomafra/dev-orchestra.git
cd dev-orchestra
```

Para o primeiro teste, você pode executar as três sessões diretamente neste repositório.

Depois, a mesma estrutura pode ser aplicada a um projeto real, mantendo:

- `AGENTS.md`;
- `roles/`;
- `templates/`;
- as regras de Task Source e memória.

## 5. Primeira execução com três terminais

Abra três terminais no mesmo repositório.

### Terminal 1 — Orchestrator

Na primeira vez:

```bash
ai-memory run --new orchestrator codex
```

Depois:

```bash
ai-memory run --workstream orchestrator codex
```

Primeira instrução:

```text
Leia AGENTS.md e roles/orchestrator.md e assuma o papel de Orchestrator.
Consulte o Task Source configurado e apresente o backlog antes de executar qualquer tarefa.
```

### Terminal 2 — Developer

Na primeira vez:

```bash
ai-memory run --new developer codex
```

Depois:

```bash
ai-memory run --workstream developer codex
```

Primeira instrução:

```text
Leia AGENTS.md e roles/developer.md e assuma o papel de Developer.
Aguarde um handoff do Orchestrator antes de iniciar implementação.
```

### Terminal 3 — QA

Na primeira vez:

```bash
ai-memory run --new qa codex
```

Depois:

```bash
ai-memory run --workstream qa codex
```

Primeira instrução:

```text
Leia AGENTS.md e roles/qa.md e assuma o papel de QA.
Aguarde critérios de aceite e evidências antes de validar uma tarefa.
```

### Usando Claude Code

O papel pertence ao Dev Orchestra, não à CLI.

Você pode trocar qualquer sessão:

```bash
ai-memory run --workstream developer claude
```

Assim, por exemplo, o Orchestrator pode continuar no Codex enquanto o Developer passa para Claude Code sem abandonar o workstream e o contexto durável.

## 6. Teste o fluxo manual antes de automatizar

Comece com um card simples no Trello.

Exemplo:

```text
Título: Criar endpoint de health check

Critérios:
- GET /health
- retornar HTTP 200
- resposta deve indicar status UP
```

No terminal do Orchestrator, peça para selecionar esse card e criar um handoff.

O handoff deve seguir:

```text
templates/handoff.md
```

O Developer executa e devolve:

```text
templates/result.md
```

Depois o Orchestrator encaminha os critérios e evidências para o QA.

O objetivo do primeiro teste não é automatizar a comunicação entre terminais. É validar se a **separação dos papéis, do backlog e da memória realmente melhora o fluxo**.

## AGENTS.md

`AGENTS.md` é a fonte única das regras globais do projeto.

Os papéis específicos ficam em:

```text
roles/orchestrator.md
roles/developer.md
roles/qa.md
roles/planner.md
```

Isso permite trocar Codex por Claude Code sem duplicar as regras do projeto.

## Onde cada informação vive

| Informação | Local |
| --- | --- |
| Backlog, prioridade e estado da tarefa | Task Source / Trello |
| Decisões e conhecimento durável | AI Memory |
| Código e documentação | Git |
| Trabalho temporário da tarefa | Session Context |

Regra simples:

> **O Task Source mostra o que precisa ser feito. AI Memory preserva o que aprendemos. Git registra o que construímos.**

## Estrutura do repositório

```text
.
├── AGENTS.md
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CONTEXT-MODEL.md
│   ├── PROTOCOL.md
│   ├── TASK-SOURCE.md
│   ├── WORKFLOW.md
│   └── assets/
│       └── dev-orchestra-architecture.svg
├── roles/
│   ├── orchestrator.md
│   ├── developer.md
│   ├── qa.md
│   └── planner.md
└── templates/
    ├── handoff.md
    └── result.md
```

## Princípio de simplicidade

O Dev Orchestra começa manualmente de propósito.

Não precisamos inicialmente de:

- servidor próprio de orquestração;
- fila de mensagens;
- banco de dados adicional;
- dashboard próprio;
- runtime multiagente customizado;
- automação completa.

Primeiro usamos terminais separados, Task Source, AI Memory e Git.

Automação só deve ser adicionada quando um problema real aparecer repetidamente.

## Documentação

- [`AGENTS.md`](AGENTS.md) — regras globais.
- [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — fluxo operacional.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — visão arquitetural.
- [`docs/CONTEXT-MODEL.md`](docs/CONTEXT-MODEL.md) — separação de contexto.
- [`docs/TASK-SOURCE.md`](docs/TASK-SOURCE.md) — abstração do backlog.
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — handoff entre papéis.

## Estado atual

O Dev Orchestra está começando pelo fluxo manual:

```text
Task Source -> Orchestrator -> Developer -> QA -> Orchestrator -> Task Source
```

O objetivo agora é usar esse modelo em projetos reais, observar o que funciona e manter somente o que realmente melhora organização e produtividade.
