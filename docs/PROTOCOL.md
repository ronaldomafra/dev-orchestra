# Protocolo entre sessões

## Objetivo

Definir o conteúdo mínimo de uma delegação e de um resultado. O transporte depende da CLI configurada.

No Codex, o transporte validado usa sessões conectadas ao mesmo codex app-server e codex queue. Trello e outros Task Sources não são canais de comunicação.

## Handoff

Toda delegação informa:

- TASK: identificador da tarefa;
- INSTRUCTION: objetivo, escopo, critérios e restrições;
- RETURN_TO: sessão do Orchestrator para receber o resultado.

Use templates/handoff.md quando a tarefa precisar de referências, contexto ou critérios detalhados.

Exemplo:

    TASK: COMM-001
    INSTRUCTION: Confirme o recebimento. Não crie nem altere arquivos.
    RETURN_TO: orchestrator-poc

## Resultado

Todo worker responde pelo mesmo canal usado para receber a tarefa. O formato mínimo é:

    STATUS: DONE
    TASK: COMM-001
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

Use templates/result.md para o formato completo. Seja breve, mas inclua evidências verificáveis.

## Transporte Codex

Inicie um codex app-server e conecte as sessões participantes ao mesmo endpoint. Para entregar uma mensagem:

    codex queue --remote ws://127.0.0.1:4500 --thread developer-poc --message 'TASK: COMM-001
    INSTRUCTION: Confirme o recebimento. Não altere arquivos.
    RETURN_TO: orchestrator-poc'

Uma resposta de fila confirma que a mensagem foi aceita pelo App Server. O encerramento da tarefa é confirmado pelo resultado recebido na sessão de destino.

## Nome e identificação da sessão

- Use /rename no Codex para atribuir nomes claros e distintos às sessões, por exemplo orchestrator-poc e developer-poc.
- Tente primeiro o nome exato com codex queue.
- Se o Codex retornar um UUID correspondente à sessão, repita o envio usando esse UUID.
- Use somente um UUID confirmado para a sessão de destino. Nunca escolha entre sessões ambíguas por tentativa.
- Se não houver nome único nem UUID confirmado, marque BLOCKED e informe o que falta.

O envio ao worker não substitui o resultado do worker. Depois de delegar, o Orchestrator encerra o turno e aguarda a resposta pelo canal; não faz polling nem inspeciona arquivos para inferir conclusão.

## Bloqueios

Quando não puder prosseguir, envie BLOCKED com:

1. o que impediu a execução;
2. o que foi tentado;
3. qual informação ou ação destrava o trabalho.
