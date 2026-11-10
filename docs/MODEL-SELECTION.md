# Seleção de modelo e esforço

O modelo e o esforço de raciocínio de um worker são escolhidos **a cada delegação**, conforme a tarefa. Não ficam fixos pelo nome do papel nem são herdados da configuração local.

## Regra

Antes de cada handoff ao Developer, Planner ou QA, o Orchestrator:

1. avalia escopo, risco, módulos afetados e critérios da tarefa;
2. recomenda `MODEL`, `EFFORT` e `MODEL_REASON`, preferindo a opção de **menor custo com capacidade suficiente**;
3. apresenta a recomendação à pessoa desenvolvedora com uma alternativa mais barata e outra mais cara, e pede aprovação explícita;
4. informa como ajustar a sessão do worker e aguarda a confirmação de que o ajuste foi feito;
5. só então envia o handoff, registrando nele os valores aprovados.

A aprovação vale para uma delegação. Uma nova tarefa, ou o reenvio após falha, exige nova avaliação. Se o worker não conseguir progredir, o Orchestrator reavalia modelo e esforço em vez de repetir a mesma configuração. Se um modelo não estiver disponível na conta, escolha uma alternativa disponível e registre-a.

## Campos no handoff

```text
MODEL: <modelo aprovado>
EFFORT: <esforço aprovado>
MODEL_REASON: <justificativa curta ligada à complexidade e ao risco desta tarefa>
```

## Pontos de partida — Codex

| Trabalho | Ponto de partida |
| --- | --- |
| Edição ou bug pequeno, isolado e com critérios claros | `gpt-6-luna`, esforço `low` |
| Mudança delimitada com alguns passos ou arquivos relacionados | `gpt-6-luna`, `medium` |
| Codificação comum que exige julgamento de domínio | `gpt-6-sol`, `medium` |
| Segurança, pagamentos, migrations ou mudança entre módulos | `gpt-6-sol`, `high`; considere Planner antes de implementar |
| Análise excepcionalmente difícil | `gpt-6-astra`, somente se disponível e se Sol não bastar |

Para coordenar, o Orchestrator pode usar `gpt-6-sol` com esforço `medium`. Fixe o esforço no comando (`-c 'model_reasoning_effort="..."'`) e confirme o valor efetivo em `/status`; não herde um `xhigh` da configuração local.

Na tabela de créditos do Codex consultada em 2026-09-25, Luna usava nominalmente 2,5/12,5 créditos por milhão de tokens de entrada/saída, contra 50/250 para Sol. Isso favorece Luna em tarefas delimitadas, mas não prevê sozinho o consumo real: contexto, ferramentas, cache, esforço e plano também contam. Revalide as taxas em [uso e créditos](https://learn.chatgpt.com/docs/pricing).

## Pontos de partida — Claude Code

| Trabalho | Ponto de partida |
| --- | --- |
| Edição ou bug pequeno, isolado e com critérios claros | `haiku`, effort `low` |
| Mudança delimitada, alguns arquivos | `sonnet`, effort `medium` |
| Codificação com julgamento de domínio | `sonnet`, effort `high` |
| Segurança, pagamentos, migrations ou mudança entre módulos | `opus`, effort `high`; considere Planner antes de implementar |
| Análise excepcionalmente difícil | `opus`, effort `xhigh`, somente com justificativa |

Os aliases `haiku`, `sonnet` e `opus` apontam para a versão mais recente de cada família disponível na conta. Defina os valores na abertura (`--model` e `--effort`) ou ajuste com `/model` dentro da sessão. Disponibilidade e limites dependem do plano.

## Como pedir a aprovação

No Claude Code, o Orchestrator usa a pergunta estruturada da CLI (AskUserQuestion), com a recomendação como primeira opção. No Codex, apresenta as opções em texto e aguarda a resposta. Exemplo:

```text
Recomendação para DEMO-001 (Developer):
1. sonnet / medium (recomendado): mudança em dois arquivos com critérios claros.
2. haiku / low: mais barato; suficiente se a mudança for só texto.
3. opus / high: mais caro; só se houver dúvida de arquitetura.
Após aprovar, ajuste a sessão developer e me confirme.
```

## Orchestrator

O modelo do Orchestrator é escolhido na abertura da sessão e revisto quando o padrão de trabalho mudar. A regra de aprovação acima vale para cada delegação a um worker.
