# Modelo de contexto

A arquitetura separa quatro camadas.

## 1. Task Source

Guarda o estado operacional do trabalho.

Exemplos:

- backlog;
- prioridade;
- descrição;
- critérios de aceite;
- responsável lógico;
- status;
- bloqueios;
- links para PRs ou evidências.

É a referência para **o que precisa ser feito e em que estado está**.

## 2. AI Memory

Guarda conhecimento durável.

Exemplos:

- decisão de usar determinada arquitetura;
- convenção importante;
- limitação conhecida de um provedor;
- causa raiz recorrente;
- escolha de biblioteca;
- regra de produto que afeta várias tarefas.

É a referência para **o que aprendemos e precisamos lembrar**.

## 3. Repository

Guarda artefatos versionados.

Exemplos:

- código;
- testes;
- documentação;
- configuração;
- contratos de agentes;
- ADRs;
- templates.

É a referência para **o que foi implementado e versionado**.

## 4. Session Context

Guarda somente o contexto transitório de uma execução.

Exemplos:

- arquivos abertos;
- logs recentes;
- raciocínio da tarefa atual;
- comandos executados;
- hipóteses ainda não validadas.

É a referência para **o que este agente precisa agora**.

## Regra de promoção

Informação transitória só deve virar memória durável quando tiver valor futuro.

Pergunta prática:

> Se esta sessão fosse apagada agora, outra sessão precisaria saber disso daqui a uma semana?

Se sim, provavelmente merece AI Memory, documentação ou ambos.

## Regra de portabilidade

Nenhuma decisão importante deve existir exclusivamente no histórico interno de uma única CLI.

Para permitir migrar entre Codex, Claude Code ou outra ferramenta:

- regras globais ficam no Git;
- conhecimento durável fica em AI Memory;
- estado de trabalho fica no Task Source;
- a sessão é descartável.
