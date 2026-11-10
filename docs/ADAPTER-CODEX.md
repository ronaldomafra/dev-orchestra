# Adapter: Codex

Este documento reúne o que é específico do Codex. Papéis, protocolo e Task Source são os mesmos do [modo Claude Code](ADAPTER-CLAUDE-CODE.md).

## Transporte

As sessões se conectam ao mesmo `codex app-server`. O `codex queue` entrega uma mensagem a uma sessão identificada pelo nome exato ou pelo Session UUID.

```bash
codex app-server --listen ws://127.0.0.1:4500
```

```bash
codex queue --remote ws://127.0.0.1:4500 --thread developer --message 'TASK: DEMO-001
INSTRUCTION: <objetivo, escopo, critérios, restrições, MODEL/EFFORT/MODEL_REASON>
RETURN_TO: <UUID do Orchestrator>'
```

- A resposta do `codex queue` confirma apenas que a mensagem entrou na fila; não comprova execução nem retorno.
- **Encerre o turno logo após o enfileiramento.** Na validação em uso real, a resposta do worker ficou na fila enquanto o Orchestrator estava em *working* e chegou quando o turno terminou. Manter o turno aberto com `sleep`, espera ou checagem de status atrasa o recebimento.
- O worker envia o resultado autonomamente com `codex queue` ao `RETURN_TO`; imprimir o resultado na própria sessão não entrega a mensagem.
- O App Server é experimental na CLI. Não inicie uma segunda instância na mesma porta; confira `/readyz` e `/healthz` antes.

## Identificação das sessões

- Use `/rename <papel>` dentro de cada sessão.
- `/status` mostra o Session UUID, o modelo e o esforço efetivos.
- Tente primeiro o nome exato; se não resolver, use somente o UUID confirmado. Nunca escolha entre sessões ambíguas por tentativa.
- O título `/rename` não é a chave do workstream AI Memory.

## Criar e retomar

```bash
# Primeira criação
ai-memory run --new developer codex --remote ws://127.0.0.1:4500 --model gpt-6-luna -c 'model_reasoning_effort="low"'

# Retomada padrão
ai-memory run --workstream developer codex --remote ws://127.0.0.1:4500 --model "<aprovado>" -c 'model_reasoning_effort="<aprovado>"'
```

Opções do AI Memory (`--project`, `--workstream`, `--new`) ficam antes de `codex`; `--remote`, `--model` e `-c` ficam depois.

Na retomada padrão, o AI Memory seleciona a sessão vinculada ao workstream. Em uso real também funcionou forçar uma sessão específica:

```bash
ai-memory run --workstream developer codex resume "<UUID>" --remote ws://127.0.0.1:4500
```

Use essa forma somente quando a retomada padrão abrir uma sessão diferente da esperada, e confira o UUID em `/status`.

## Aprovação de modelo

Depois que a pessoa desenvolvedora aprovar `MODEL` e `EFFORT`, ajuste a sessão do worker (reabrindo com `--model` e `-c 'model_reasoning_effort="..."'` quando necessário) e confirme os valores efetivos em `/status` antes do handoff. Veja [MODEL-SELECTION](MODEL-SELECTION.md).

## Diagnóstico

O [POC do transporte](CODEX-QUEUE-POC.md) guarda o roteiro isolado, o procedimento de sandbox no Ubuntu 24.04 e o histórico COMM-001/002/003. A tabela geral de diagnóstico está em [SETUP](SETUP.md#diagnostico).
