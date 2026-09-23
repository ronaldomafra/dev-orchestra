# POC — comunicação entre sessões Codex

## Objetivo

Validar a base do Dev Orchestra no Codex:

> uma sessão **Orchestrator** envia trabalho para uma sessão **Developer** em outro terminal, e o Developer devolve o resultado para o Orchestrator pelo mesmo canal de comunicação.

Neste POC usamos somente recursos nativos do Codex:

- `codex app-server`;
- `codex queue`;
- sessões Codex conectadas ao mesmo App Server.

Não use Trello, AI Memory, QA ou qualquer fila externa neste teste.

O Trello/Task Source é backlog. **Não é canal de comunicação entre agentes.**

## Resultado esperado

```text
Terminal 1                     Terminal 2               Terminal 3
Codex App Server               Orchestrator              Developer
      |                             |                        |
      |<------ conexão ------------|                        |
      |<------ conexão -------------------------------------|
      |                             |                        |
      |<---- codex queue -----------|                        |
      |----------------------- tarefa ---------------------->|
      |                             |                        |
      |<-------------------------------- codex queue --------|
      |------- resultado ---------->|                        |
```

O Orchestrator não verifica arquivos para descobrir se o Developer terminou. Depois de delegar, ele encerra seu turno e aguarda a mensagem de retorno enviada pelo Developer via `codex queue`.

---

## 1. Verifique o Codex

```bash
codex --version
codex queue --help
```

O comando `codex queue` deve estar disponível.

## 2. Crie uma pasta de teste

```bash
mkdir -p ~/dev-orchestra-poc
cd ~/dev-orchestra-poc
git init
echo "# Dev Orchestra POC" > README.md
```

Use a mesma pasta nos três terminais.

---

## 3. Terminal 1 — App Server

```bash
cd ~/dev-orchestra-poc
codex app-server --listen ws://127.0.0.1:4500
```

Deixe esse terminal aberto.

---

## 4. Terminal 2 — Orchestrator

```bash
cd ~/dev-orchestra-poc
codex --remote ws://127.0.0.1:4500
```

Dentro do Codex:

```text
/rename orchestrator-poc
```

Depois cole:

```text
Você é o Orchestrator deste POC.

Configuração:
- App Server: ws://127.0.0.1:4500
- sessão Developer: developer-poc
- sua sessão: orchestrator-poc

Regras:
1. Delegue tarefas ao Developer exclusivamente usando codex queue.
2. Não execute a tarefa destinada ao Developer.
3. Não verifique arquivos, Git ou processos para inferir se o Developer terminou.
4. Não faça polling.
5. Depois de enviar o handoff, encerre seu turno e aguarde uma nova mensagem chegar pelo canal.
6. O retorno oficial da execução é a mensagem que o Developer enviar para sua sessão via codex queue.
7. Ao receber o retorno, processe STATUS, TASK, SUMMARY e EVIDENCE.

Resolução da sessão:
- tente primeiro usar --thread developer-poc;
- se o Codex informar um UUID correspondente porque não conseguiu confirmar a unicidade do nome, capture esse UUID automaticamente e repita o comando;
- não peça o UUID ao usuário;
- se houver mais de uma sessão realmente candidata e não for possível determinar qual é a correta, não escolha aleatoriamente.

Toda tarefa enviada deve informar:
TASK: <id>
INSTRUCTION: <instrução>
RETURN_TO: orchestrator-poc

Também instrua o Developer a enviar o resultado de volta para orchestrator-poc usando codex queue.
```

---

## 5. Terminal 3 — Developer

```bash
cd ~/dev-orchestra-poc
codex --remote ws://127.0.0.1:4500
```

Dentro do Codex:

```text
/rename developer-poc
```

Depois cole:

