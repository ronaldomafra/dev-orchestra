# Adapter: Claude Code

Este documento reúne o que é específico do Claude Code. Papéis, protocolo e Task Source são os mesmos do [modo Codex](ADAPTER-CODEX.md). O estado da validação está em [CLAUDE-CODE-VALIDATION](CLAUDE-CODE-VALIDATION.md).

## Princípios

- **Uma sessão Claude Code por papel**, cada uma em seu terminal e em seu workstream AI Memory.
- **Sem App Server e sem `codex queue`.** O transporte é a ferramenta **SendMessage**, que entrega mensagens entre sessões Claude Code abertas na mesma máquina.
- **Não use subagents da ferramenta Agent para os workers.** Um subagent roda dentro da sessão do Orchestrator: o trabalho deixa de ser observável em um terminal próprio e fica registrado no workstream do Orchestrator, não no do worker. As definições de papel são usadas com `--agent` para abrir sessões completas.

## Definições de papel

Copie as definições deste repositório para o projeto:

```bash
mkdir -p .claude/agents
cp adapters/claude-code/agents/*.md .claude/agents/
```

| Arquivo | Papel | Abertura |
| --- | --- | --- |
| [developer.md](../adapters/claude-code/agents/developer.md) | Developer | `--agent developer` |
| [planner.md](../adapters/claude-code/agents/planner.md) | Planner | `--agent planner` |
| [qa.md](../adapters/claude-code/agents/qa.md) | QA | `--agent qa` |

As definições não fixam `model` nem `effort`: esses valores são aprovados a cada delegação ([MODEL-SELECTION](MODEL-SELECTION.md)). Ajuste nomes, ferramentas permitidas e instruções ao projeto que adotar o processo.

O Orchestrator não usa `--agent`: abre como sessão principal e recebe o papel pelo prompt inicial.

## Abrir as sessões

```bash
# Orchestrator
ai-memory run --new orchestrator claude --name orchestrator

# Developer
ai-memory run --new developer claude --agent developer --name developer --model sonnet --effort medium

# Planner, quando necessário
ai-memory run --new planner claude --agent planner --name planner --model sonnet --effort medium
```

Nas retomadas, troque `--new <papel>` por `--workstream <papel>` e mantenha o restante.

- O **nome da sessão** (`--name` ou `/rename`) é o endereço usado pelo SendMessage. Mantenha-o igual à chave do workstream para evitar confusão.
- Confira com `/list-agents`, ou pedindo à sessão que liste os agentes, que as sessões se enxergam. A sessão atual aparece identificada.
- **Use o mesmo modo de permissão em todas as sessões.** Sessões em modos diferentes podem reter a mensagem recebida até aprovação manual.
- Opções do AI Memory ficam antes de `claude`; `--agent`, `--name`, `--model` e `--effort` ficam depois.
- Na primeira abertura, `ai-memory run` instala automaticamente hooks e MCP do Claude Code, salvo `--no-autowire`.

## Handoff e retorno

O Orchestrator envia com SendMessage para o nome do worker:

```text
HANDOFF TASK: DEMO-001
PROFILE: <perfil técnico, quando houver>
OBJETIVO: <resultado esperado>
ESCOPO: <incluído e fora do escopo>
CRITÉRIOS: <critérios de aceite verificáveis>
RESTRIÇÕES: <preservar mudanças locais, sem commit, sem Task Source>
REFERÊNCIAS: <documentos, código, memórias com fonte>
VALIDAÇÃO: <comandos/testes esperados>
MODEL: <aprovado>
EFFORT: <aprovado>
MODEL_REASON: <justificativa curta>
RETURN_TO: orchestrator
```

O handoff deve ser autossuficiente: o worker não vê a conversa do Orchestrator.

Depois que o SendMessage confirmar a entrega, o Orchestrator **encerra o turno**. A mensagem chega ao destino como um novo turno; o retorno do worker chega ao Orchestrator da mesma forma. Não use `sleep`, polling, listagem de agentes em loop nem inspeção da sessão do worker.

O worker responde com SendMessage ao `RETURN_TO` no formato de [resultado](../templates/result.md) e encerra o turno.

## Aprovação de modelo

O Orchestrator pede aprovação com a pergunta estruturada da CLI (AskUserQuestion), com a recomendação como primeira opção e alternativas mais barata e mais cara. Depois da aprovação, a pessoa desenvolvedora ajusta a sessão do worker com `/model` ou reabrindo com `--model` e `--effort`, e confirma ao Orchestrator.

## Envio pelo terminal

Não há comando de terminal que injete uma mensagem em uma sessão interativa já aberta; para isso use SendMessage a partir de outra sessão. Com a sessão de destino **fechada**, é possível executar um turno pelo terminal:

```bash
# Um turno não interativo; a resposta sai no terminal
claude -p --resume developer "HANDOFF TASK: ..."

# Um turno em segundo plano; acompanhe com claude agents, claude logs e claude attach
claude --bg --resume developer "HANDOFF TASK: ..."
```

Essas formas não passam pelo `ai-memory run` e, portanto, podem não registrar o turno no workstream. Use-as para tarefas avulsas ou diagnóstico, não como transporte padrão.

## Trocar do Codex para o Claude Code

Um workstream criado no Codex pode ser aberto no Claude Code:

```bash
ai-memory run --workstream orchestrator claude --name orchestrator
```

A sessão Claude é nova, mas recebe o contexto do workstream (ledger e handoffs pendentes). A conversa nativa do Codex não é retomada. Encerre a sessão Codex antes: há um lease por workstream.

Se o fluxo Codex usava `RETURN_TO` com UUID de sessão Codex, ajuste para os nomes das sessões Claude. Uma mensagem enviada por `codex queue` não chega a uma sessão Claude, e vice-versa: todos os papéis de um fluxo devem usar o mesmo transporte.

## Diagnóstico

| Sintoma | Verificação |
| --- | --- |
| Mensagem não chega | Confira o nome exato em `/list-agents` e o modo de permissão das duas sessões |
| Destino recebe, Orchestrator não recebe o retorno | O Orchestrator ainda estava em *working*; o retorno chega quando o turno termina |
| `AI_MEMORY_WORKSTREAM_ID` ausente na sessão | A sessão foi aberta com `claude` direto; reabra com `ai-memory run` |
| `linked_harnesses` sem `claude` | Mesma causa; confira com `ai-memory workstreams --json` |
| Erro de projeto não encontrado | O projeto foi deduzido do checkout; crie `.ai-memory.toml` ou use `--project` |
| Lease ativo ao abrir | Encerre a outra instância do workstream; `--force-unlock` só se o processo anterior já terminou |
