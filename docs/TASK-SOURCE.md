# Task Source

## Papel

Task Source é o serviço que guarda backlog e estado operacional: o que precisa ser feito e em que estado está. Um servidor MCP (Model Context Protocol) permite que a CLI do agente consulte e altere ferramentas externas. Trello é o exemplo conectado dessa forma no primeiro fluxo. Dev Orchestra define as responsabilidades que o serviço precisa cumprir; este repositório não fornece um runtime de integração nem adapters implementados para outros serviços.

## Campos da tarefa

Toda tarefa delegada precisa de identificador, título, objetivo, escopo, critérios de aceite e referência identificável. Status, prioridade, dependências e rótulos ajudam a seleção. Campos específicos variam por ferramenta.

O exemplo inicial é **DEMO-001: Meu primeiro fluxo**, com URL de card real e critérios completos no [README](../README.md#3-criar-a-tarefa-no-trello). O arquivo de demonstração será criado quando o leitor executar o fluxo.

## Responsabilidades

O Orchestrator consulta tarefas, considera dependências, registra início e conclusão e mantém o backlog coerente com as evidências recebidas. Workers recebem handoffs e retornam resultados pelo canal entre sessões; acesso eventual ao backlog não os autoriza a mudar o status.

O Task Source não transporta handoffs/resultados nem substitui memória durável ou Git. Registre nele um resumo e referências às evidências; mantenha os artefatos no repositório e decisões reutilizáveis na fonte apropriada.

## Trello

| Trello | Uso no primeiro fluxo |
| --- | --- |
| Workspace autorizado | Escopo de acesso da conexão MCP |
| Board | Projeto de demonstração, identificado por URL |
| Listas | Backlog, Em execução, Concluído |
| Card | DEMO-001, identificado por título e URL |
| Descrição | Objetivo, critérios e resumo de evidências preservando o texto original |

Configure antes de iniciar o App Server:

```bash
codex mcp add trello --url https://mcp.trello.com/v1
```

Complete o OAuth e, se necessário, execute `codex mcp login trello`. Verifique o cadastro com `codex mcp list` e depois confirme o acesso real lendo board e card pela sessão Orchestrator. A [instalação completa](SETUP.md#trello) explica a sequência.

A [fonte oficial Trello MCP](https://support.atlassian.com/trello/docs/connect-trello-to-ai-assistants-with-trello-mcp/), consultada em 2026-09-23, documenta leitura, criação, atualização e movimentação de cards. Comentários e anexos constam como recursos futuros. Por isso, acrescente à descrição do card uma seção de resultado, sem apagar objetivo e critérios. Não dependa de comentários/anexos MCP para concluir o tutorial.

Exemplo de resumo a registrar **somente após o resultado real**:

```text
Resultado DEMO-001:
- Artefato: docs/demo/primeiro-fluxo.md
- Conteúdo lido e comparado com os critérios: <resultado real>
- git status --short --untracked-files=all: <saída real pertinente>
- Resultado recebido do Developer pelo canal: <referência disponível>
- Avaliação dos critérios pelo Orchestrator: <conclusão e pendências>
- Commit: não realizado neste tutorial.
```

Após atualizar a descrição e mover para Concluído, leia o card novamente para confirmar lista e conteúdo. Se uma operação falhar, informe exatamente a pendência; não declare sucesso com base apenas na intenção de atualizar.

## Outro serviço via MCP

Você pode conectar um serviço por meio de um servidor MCP já existente ou desenvolver seu próprio conector MCP. No segundo caso, o conector expõe ao agente as operações que o serviço oferece. Em ambos, defina o mapeamento entre projeto, tarefa, estado, critérios e evidências; configure autenticação e acesso para a CLI; e verifique operações reais de leitura e atualização. Informe ao Orchestrator como identificar tarefas sem ambiguidade.

A troca de serviço preserva responsabilidades e protocolo entre sessões. Não exige reescrever os papéis, mas exige validar o conector escolhido. A existência de um contrato Task Source não comprova que um adapter específico já esteja implementado aqui.

## Indisponibilidade

Informe falhas de autenticação, autorização ou serviço sem inventar tarefas ou estados. Continue somente quando houver tarefa explicitamente fornecida pelo usuário ou Orchestrator. Se a execução local terminar e a atualização do backlog falhar, reporte ambas as condições: artefato pronto, sincronização pendente. Nunca afirme que o card foi atualizado sem retorno real.
