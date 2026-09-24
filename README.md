# Dev Orchestra

Dev Orchestra é uma proposta de processo para organizar desenvolvimento assistido por IA. Ela combina um backlog visual, sessões com papéis explícitos, um canal entre sessões, memória durável e Git.

O Orchestrator coordena o trabalho e mantém o backlog. Workers executam tarefas delimitadas e devolvem evidências. A pessoa desenvolvedora continua responsável por decisões críticas.

![Arquitetura do Dev Orchestra](docs/assets/dev-orchestra-architecture.webp)

## Por que existe

Em projetos pessoais, tarefas e decisões acabam espalhadas entre post-its, conversas e sessões diferentes de IA. Um backlog visual ajuda a organizar o que deve ser feito, mas não define como sessões independentes colaboram.

O Dev Orchestra conecta essas partes com uma esteira leve. Começa com ferramentas existentes, instruções claras e mensagens verificáveis; não exige um runtime multiagente próprio.

## A ideia em 30 segundos

    Task Source <-> Orchestrator <-> AI Memory
       backlog        coordena       conhecimento
                          |
                    canal entre sessões
                          |
                    +-----+-----+
                    |           |
                 Developer      QA
                  executa     valida
                               /
                     resultados

    Git registra código, documentação, contratos e evidências.

O Task Source é backlog e estado operacional. O canal entre sessões carrega handoffs e resultados. AI Memory guarda conhecimento durável. Git guarda artefatos versionados.

## Papéis

- **Orchestrator:** seleciona tarefas, reúne contexto mínimo, delega, consolida resultados e atualiza o Task Source.
- **Developer:** implementa a tarefa recebida e retorna mudanças, validações e riscos.
- **QA:** verifica critérios e evidências e devolve o resultado ao Orchestrator.
- **Planner:** opcional; investiga dependências e divide tarefas antes da implementação.
- **Pessoa desenvolvedora:** decide questões de produto e arquitetura que não podem ser inferidas com segurança.

As instruções completas estão em roles/.

## Esteira de trabalho

    Backlog -> Orchestrator -> handoff -> Worker -> resultado
                  ^                              |
                  +------------------------------+
                  |
                  +-> QA quando necessário -> Task Source atualizado

1. O Orchestrator seleciona uma tarefa disponível.
2. Envia objetivo, escopo, critérios e sessão de retorno ao worker.
3. O worker executa somente o escopo recebido.
4. O worker devolve resultado e evidências pelo mesmo canal.
5. O Orchestrator consolida o resultado, chama QA quando necessário e atualiza o backlog.

Depois de delegar, o Orchestrator aguarda a resposta pelo canal. Não faz polling nem usa arquivos para deduzir que a tarefa terminou.

## Onde cada informação vive

| Informação | Fonte |
| --- | --- |
| Backlog, prioridade e status | Task Source |
| Decisões e conhecimento reutilizável | AI Memory |
| Código, contratos e documentação | Git |
| Contexto transitório da execução | Sessão atual |

## Estado da proposta

O App Server Codex iniciou e seus endpoints de saúde responderam HTTP 200. No Ubuntu, o sandbox também foi validado com `codex sandbox /bin/true` após habilitar o perfil AppArmor necessário; os passos estão em docs/CODEX-QUEUE-POC.md. O canal entre sessões via `codex app-server` e `codex queue` foi confirmado nos testes COMM-001, COMM-002 e COMM-003: o worker recebeu a tarefa e devolveu resultado ao Orchestrator sem alterar arquivos. No COMM-003, o envio pelo título `desenvolvedor-novo` falhou; o Orchestrator identificou a sessão e enviou pelo UUID, com sucesso.

O workstream `developer` apareceu com harness `codex` em `ai-memory workstreams --json`; a retomada desse workstream deve ser exercitada seguindo as instruções abaixo. Um workstream separado, `developer_new`, apareceu sem harness vinculado, portanto não deve ser usado como evidência de continuidade. A integração completa com Task Source e QA ainda não foi validada. Consulte docs/CODEX-QUEUE-POC.md para reproduzir o teste de comunicação.

## Começar

1. Leia AGENTS.md e docs/WORKFLOW.md.
2. Valide ou reproduza a comunicação nativa entre sessões seguindo docs/CODEX-QUEUE-POC.md.
3. Crie e retome as sessões com AI Memory conforme as instruções abaixo.
4. Use templates/handoff.md e templates/result.md para trocar tarefas e resultados.

