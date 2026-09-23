# Dev Orchestra

**Dev Orchestra** é uma arquitetura de trabalho para organizar desenvolvimento assistido por IA com múltiplas sessões especializadas, um backlog visual e memória persistente compartilhada.

Não é um framework multiagente fechado nem um novo runtime. A proposta é combinar ferramentas que já existem — como Codex, Claude Code, Trello, AI Memory e Git — com papéis e responsabilidades explícitos, mantendo o desenvolvedor no controle.

![Arquitetura do Dev Orchestra](docs/assets/dev-orchestra-architecture.webp)

## Por que isso existe?

A ideia nasceu de um problema simples em projetos pessoais: conforme o projeto crescia, bugs, melhorias, ideias e decisões começaram a se espalhar entre conversas diferentes com agentes de IA.

A primeira tentativa de organização foi bem física: **post-its no monitor**.

Funcionava para lembrar o que precisava ser feito, mas os agentes não tinham acesso àquela visão. Depois comecei a organizar essas tarefas no Trello e surgiu a pergunta:

> Se eu já tenho um backlog visual organizado, por que o agente que coordena o desenvolvimento não pode conversar diretamente com ele?

A partir daí nasceu a ideia do Dev Orchestra: usar um backlog visível como referência operacional, separar coordenação de execução e preservar conhecimento entre sessões sem depender do histórico de uma única CLI.

A proposta inicial é deliberadamente simples e de baixo custo:

- Trello como primeiro backlog visual;
- uma sessão de IA como Orchestrator;
- sessões separadas para implementação, testes e planejamento quando necessário;
- AI Memory como memória persistente entre sessões e CLIs;
- Git como registro do código, documentação e contratos.

A simplicidade é parte da arquitetura. Se algo não ajuda a **organizar tarefas, preservar contexto, separar responsabilidades ou melhorar a rastreabilidade**, provavelmente ainda não precisa fazer parte do Dev Orchestra.

## A ideia em 30 segundos

```text
                       Developer
                    supervisiona / decide
                            |
                            v
Task Source <-------> Orchestrator <-------> AI Memory
(Trello)              coordena                contexto durável
                           |
                    +------+------+
                    |             |
                    v             v
                Developer         QA
                 Session       Session
                    \             /
                     \-----------/
                       resultados

Git -> código, documentação, branches, commits e evidências
```

### Orchestrator

É a sessão responsável por coordenar o fluxo.

Ela:

- consulta o backlog;
- seleciona e prioriza trabalho dentro das regras definidas;
- entende dependências;
- reúne apenas o contexto necessário;
- cria handoffs;
- delega implementação, planejamento ou validação;
- recebe resultados;
- mantém o Task Source sincronizado;
- registra conhecimento durável quando necessário;
- pede decisão humana quando a tarefa exige julgamento de produto ou arquitetura.

No fluxo inicial, **o Orchestrator é o proprietário das alterações de estado do backlog**.

### Developer

Recebe uma tarefa delimitada e fica focado em execução.

Devolve ao Orchestrator:

- resumo do que foi feito;
- arquivos alterados;
- testes executados;
- branch/commit quando aplicável;
- riscos, limitações ou pendências;
- possíveis aprendizados que mereçam memória durável.

### QA

Recebe critérios de aceite e evidências da implementação.

Sua função é validar:

- comportamento esperado;
- regressões relevantes;
- critérios de aceite;
- evidências técnicas.

QA não conclui o card diretamente. Ele devolve o resultado para o Orchestrator.

### Planner

É opcional.

Pode ser usado quando uma tarefa precisa de:

- investigação;
- decomposição;
- análise de impacto;
- desenho técnico;
- identificação de dependências antes da implementação.

## Onde cada informação vive

| Informação | Fonte |
| --- | --- |
| Backlog, prioridade, status e critérios | Task Source |
| Decisões e conhecimento durável | AI Memory |
| Código, documentação e contratos | Git |
| Trabalho temporário da tarefa atual | Session Context |

Regra simples:

> **Task Source mostra o que precisa ser feito. AI Memory preserva o que aprendemos. Git registra o que construímos. A sessão mantém apenas o contexto necessário para executar o trabalho atual.**

## Requisitos

Para experimentar o fluxo inicial você precisa de:

- Git;
- Docker ou Podman para a instalação padrão do AI Memory;
- **AI Memory**;
- pelo menos uma CLI de desenvolvimento assistido por IA:
  - Codex CLI; ou
  - Claude Code;
- um Task Source:
  - inicialmente Trello;
