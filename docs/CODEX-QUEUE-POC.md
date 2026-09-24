# POC — comunicação entre sessões Codex

## Objetivo

Validar uma troca de mensagens entre duas sessões Codex independentes usando o mesmo App Server. O worker recebe uma tarefa e envia a confirmação ao Orchestrator sem o usuário copiar mensagens entre terminais.

Esta etapa valida somente o canal. Não usa Trello, QA ou AI Memory.

## Requisitos

- Codex CLI com codex queue disponível;
- um terminal para o App Server;
- terminais separados para Orchestrator e Developer;
- mesma URL de App Server nas duas sessões.

Confira a CLI e o comando:

    codex --version
    codex queue --help

## 1. Inicie o App Server

Em um terminal dedicado:

    codex app-server --listen ws://127.0.0.1:4500

Mantenha o processo aberto. Em outro terminal, os endpoints de saúde devem responder HTTP 200:

    curl -i http://127.0.0.1:4500/readyz
    curl -i http://127.0.0.1:4500/healthz

## 2. Inicie o Orchestrator

Em outro terminal, na raiz do projeto:

    codex --remote ws://127.0.0.1:4500

Dentro da sessão, defina o nome:

    /rename orchestrator-poc

Carregue AGENTS.md e roles/orchestrator.md.

## 3. Inicie o Developer

Em um terminal separado, no mesmo projeto:

    codex --remote ws://127.0.0.1:4500

Dentro da sessão, defina o nome:

    /rename developer-poc

Carregue AGENTS.md e roles/developer.md.

## 4. Envie uma tarefa sem alterar arquivos

O Orchestrator envia ao Developer:

    codex queue --remote ws://127.0.0.1:4500 --thread developer-poc --message 'TASK: COMM-001
    INSTRUCTION: Confirme o recebimento. Não crie nem altere arquivos.
    RETURN_TO: orchestrator-poc'

Depois do envio, o Orchestrator encerra o turno e aguarda. Não verifica arquivos ou processos para inferir conclusão.

## 5. Envie o resultado

Depois de receber a tarefa, o Developer envia:

    codex queue --remote ws://127.0.0.1:4500 --thread orchestrator-poc --message 'STATUS: DONE
    TASK: COMM-001
    SUMMARY: Confirmei o recebimento da tarefa.
    EVIDENCE: A mensagem foi recebida; nenhum arquivo foi alterado.'

## Identificação da sessão

O Codex aceita o nome exato da sessão ou seu UUID como valor de thread. Use /rename para dar nomes distintos e tente o nome exato primeiro. Se o Codex retornar o UUID correspondente, repita o envio usando esse UUID. Não escolha uma sessão ambígua por tentativa.

Na validação local, o envio pelo UUID da sessão foi aceito. Se o nome não resolver e o Codex não fornecer uma identificação segura, informe o bloqueio; não adivinhe nem inspecione processos de outros agentes para encontrar um destino.

## Critério de sucesso

- O Developer recebe COMM-001 pelo canal.
- O Developer devolve STATUS, TASK, SUMMARY e EVIDENCE pelo mesmo canal.
- O Orchestrator recebe o resultado em sua sessão.
- Nenhum arquivo é alterado pelo teste.

O teste COMM-001 foi confirmado em 2026-09-23. A mensagem de retorno chegou ao Orchestrator via codex queue usando o UUID da sessão, sem alteração de arquivos. A troca entre sessões está validada.

## Se o sandbox falhar no Ubuntu 24.04

Use estes passos somente quando o Codex reportar que o AppArmor impede a criação de user namespaces. Os comandos foram validados neste ambiente em 2026-09-23:

    sudo apt update
    sudo apt install bubblewrap apparmor-profiles apparmor-utils
    sudo install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/bwrap-userns-restrict
    sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
    sudo aa-status | grep bwrap
    codex sandbox /bin/true

O perfil deve aparecer carregado e o comando de sandbox deve terminar sem erro. Consulte também a [documentação oficial do sandbox do Codex](https://developers.openai.com/pt-BR/docs/sandboxing).

## Limite do POC

O teste confirma o transporte entre sessões. Ele não valida a integração do Task Source, AI Memory, QA, branches/worktrees, vários workers ou troca de CLI.