```text
Você é o Developer deste POC.

Configuração:
- App Server: ws://127.0.0.1:4500
- sessão Orchestrator: orchestrator-poc
- sua sessão: developer-poc

Regras:
1. Receba tarefas pelo canal codex queue.
2. Execute somente a tarefa recebida.
3. Ao terminar, envie obrigatoriamente o resultado para orchestrator-poc usando codex queue.
4. Não espere o usuário pedir o retorno.
5. Não considere suficiente escrever o resultado apenas na sua própria sessão.
6. Depois de enviar o retorno, encerre seu turno.

Resolução da sessão:
- tente primeiro usar --thread orchestrator-poc;
- se o Codex informar um UUID correspondente porque não conseguiu confirmar a unicidade do nome, capture esse UUID automaticamente e repita o comando;
- não peça o UUID ao usuário;
- se houver mais de uma sessão realmente candidata e não for possível determinar qual é a correta, não escolha aleatoriamente.

Formato do retorno:

STATUS: DONE | PARTIAL | BLOCKED | FAILED
TASK: <id>
SUMMARY: <resumo curto>
EVIDENCE: <evidência relevante>
```

---

## 6. Teste Orchestrator -> Developer -> Orchestrator

No Terminal 2, envie ao Orchestrator:

```text
Delegue a tarefa TEST-001 para o Developer:

Crie o arquivo conversa.txt contendo exatamente:

SESSIONS ARE TALKING

Depois de enviar a tarefa, aguarde o retorno do Developer pelo canal de comunicação.
```

### Comportamento esperado

O Orchestrator deve executar algo equivalente a:

```bash
codex queue \
  --remote ws://127.0.0.1:4500 \
  --thread developer-poc \
  --message "TASK: TEST-001
INSTRUCTION: Crie o arquivo conversa.txt contendo exatamente SESSIONS ARE TALKING
RETURN_TO: orchestrator-poc

Quando terminar, envie o resultado de volta para orchestrator-poc usando codex queue."
```

Se o nome não puder ser confirmado como único, o próprio Orchestrator deve ler o UUID retornado pelo Codex e repetir o envio usando esse UUID.

Depois do envio, **o Orchestrator deve parar**. Ele não deve abrir `conversa.txt` nem fazer qualquer checagem para descobrir se o Developer terminou.

O Developer recebe a tarefa, executa e envia de volta algo equivalente a:

```text
STATUS: DONE
TASK: TEST-001
SUMMARY: arquivo criado com sucesso
EVIDENCE: conversa.txt criado
```

O retorno também deve ser enviado via `codex queue` para a sessão `orchestrator-poc`.

Quando a mensagem chegar, ela inicia o próximo turno do Orchestrator. Só então ele processa o resultado recebido.

---

## 7. Critério de sucesso do POC

O POC está validado quando acontecer sem intervenção manual entre as sessões:

```text
Você
  |
  v
Orchestrator
  |
  | codex queue
  v
Developer
  |
  | executa
  | codex queue
  v
Orchestrator
```

Você pode observar os terminais, mas não deve copiar a mensagem de uma sessão para a outra.

## Problemas conhecidos durante o teste

### "Multiple sessions match"

Exemplo:

```text
Multiple sessions match 'developer' ... use a session UUID to disambiguate.
```

Existem várias sessões com o mesmo nome. Use nomes de POC mais específicos e deixe o agente resolver o UUID quando necessário.

### "Cannot verify a unique session label across server pages"

Exemplo:

```text
Cannot verify a unique session label across server pages; matching session UUID: <UUID>.
```

Isso não exige que o usuário copie o UUID.

A sessão emissora deve:

1. ler o UUID informado pelo próprio Codex;
2. repetir automaticamente o `codex queue` com `--thread <UUID>`;
3. continuar o fluxo.

## O que este POC não valida

Este teste não valida ainda:

- Trello ou outro Task Source;
- AI Memory;
- QA;
- Planner;
- branches/worktrees;
- múltiplos Developers;
- troca Codex/Claude;
- automação de bootstrap.

Ele valida somente o fundamento:

**sessões Codex independentes, visíveis em terminais diferentes, trocando tarefas e resultados pelo App Server usando `codex queue`.**