- acesso MCP ao Task Source quando a plataforma utilizar MCP.

> [!IMPORTANT]
> O padrão operacional do Dev Orchestra é iniciar as sessões com **`ai-memory run`**.
>
> O AI Memory também pode funcionar com CLIs iniciadas diretamente quando hooks/MCP já estão configurados, mas `ai-memory run` é o caminho recomendado porque prepara o escopo do projeto, gerencia workstreams, permite continuidade entre harnesses e faz o auto-wiring das integrações suportadas.

## 1. Instale uma CLI de IA

Você pode usar apenas Codex, apenas Claude Code ou misturar as duas ferramentas.

### Codex CLI

Com npm:

```bash
npm install -g @openai/codex
```

Verifique:

```bash
codex --version
```

Documentação:

https://github.com/openai/codex

### Claude Code

No Linux/macOS:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Verifique:

```bash
claude --version
```

Documentação:

https://github.com/anthropics/claude-code

## 2. Instale o AI Memory

Projeto:

https://github.com/akitaonrails/ai-memory

O AI Memory fornece a camada persistente que permite:

- capturar contexto entre sessões;
- recuperar decisões e histórico;
- continuar trabalho entre ferramentas diferentes;
- compartilhar memória por projeto;
- manter linhas de trabalho independentes com workstreams.

### Instalação padrão com Docker

A instalação oficial usa um wrapper local e um servidor em container.

Consulte sempre o quick start do projeto para o procedimento atualizado:

https://github.com/akitaonrails/ai-memory#quick-start

Depois da instalação, valide:

```bash
ai-memory --help
```

Para o Dev Orchestra, o modo preferido de iniciar uma CLI é:

```bash
ai-memory run codex
```

ou:

```bash
ai-memory run claude
```

Na primeira execução de um harness suportado, o `ai-memory run` pode configurar automaticamente hooks e MCP necessários para captura e recuperação de memória.

### Workstreams e sessões paralelas

**Workstream não é um novo componente da arquitetura do Dev Orchestra.** É um recurso operacional do AI Memory.

Um workstream representa uma linha lógica de trabalho. Como um mesmo workstream aceita apenas um escritor ativo por vez, sessões simultâneas devem usar workstreams separados.

Para o primeiro teste:

```text
orchestrator
developer
qa
```

Todos continuam no mesmo projeto e compartilham conhecimento persistente, mas cada papel mantém sua própria linha de execução.

## 3. Configure o Trello como Task Source

O Trello é apenas o primeiro adapter do Dev Orchestra.

O conceito genérico é **Task Source**, portanto futuramente o mesmo fluxo pode usar Jira, GitHub Issues/Projects, Linear ou outra plataforma.

Para Trello, configure o MCP disponível para a sua CLI.

Endpoint usado pelo Trello MCP:

```text
https://mcp.trello.com/v1
```

### Codex

```bash
codex mcp add trello --url https://mcp.trello.com/v1
codex mcp list
```

### Claude Code

```bash
claude mcp add --transport http trello https://mcp.trello.com/v1
```

Conclua a autorização solicitada pela ferramenta.

No fluxo inicial:

- Orchestrator precisa consultar e atualizar o Task Source;
- Developer recebe seu escopo pelo handoff;
- QA recebe critérios e evidências pelo handoff;
- acesso direto de workers ao Task Source é opcional.

Essa restrição reduz alterações concorrentes no backlog.

## 4. Clone o Dev Orchestra

```bash
git clone https://github.com/ronaldomafra/dev-orchestra.git
cd dev-orchestra
```

O repositório contém os contratos que definem o fluxo:

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
│       └── dev-orchestra-architecture.webp
├── roles/
│   ├── orchestrator.md
│   ├── developer.md
│   ├── qa.md
│   └── planner.md
└── templates/
    ├── handoff.md
    └── result.md
