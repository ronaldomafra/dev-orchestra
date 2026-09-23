# Workflow operacional

## Objetivo

Operar com sessões visíveis e responsabilidades claras, usando um canal explícito de comunicação entre agentes.

No Codex, o transporte já validado pelo POC é:

```text
codex app-server + codex queue
```

Antes de integrar backlog, memória e QA, execute [CODEX-QUEUE-POC.md](CODEX-QUEUE-POC.md).

## Sessões iniciais

Abra três terminais:

1. Orchestrator
2. Developer
3. QA

Opcionalmente:

4. Planner

Cada terminal deve:

- estar no mesmo projeto;
- carregar `AGENTS.md`;
- carregar o arquivo correspondente em `roles/`;
- usar o AI Memory;
- trabalhar em branch/worktree compatível com seu papel quando necessário.

### Inicialização recomendada

O padrão do Dev Orchestra é usar:

```bash
ai-memory run <harness>
```

Exemplos:

```bash
ai-memory run codex
ai-memory run claude
```

Uma CLI iniciada diretamente também pode continuar usando AI Memory quando hooks/MCP já estiverem configurados, mas `ai-memory run` é o launcher preferido porque:

- prepara o escopo correto do projeto;
- pode fazer auto-wiring das integrações suportadas;
- gerencia continuidade entre harnesses;
- gerencia workstreams.

## Workstreams

Workstreams são um detalhe operacional do AI Memory, não um novo componente arquitetural do Dev Orchestra.

Um workstream representa uma linha lógica de trabalho. Como existe apenas um escritor ativo por workstream, sessões paralelas devem usar linhas distintas.

Sugestão inicial:

```text
orchestrator
developer
qa
```

Criação:

```bash
ai-memory run --new orchestrator codex
ai-memory run --new developer codex
ai-memory run --new qa codex
```

Retomada:

```bash
ai-memory run --workstream orchestrator codex
ai-memory run --workstream developer codex
ai-memory run --workstream qa codex
```

Troca de harness mantendo a mesma linha lógica:

```bash
ai-memory run --workstream developer claude
```

Os workstreams permanecem associados ao mesmo projeto e compartilham conhecimento persistente, mas cada papel mantém seu próprio estado de execução.

## Task Source

O Task Source é **somente backlog e estado operacional**. Ele é substituível.

Trello é o primeiro adapter, mas não é canal de comunicação entre sessões e não deve ser usado como fila de mensagens.

No fluxo inicial, o **Orchestrator é responsável por manter o Task Source sincronizado com o trabalho real**.

Com Trello, isso inclui:

- consultar o backlog;
- selecionar cards;
- mover o card para execução;
- mover para teste/validação quando a implementação terminar;
- devolver para execução quando QA encontrar falha;
- mover para concluído quando houver evidência suficiente;
- criar ou atualizar cards quando isso fizer parte do trabalho de coordenação.

Developer e QA não precisam administrar o quadro. Eles recebem a tarefa, executam seu papel e devolvem resultado e evidências ao Orchestrator.

Essa política reduz concorrência e mantém um único ponto responsável pelo estado operacional.

## Ciclo básico

### 1. Seleção

Orchestrator consulta o Task Source e escolhe uma tarefa elegível.

### 2. Início

Orchestrator move a tarefa para o estado de execução apropriado.

### 3. Contextualização

Orchestrator consulta somente o necessário:

- card/tarefa;
- critérios de aceite;
- memória relevante;
- documentação;
- dependências.

### 4. Handoff

Orchestrator envia uma mensagem seguindo `templates/handoff.md` pelo canal entre sessões.

No Codex, use `codex queue`.

Depois do envio, o Orchestrator encerra o turno e aguarda o retorno. Ele não faz polling e não inspeciona arquivos para inferir se o worker terminou.

### 5. Execução

Developer trabalha no escopo recebido e evita expandir a tarefa sem autorização.

### 6. Retorno

Developer responde com `templates/result.md`, incluindo resumo e evidências, pelo mesmo canal de comunicação.

No Codex, o Developer usa `codex queue` para enviar o resultado à sessão do Orchestrator. Essa mensagem inicia o próximo turno do Orchestrator quando a sessão estiver disponível.

### 7. Validação

Orchestrator move a tarefa para teste/validação e envia critérios + evidências para QA.

QA valida a implementação e devolve o resultado.

### 8. Consolidação

Se QA aprovar, Orchestrator:

- consolida o resultado;
- move a tarefa para concluído;
- registra links relevantes;
- promove conhecimento durável para AI Memory quando necessário.

Se QA encontrar problema, Orchestrator devolve a tarefa para execução com a evidência recebida.

## Regra de contexto

O Orchestrator não deve absorver logs, diffs e transcrições completas se um resumo verificável for suficiente.

Workers devem devolver:

- síntese;
- evidência;
- riscos;
- próximo passo recomendado.

Informação temporária permanece na sessão.

Informação durável pode ser promovida para AI Memory.

Estado operacional permanece no Task Source.

## Fluxo de bug

```text
Backlog
  -> Orchestrator move para execução
  -> Developer reproduz/corrige
  -> Orchestrator move para teste
  -> QA valida reprodução e regressão
  -> Orchestrator atualiza o Task Source
```

## Fluxo de feature

```text
Backlog
  -> Orchestrator move para execução
  -> Planner (quando necessário)
  -> Developer
  -> Orchestrator move para teste
  -> QA
  -> Orchestrator atualiza o Task Source
```

## Estratégia Git inicial

Durante a fase manual:

- Orchestrator coordena e evita editar código de feature;
- Developer usa branch/worktree própria quando houver paralelismo;
- QA valida a branch do Developer ou usa worktree separada quando necessário;
- merge final acontece somente após validação.

## Primeiro teste recomendado

Primeiro valide apenas a comunicação entre sessões seguindo [CODEX-QUEUE-POC.md](CODEX-QUEUE-POC.md).

Critério mínimo:

1. Orchestrator envia uma tarefa ao Developer sem o usuário copiar mensagens.
2. Developer recebe e executa.
3. Developer devolve `DONE | PARTIAL | BLOCKED | FAILED` via canal entre sessões.
4. Orchestrator recebe esse retorno em um novo turno.
5. O usuário não precisa transportar UUIDs manualmente quando o Codex consegue fornecê-los para a sessão emissora.

Depois valide o fluxo completo:

1. Orchestrator consegue ler o Task Source.
2. Handoff contém contexto suficiente.
3. Developer executa sem precisar ler toda a história do projeto.
4. QA consegue validar apenas com critérios + evidências.
5. Orchestrator atualiza o backlog corretamente.
6. Uma nova sessão recupera contexto útil pelo AI Memory.
7. Trocar Codex por Claude Code dentro do mesmo workstream mantém continuidade suficiente.

## Quando automatizar

Automatize somente depois de observar algumas execuções e identificar um problema recorrente.

Possíveis etapas futuras:

- bootstrap de terminais;
- tmux;
- criação automática de worktrees;
- bootstrap e descoberta automática de sessões;
- polling/eventos do Task Source;
- workers adicionais.

A regra é simples: **não adicionar infraestrutura antes de existir necessidade real**.
