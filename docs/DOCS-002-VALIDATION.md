# DOCS-002 — registro de validação documental

Data: 2026-11-10. Escopo: incluir o modo Claude Code, a regra de escolha e aprovação de modelo a cada delegação e o uso de um workstream AI Memory por mais de uma CLI. Documentação em português e verificações não destrutivas.

## Mudanças

- README reorganizado: evolução do processo, imagem do fluxo de trabalho, linha do tempo de maturidade, Quick Start com trilhas Codex e Claude Code, retomada nos dois modos e troca de CLI no mesmo workstream.
- Nova imagem `docs/assets/dev-orchestra-workflow.webp`, com a fonte editável `dev-orchestra-workflow.svg`.
- Novos documentos: MODEL-SELECTION, ADAPTER-CODEX, ADAPTER-CLAUDE-CODE e CLAUDE-CODE-VALIDATION.
- Novas definições de papel para o Claude Code em `adapters/claude-code/agents/` (developer, planner, qa).
- AGENTS.md: transportes por modo, aprovação de modelo, workstreams fixos por papel e `roles/social-media.md` na lista de papéis.
- PROTOCOL, WORKFLOW, SETUP, TASK-SOURCE, ARCHITECTURE, CONTEXT-MODEL, papéis e templates ajustados para os dois transportes, com `MODEL`, `EFFORT` e `MODEL_REASON` no handoff.
- SETUP: instalação do Claude Code, hooks/MCP do AI Memory para `claude-code`, `.ai-memory.toml`, Trello no Claude Code e novas linhas de diagnóstico.
- Retomada Codex: o caminho padrão continua sem `resume <UUID>`; a forma com `resume <UUID>`, usada em projeto real, foi documentada como alternativa.
- Âncora explícita `outro-servico-via-mcp` em TASK-SOURCE, corrigindo um link de ARCHITECTURE que não resolvia.

## Verificações realizadas

| Verificação | Evidência |
| --- | --- |
| Whitespace | `git diff --check` sem erros; arquivos novos conferidos com `git diff --no-index --check /dev/null <arquivo>` |
| Markdown | Fences balanceadas; links locais e âncoras conferidos por script Python em todos os arquivos Markdown |
| Shell | Blocos `bash` aprovados por `bash -n`; nenhum comando de instalação foi executado |
| Comandos | Conferidos nos helps locais: `claude --help` e `claude mcp add --help` (Claude Code `2.1.322`), `ai-memory run --help`, `install-mcp --help` e `install-hooks --help` (AI Memory `3.4.3`), `codex queue --help` (Codex `0.163.0`) |
| Imagem | Renderizada a partir do SVG com Chrome headless e conferida visualmente |

## Limites e pendências

O modo Claude Code está documentado, mas o fluxo completo ainda não foi validado de ponta a ponta. As evidências disponíveis e os testes pendentes (ping-pong, retorno com o Orchestrator em *working*, retomada gerenciada, memória durável e DEMO-001) estão em [CLAUDE-CODE-VALIDATION](CLAUDE-CODE-VALIDATION.md).

Os modelos e esforços em [MODEL-SELECTION](MODEL-SELECTION.md) são pontos de partida, não benchmarks. Disponibilidade e custos dependem da conta e do plano.

Não foram executados instalação, criação ou reinício de serviços, alterações no Trello nem abertura de sessões de agentes nesta revisão.
