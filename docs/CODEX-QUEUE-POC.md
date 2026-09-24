# POC — comunicação entre sessões Codex

## Objetivo

Validar uma troca de mensagens entre duas sessões Codex independentes usando o mesmo App Server. O worker recebe uma tarefa e envia a confirmação ao Orchestrator sem o usuário copiar mensagens entre terminais.

Este roteiro isolado valida somente o canal, sem depender de Trello, QA ou AI Memory. É um diagnóstico opcional; o caminho principal está no [Quick Start](../README.md#quick-start-um-card-dois-agentes), que inclui Trello e AI Memory desde o início. Não é necessário abrir terminais adicionais de POC para executar aquele tutorial.

## Requisitos

- Codex CLI com codex queue disponível;
- um terminal para o App Server;
- terminais separados para Orchestrator e Developer;
- mesma URL de App Server nas duas sessões.

Confira a CLI e o comando:

    codex --version
    codex queue --help

## 1. Inicie o App Server

Confira primeiro os endpoints abaixo. Se o App Server esperado já estiver ativo, reutilize-o; não inicie outro na mesma porta. Caso não haja serviço ativo, em um terminal dedicado:

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

Use `/status` dentro da sessão de destino para obter seu Session UUID. O `RETURN_TO` é o UUID ou nome único do Orchestrator, não do worker. Na validação local, o envio pelo UUID da sessão foi aceito. Se o nome não resolver e o Codex não fornecer uma identificação segura, informe o bloqueio; não adivinhe nem inspecione processos de outros agentes para encontrar um destino.

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

<a id="historico-local-preservado"></a>

## Histórico local preservado

As evidências a seguir descrevem o ambiente do POC, não uma garantia para toda instalação. Registros locais consolidados em 2026-09-23:

| Verificação | Evidência e limite |
| --- | --- |
| App Server | Iniciou e respondeu HTTP 200 em `/readyz` e `/healthz` |
| Sandbox Ubuntu | `codex sandbox /bin/true` terminou sem erro após carregar o perfil AppArmor descrito acima |
| COMM-001 | Worker recebeu e devolveu confirmação via `codex queue` ao UUID do Orchestrator, sem alterar arquivos |
| COMM-002 | Confirmação devolvida pelo canal, sem alterar arquivos, conforme histórico fornecido; não há ID de mensagem adicional neste registro |
| COMM-003 | Nome `desenvolvedor-novo` falhou na delegação; UUID confirmado funcionou; retorno chegou pelo canal sem alterações em arquivos |
| Workstream `developer` | `linked_harnesses` continha `codex`; comprova associação, não retomada completa |
| Workstream `developer_new` | Sem harness vinculado; não é evidência de continuidade |

No COMM-003, o retorno foi aceito como mensagem `01a0d0b9-9444-7f82-a3f2-f7ea463cd633`, destinada ao Orchestrator `01a0cfff-2d85-7213-85fe-5004dfb842ea`. Esses identificadores são históricos; não os reutilize no tutorial. Obtenha os UUIDs das suas sessões por `/status`.

O título Codex `desenvolvedor` também foi usado com a chave AI Memory `developer`. O erro `managed workstream not found` ao selecionar `desenvolvedor` decorre dessa distinção. Na retomada, use a chave original; não acrescente `resume <UUID>` depois de `codex --remote` no caminho gerenciado.

A integração completa Trello + AI Memory, a recuperação de conhecimento durável e a retomada gerenciada ainda não possuem evidência de validação de ponta a ponta neste registro. O [SETUP](SETUP.md#verificar-memoria-e-continuidade) descreve verificações separadas de vínculo, continuidade e memória.
