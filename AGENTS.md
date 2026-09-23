# AGENTS.md

Este arquivo contém as regras globais e canônicas do **Dev Orchestra**. Elas valem para qualquer agente, CLI ou sessão compatível usada neste repositório.

## 1. Princípio central

O objetivo do Dev Orchestra é separar **coordenação**, **execução**, **estado de tarefa** e **memória durável**.

Nenhum agente deve assumir que sua sessão atual contém todo o contexto necessário.

## 2. Fontes de contexto

Use as fontes abaixo com responsabilidades distintas:

- **Task Source**: backlog e estado vivo das tarefas.
- **AI Memory**: decisões e conhecimento durável reutilizável.
- **Repository**: código, documentação e contratos versionados.
- **Session Context**: somente o contexto temporário necessário para a tarefa atual.

Nunca use AI Memory como substituto do backlog.

Nunca use o backlog como depósito de toda a memória técnica do projeto.

## 3. Task Source

O adapter inicial é Trello via MCP, mas as instruções devem tratar a plataforma como uma implementação de `Task Source`.

Quando o Task Source configurado não estiver acessível:

1. não invente tarefas;
2. não altere estado localmente como se tivesse atualizado o backlog;
3. informe claramente que a integração está indisponível;
4. continue apenas se a tarefa já estiver explicitamente fornecida pelo usuário ou pelo Orchestrator.

## 4. Papéis

Cada sessão deve iniciar com um papel explícito em `roles/`.

Papéis iniciais:

- `roles/orchestrator.md`
- `roles/developer.md`
- `roles/qa.md`
- `roles/planner.md`

Uma sessão deve evitar assumir responsabilidades de outro papel sem instrução explícita.

## 5. Orchestrator

O Orchestrator é responsável por:

- selecionar trabalho;
- entender dependências;
- decompor tarefas;
- delegar;
- acompanhar;
- consolidar resultados;
- manter o estado operacional da tarefa.

O Orchestrator deve evitar implementar diretamente quando existir um worker apropriado.

## 6. Workers

Workers especializados devem:

- receber uma tarefa claramente delimitada;
- trabalhar apenas no escopo recebido;
- registrar evidências;
- devolver um resultado estruturado;
- evitar carregar detalhes irrelevantes de outras tarefas.

## 7. Comunicação entre agentes

Use os contratos definidos em:

- `docs/PROTOCOL.md`
- `templates/handoff.md`
- `templates/result.md`

Mensagens entre agentes devem ser curtas, verificáveis e orientadas a resultado.

A comunicação entre sessões deve usar o transporte da CLI configurada. No Codex, o POC validado usa `codex app-server` + `codex queue`.

O Task Source é somente backlog e estado operacional. Nunca o use como fila ou canal de comunicação entre agentes.

Depois de delegar, o Orchestrator deve aguardar o retorno pelo canal entre sessões em vez de fazer polling ou inferir conclusão por inspeção de arquivos.

Workers devem devolver o resultado ao Orchestrator pelo mesmo canal, sem depender de intervenção manual do usuário.

## 8. Memória

Grave em AI Memory apenas informação com valor futuro, como:

- decisões arquiteturais;
- convenções;
- restrições persistentes;
- causas raiz relevantes;
- aprendizados recorrentes;
- contexto de produto que afeta tarefas futuras.

Não grave como memória durável:

- logs transitórios;
- progresso minuto a minuto;
- diffs completos;
- saída extensa de testes;
- estado atual de um card;
- informação facilmente derivável do repositório.

## 9. Git

Mudanças de código devem ser rastreáveis.

Quando múltiplos agentes trabalham em paralelo, prefira isolamento por branch ou worktree para reduzir conflitos.

Nunca sobrescreva silenciosamente mudanças de outro agente.

## 10. Humano no controle

O desenvolvedor humano continua responsável por decisões críticas.

Escalone quando houver:

- requisito ambíguo;
- mudança arquitetural relevante;
- risco de perda de dados;
- conflito entre critérios;
- alteração destrutiva;
- bloqueio externo;
- dúvida de produto que não pode ser inferida com segurança.

## 11. Portabilidade

`AGENTS.md` é o contrato universal e a fonte única das regras globais do Dev Orchestra.

Não duplique regras globais em arquivos específicos de Codex, Claude Code ou qualquer outra CLI.

Diferenças de responsabilidade entre sessões devem ser expressas pelos arquivos em `roles/`, enquanto diferenças de integração devem ficar isoladas em adapters, scripts ou configuração própria da ferramenta quando realmente necessárias.

Uma troca de CLI não deve exigir reescrever as regras do projeto.

## 12. Regra de ouro

**Estado operacional vive no Task Source. Conhecimento durável vive na memória. Código e contratos vivem no Git. Contexto temporário vive na sessão.**
