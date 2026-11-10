# Protocolo entre sessões

## Objetivo

Definir o conteúdo mínimo de uma delegação e de um resultado. O formato é o mesmo em qualquer CLI; o transporte depende do modo configurado:

| Modo | Transporte | Endereço da sessão |
| --- | --- | --- |
| Codex | `codex app-server` + `codex queue` | Nome exato (`/rename`) ou Session UUID (`/status`) |
| Claude Code | SendMessage entre sessões locais | Nome da sessão (`--name` ou `/rename`) |

Todos os papéis de um fluxo usam o mesmo transporte. Trello e outros Task Sources não são canais de comunicação.

## Handoff

Toda delegação informa:

- TASK: identificador da tarefa;
- INSTRUCTION: objetivo, escopo, critérios e restrições;
- MODEL, EFFORT e MODEL_REASON: modelo e esforço aprovados para esta delegação ([MODEL-SELECTION](MODEL-SELECTION.md));
- RETURN_TO: sessão do Orchestrator para receber o resultado.

Use [templates/handoff.md](../templates/handoff.md) quando a tarefa precisar de referências, contexto ou critérios detalhados. O handoff deve ser autossuficiente: o worker não vê a conversa do Orchestrator.

Exemplo:

    TASK: EXEMPLO-001
    INSTRUCTION: Confirme o recebimento. Não crie nem altere arquivos.
    MODEL: haiku
    EFFORT: low
    MODEL_REASON: confirmação sem alteração de arquivos
    RETURN_TO: orchestrator

## Resultado

Todo worker responde pelo mesmo transporte usado para receber a tarefa. O formato mínimo é:

    STATUS: DONE
    TASK: EXEMPLO-001
    SUMMARY: Confirmei o recebimento da tarefa.
    EVIDENCE: A mensagem foi recebida; nenhum arquivo foi alterado.

STATUS aceita DONE, PARTIAL, BLOCKED, FAILED ou NEEDS_REVIEW.

Acrescente informações quando forem relevantes:

- CHANGES: arquivos e mudanças realizadas;
- VALIDATION: comandos/testes e resultado;
- RISKS: riscos ou pendências;
- NEXT_STEP: próxima ação recomendada;
- MEMORY_CANDIDATE: conhecimento durável sugerido;
- GIT: branch e commit, quando aplicável.

Use [templates/result.md](../templates/result.md) para o formato completo. Seja breve, mas inclua evidências verificáveis.

## Espera e recebimento

Depois de entregar o handoff, o Orchestrator **encerra o turno**. O resultado chega como uma nova mensagem na sessão do Orchestrator. Não use `sleep`, polling nem inspeção de arquivos ou processos para inferir conclusão. O envio ao worker não substitui o resultado do worker.

No Codex, foi observado em uso real que a resposta fica na fila enquanto a sessão de destino está em *working* e chega quando o turno termina. Por isso manter o turno aberto atrasa o recebimento.

<a id="transporte-codex"></a>

## Transporte Codex

Configure ferramentas, hooks e MCPs pelo [SETUP](SETUP.md) antes de iniciar um `codex app-server`. Conecte as sessões participantes ao mesmo endpoint, cada uma em seu terminal. Para entregar uma mensagem:

    codex queue --remote ws://127.0.0.1:4500 --thread developer --message 'TASK: EXEMPLO-001
    INSTRUCTION: Confirme o recebimento. Não altere arquivos.
    RETURN_TO: <UUID do Orchestrator>'

Uma resposta de fila confirma que a mensagem foi aceita pelo App Server. O encerramento da tarefa é confirmado pelo resultado recebido na sessão de destino. Detalhes em [ADAPTER-CODEX](ADAPTER-CODEX.md).

<a id="transporte-claude-code"></a>

## Transporte Claude Code

Cada papel roda em uma sessão Claude Code própria, aberta com `--name <papel>`. O Orchestrator chama SendMessage com `to: "developer"` e o texto do handoff; o worker responde com SendMessage para o `RETURN_TO`. As sessões precisam estar abertas na mesma máquina e no mesmo modo de permissão. Detalhes em [ADAPTER-CLAUDE-CODE](ADAPTER-CLAUDE-CODE.md).

## Nome e identificação da sessão

- Dê nomes claros e distintos às sessões, iguais às chaves dos workstreams: `orchestrator`, `developer`, `planner`, `qa`.
- Codex: use `/rename` e obtenha o Session UUID em `/status`. Tente primeiro o nome exato; se não resolver, use somente o UUID confirmado.
- Claude Code: use `--name` na abertura ou `/rename`; confira os nomes visíveis com `/list-agents`.
- Títulos de sessão e chaves de workstreams AI Memory são distintos; renomear a sessão não altera a chave usada em `--workstream`.
- Nunca escolha entre sessões ambíguas por tentativa. Se não houver nome único nem identificador confirmado, marque BLOCKED e informe o que falta.

O `RETURN_TO` deve identificar o Orchestrator real que aguarda o resultado, nunca o próprio worker. Inclua no handoff a obrigação de enviar autonomamente o retorno pelo transporte; imprimir o resultado na própria sessão não entrega a mensagem.

## Bloqueios

Quando não puder prosseguir, envie BLOCKED com:

1. o que impediu a execução;
2. o que foi tentado;
3. qual informação ou ação destrava o trabalho.

## Primeiro fluxo e diagnóstico

O [Quick Start](../README.md#quick-start-um-card-dois-agentes) usa DEMO-001 com Trello desde o início. O exemplo EXEMPLO-001 acima apenas ilustra a mensagem mínima. Os testes históricos do Codex estão no [POC de transporte](CODEX-QUEUE-POC.md); os testes do Claude Code, em [CLAUDE-CODE-VALIDATION](CLAUDE-CODE-VALIDATION.md).
