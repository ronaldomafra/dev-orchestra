# DOCS-001 — registro de validação documental

Data: 2026-09-23. Escopo: documentação em português e verificações não destrutivas. O checkout estava limpo no `git status --short` inicial. Não houve alteração dos contratos AGENTS.md, roles/ ou templates/.

## Mudanças

- README reorganizado como entrada: história, proposta, sessões observáveis, papéis, maturidade, primeiro fluxo Trello, retomada, expansão e agradecimentos.
- SETUP criado com instalação Ubuntu, Docker, Codex, wrapper AI Memory com checksum, hooks/MCP, OAuth Trello e diagnóstico.
- WORKFLOW, TASK-SOURCE e PROTOCOL alinhados ao fluxo com Orchestrator e Developer e retorno autônomo por fila.
- Histórico COMM-001/002/003 e distinção `developer`/`developer_new` concentrados em CODEX-QUEUE-POC, preservando as evidências disponíveis.
- ARCHITECTURE e CONTEXT-MODEL ajustados para distinguir contratos, captura técnica, continuidade e conhecimento durável.

## Verificações realizadas

| Verificação | Evidência |
| --- | --- |
| Whitespace | `git diff --check` sem erros; arquivos novos também conferidos com `git diff --no-index --check /dev/null <arquivo>` |
| Markdown | 16 arquivos Markdown verificados; fences balanceadas e 59 links locais/âncoras conferidos por script Python |
| Shell | 48 blocos de exemplos, incluindo shell indentado do protocolo/POC, aprovados por `bash -n`; nenhum comando de instalação foi executado |
| Coerência | Revisados nomes `orchestrator`/`developer`, checkout compartilhado, endpoint 4500, servidor AI Memory 49374 e distinção entre destino Developer e `RETURN_TO` Orchestrator |
| Mermaid | Playwright com Mermaid 11: `mermaid.parse` identificou `sequence`; `mermaid.render` gerou SVG de 25.291 caracteres, com Trello, Orchestrator, Developer e Concluído |
| Imagem | SHA256 antes e depois: `38f061afffc2cba1d3a7bbf0dffab25a942715fec260e4dc1f2f3079364fdd96` |
| Artefato do tutorial | `docs/demo/primeiro-fluxo.md` ausente; será criado pelo leitor ao executar DEMO-001 |
| Contratos | Diff de AGENTS.md, roles/, templates/ e docs/assets/ vazio |

A validação Mermaid usou a biblioteca carregada no navegador a partir de `https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js`, sem instalar dependências globais. Isso valida parser e geração de SVG; não comprova renderização em toda plataforma Markdown. As âncoras locais com acentos foram corrigidas por identificadores explícitos quando necessário.

## Fundamentação dos comandos

Foram lidos os helps locais de `codex queue`, `codex app-server`, `codex mcp add`, `codex mcp login`, `codex login` e `ai-memory run`. `codex --version` retornou `codex-cli 0.156.1`. O help confirma App Server experimental, fila por nome exato/UUID, transporte remoto, cadastro MCP por URL, OAuth e seletores AI Memory `--new`/`--workstream`. Não se deduziu uma versão mínima.

Fontes oficiais consultadas: [instalação Codex](https://learn.chatgpt.com/docs/codex/cli), [comandos interativos](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [Docker Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [Docker pós-instalação](https://docs.docker.com/engine/install/linux-postinstall/), [AI Memory Quick Start](https://github.com/akitaonrails/ai-memory#quick-start), [instalação AI Memory](https://github.com/akitaonrails/ai-memory/blob/main/docs/install.md), [workstreams AI Memory](https://github.com/akitaonrails/ai-memory/blob/main/docs/managed-workstreams.md) e [Trello MCP](https://support.atlassian.com/trello/docs/connect-trello-to-ai-assistants-with-trello-mcp/).

## Limites e pendências

No levantamento anterior fornecido no handoff, `ai-memory --version`, `install-hooks --help` e `install-mcp --help` falharam por acesso negado ao socket Docker. Essa limitação não foi tratada como defeito universal da instalação e não houve tentativa de corrigir permissões nesta tarefa. Os parâmetros de hooks/MCP foram fundamentados nas fontes oficiais; `ai-memory run --help` pôde ser consultado localmente.

Não foram executados instalação, criação/reinício de serviços, abertura de sessões de agentes, alterações no Trello ou testes de comunicação com workers. O envio do resultado DOCS-001 ao Orchestrator é a entrega autorizada desta tarefa, não um teste de runtime do tutorial. Nenhum commit, push ou publicação foi realizado.

Permanecem para execução pelo usuário: instalação em ambiente novo, leitura/escrita reais Trello, ciclo completo DEMO-001, retomada gerenciada com mesmo UUID e marcador e recuperação independente de conhecimento durável via AI Memory. As evidências históricas do [POC](CODEX-QUEUE-POC.md#historico-local-preservado) não substituem essas verificações.