Nesta etapa, valide Codex e AI Memory sem configurar Trello. A integração com o Task Source e o fluxo com QA ficam para uma fase posterior.

AI Memory é a opção recomendada para memória persistente. Consulte o [guia de workstreams do AI Memory](https://github.com/akitaonrails/ai-memory/blob/main/docs/managed-workstreams.md) para instalação e comandos atuais.

### Criar e retomar sessões Codex com AI Memory

Execute os comandos da raiz do repositório. Se o App Server ainda não estiver ativo, inicie-o em um terminal dedicado:

```bash
codex app-server --listen ws://127.0.0.1:4500
```

Mantenha o App Server aberto em um terminal. Abra outro terminal para cada sessão, sempre a partir da raiz deste repositório. `--new` cria um workstream independente; use-o uma única vez para cada linha de trabalho. Os comandos iniciam uma sessão interativa e permanecem em primeiro plano. Se o workstream já existir, pule a criação e use o comando de retomada abaixo.

Orchestrator:

```bash
ai-memory run --new orchestrator codex --remote ws://127.0.0.1:4500
```

Developer:

```bash
ai-memory run --new developer codex --remote ws://127.0.0.1:4500
```

Na primeira abertura de cada sessão, defina o título da thread com `/rename` e, em seguida, envie a instrução do papel. Esses comandos são digitados dentro da sessão Codex, não no shell.

No Orchestrator:

```text
/rename orchestrator
```

Depois, envie:

```text
Leia AGENTS.md e roles/orchestrator.md e assuma o papel de Orchestrator.
```

No Developer:

```text
/rename desenvolvedor
```

Depois, envie:

```text
Leia AGENTS.md, roles/developer.md e docs/PROTOCOL.md. Assuma o papel de Developer e aguarde um handoff do Orchestrator.
```

Quando encerrar uma sessão e quiser retomá-la depois, inicie `ai-memory run` novamente usando `--workstream` com a chave original. Faça isso somente depois que a execução anterior daquele workstream tiver terminado.

Retomar o Orchestrator:

```bash
ai-memory run --workstream orchestrator codex --remote ws://127.0.0.1:4500
```

Retomar o Developer:

```bash
ai-memory run --workstream developer codex --remote ws://127.0.0.1:4500
```

O AI Memory retoma a sessão Codex vinculada ao workstream; não acrescente `resume <UUID>`. O nome de `--workstream` é a chave do AI Memory, não o título da thread definido com `/rename`. Como o workstream foi criado como `developer` e a thread se chama `desenvolvedor`, retome com `--workstream developer`. Usar `--workstream desenvolvedor` causa erro `managed workstream not found`.

Só crie outro workstream de Developer quando precisar de uma linha de trabalho independente. Use `--new` uma vez na criação:

```bash
ai-memory run --new developer-2 codex --remote ws://127.0.0.1:4500
```

Em execuções futuras, retome essa mesma linha assim:

```bash
ai-memory run --workstream developer-2 codex --remote ws://127.0.0.1:4500
```

Confira workstreams e harnesses associados com:

```bash
ai-memory workstreams --json
```

O campo `linked_harnesses` deve incluir `codex` para confirmar que o workstream está associado à CLI. Para testar a retomada de ponta a ponta, registre uma palavra ou identificador único na sessão, encerre-a, execute o comando `--workstream` correspondente e peça à sessão retomada que recupere esse identificador. Considere a retomada validada quando a thread correta abrir e o identificador estiver acessível. Não execute duas instâncias simultâneas no mesmo workstream. As opções do AI Memory (`--new`, `--workstream`) vêm antes de `codex`; as opções nativas (`--remote`) vêm depois.

## Documentos

- AGENTS.md — contrato global para todas as sessões e CLIs.
- docs/ARCHITECTURE.md — componentes e fluxo.
- docs/CONTEXT-MODEL.md — responsabilidade de cada fonte de contexto.
- docs/PROTOCOL.md — formato de handoff, retorno e identificação de sessões.
- docs/TASK-SOURCE.md — papel e limites do backlog.
- docs/WORKFLOW.md — instruções operacionais.
- docs/CODEX-QUEUE-POC.md — configuração e validação do canal Codex.
- roles/ — instruções específicas por papel.
- templates/ — modelos para delegação e resultado.