```

Para um projeto real, a ideia é levar esses contratos para o repositório do projeto ou adaptar a mesma estrutura.

## 5. Primeira execução com três terminais

Abra três terminais no mesmo checkout.

### Terminal 1 — Orchestrator

Primeira criação:

```bash
ai-memory run --new orchestrator codex
```

Execuções seguintes:

```bash
ai-memory run --workstream orchestrator codex
```

Instrução inicial:

```text
Leia AGENTS.md e roles/orchestrator.md e assuma o papel de Orchestrator.
Consulte o Task Source configurado e apresente o backlog antes de executar qualquer tarefa.
```

### Terminal 2 — Developer

Primeira criação:

```bash
ai-memory run --new developer codex
```

Execuções seguintes:

```bash
ai-memory run --workstream developer codex
```

Instrução inicial:

```text
Leia AGENTS.md e roles/developer.md e assuma o papel de Developer.
Aguarde um handoff do Orchestrator antes de iniciar implementação.
```

### Terminal 3 — QA

Primeira criação:

```bash
ai-memory run --new qa codex
```

Execuções seguintes:

```bash
ai-memory run --workstream qa codex
```

Instrução inicial:

```text
Leia AGENTS.md e roles/qa.md e assuma o papel de QA.
Aguarde critérios de aceite e evidências antes de validar uma tarefa.
```

## 6. Troque Codex e Claude Code sem trocar o papel

O papel pertence ao Dev Orchestra, não à CLI.

Por exemplo, uma linha de trabalho criada no Codex pode ser retomada no Claude Code:

```bash
ai-memory run --workstream developer claude
```

O AI Memory gerencia a continuidade do workstream entre harnesses suportados.

Isso permite testar qual CLI funciona melhor para cada tipo de trabalho sem transformar o histórico proprietário de uma ferramenta na única fonte de contexto do projeto.

## 7. Teste o fluxo manual antes de automatizar

Comece com um card pequeno e verificável.

Exemplo:

```text
Título: Criar endpoint de health check

Critérios:
- GET /health
- retornar HTTP 200
- resposta deve indicar status UP
```

Fluxo esperado:

```text
Backlog
   |
   v
Orchestrator
   |
   | handoff
   v
Developer
   |
   | resultado + evidências
   v
Orchestrator
   |
   | critérios + evidências
   v
QA
   |
   | resultado
   v
Orchestrator
   |
   v
Task Source atualizado
```

Use:

- `templates/handoff.md` para delegação;
- `templates/result.md` para retorno.

O primeiro objetivo **não é automatizar a comunicação entre terminais**.

Primeiro valide se:

- separar papéis reduz confusão;
- o backlog permanece coerente;
- o Orchestrator não acumula contexto técnico demais;
- o AI Memory recupera contexto útil entre sessões;
- trocar de CLI mantém a continuidade esperada;
- os handoffs possuem informação suficiente sem copiar conversas inteiras.

## AGENTS.md

`AGENTS.md` é o contrato global do Dev Orchestra.

Os papéis específicos ficam em:

```text
roles/orchestrator.md
roles/developer.md
roles/qa.md
roles/planner.md
```

O repositório intencionalmente evita duplicar regras globais em arquivos específicos de fornecedor.

Ao usar uma CLI, confirme que a versão/configuração instalada carrega `AGENTS.md` como instrução de projeto.

## Fluxo básico do backlog

Uma configuração simples pode usar:

```text
Backlog -> Em execução -> Em teste -> Concluído
```

Exemplo:

1. Orchestrator lê o backlog.
2. Seleciona um card e move para **Em execução**.
3. Cria o handoff para Developer.
4. Developer implementa e devolve o resultado.
5. Orchestrator move o card para **Em teste**.
6. QA valida.
7. Se aprovado, Orchestrator move para **Concluído**.
8. Se houver falha, Orchestrator devolve para **Em execução** anexando a evidência.

Os nomes das colunas são configuráveis. O importante é existir um mapeamento claro entre estado real e estado visível.

## Princípio de simplicidade

A primeira versão começa manualmente de propósito.

Não precisamos inicialmente de:

- servidor próprio de orquestração;
- fila de mensagens;
- banco de dados adicional;
- dashboard próprio;
- runtime multiagente customizado;
- delegação automática completa.

Primeiro usamos:

```text
Task Source + Orchestrator + Workers + AI Memory + Git
```

Automação deve ser adicionada depois que o uso real revelar onde ela gera valor.

## Documentação

- [`AGENTS.md`](AGENTS.md) — regras globais.
- [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — fluxo operacional.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — visão arquitetural.
- [`docs/CONTEXT-MODEL.md`](docs/CONTEXT-MODEL.md) — separação de contexto.
- [`docs/TASK-SOURCE.md`](docs/TASK-SOURCE.md) — abstração do backlog.
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — handoff entre papéis.

## Estado atual

O Dev Orchestra está na fase de validação do fluxo manual:

```text
Task Source -> Orchestrator -> Developer -> QA -> Orchestrator -> Task Source
```

A próxima etapa é usar essa arquitetura em projetos reais, observar os pontos de atrito e automatizar somente aquilo que se provar repetitivo.
