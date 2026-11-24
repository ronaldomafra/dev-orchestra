"""Importação dos cartões do Trello para arquivos de tarefa locais, feita pelo orquestrador.

A ferramenta não fala com o Trello: o orquestrador usa o conector MCP do Trello da própria CLI, que o projeto já usa.
Aqui geramos as instruções precisas (arquivo em `.dev-orchestra/abertura/`) e a mensagem que as entrega.
"""

from __future__ import annotations

from pathlib import Path

from .config import atomic_write_text
from .importer import DEFAULT_TRELLO_LISTS
from .models import Project
from .sender import INSTRUCTIONS_DIR

IMPORT_FILE = "importar-trello.md"


def import_path(project: Project) -> Path:
    return project.path / INSTRUCTIONS_DIR / IMPORT_FILE


def import_rel() -> str:
    return f"{INSTRUCTIONS_DIR}/{IMPORT_FILE}"


def list_mapping(project: Project) -> dict[str, str]:
    return project.task_source.trello_lists or DEFAULT_TRELLO_LISTS


def import_instructions(project: Project) -> str:
    board = project.task_source.trello_board or "(o board do projeto: confirme o nome comigo)"
    mapping = "\n".join(f"   - {name} → `{state}`" for name, state in list_mapping(project).items())
    tasks_dir = project.tasks_dir
    worker = next((r for r in project.role_order() if r == "developer"), project.role_order()[0])
    return f"""# Importar os cartões do Trello para arquivos locais

Objetivo: copiar os cartões do board **{board}** para arquivos de tarefa em texto em `{tasks_dir}/`.
Esta é uma tarefa **somente de leitura do Trello**: não altere cartões, listas, etiquetas nem checklists, e **não
inicie nenhuma tarefa**. O Trello continua sendo a fonte oficial do estado; os arquivos são cópias de trabalho.

## Passos

1. Use o conector MCP do Trello para ler o board **{board}** pelo nome, ID e listas reais. Confirme por uma leitura
   direta. Se o MCP devolver IDs repetidos, lista ausente ou estado inconsistente, **pare e me avise** antes de gravar.
2. Mapeamento da lista do cartão para o campo `estado` do arquivo (listas que não estiverem aqui: `draft`, e me avise):
{mapping}
3. Para cada cartão **não arquivado**, crie `{tasks_dir}/<ID>.md` com este formato exato:

       +++
       id = "<ID>"
       titulo = "<título do cartão, sem o prefixo do ID>"
       papel = "{worker}"
       estado = "<estado conforme a lista>"
       criado = <AAAA-MM-DD da criação do cartão, ou de hoje>
       trello = "<URL do cartão>"
       lista = "<nome da lista no Trello>"
       +++

       ## Objetivo
       <descrição do cartão, preservando o texto>

       ## Critérios de aceite
       - [ ] <item de checklist pendente>
       - [x] <item de checklist concluído>

       ## Resultado
       <resultado ou evidências já registrados no cartão; se não houver: (preenchido pelo orquestrador ao concluir)>

   Regras:
   - **ID:** o prefixo do título antes de `:` quando parecer um identificador (por exemplo `MF-123`);
     senão `T-<shortLink>`.
     Use só letras, números, `.`, `-` e `_`.
   - **papel:** `{worker}` por padrão; use outro papel do projeto só se o cartão indicar (etiqueta ou descrição).
   - Preserve a descrição e os itens **como estão**; não resuma nem reescreva. Não importe anexos nem comentários.
   - Não grave segredos, credenciais nem dados de clientes nos arquivos.
4. **Idempotência:** se já existir um arquivo com o mesmo `trello`, **não o sobrescreva**; apenas liste as diferenças
   (lista, título, checklist). Peça a minha aprovação antes de alterar qualquer arquivo existente.
5. Ao final, mostre um resumo: quantos arquivos foram criados, quantos já existiam, quais divergem e quais listas ou
   cartões ficaram de fora. Depois **pare** e aguarde as minhas instruções.
"""


def write_import_file(project: Project) -> Path:
    path = import_path(project)
    atomic_write_text(path, import_instructions(project))
    return path


def import_message(project: Project) -> str:
    return (
        f"Importação do Trello do projeto {project.id}: leia {import_rel()} e siga as instruções desse arquivo. "
        "É uma tarefa de leitura: não altere o Trello, não inicie nenhuma tarefa e peça a minha aprovação antes "
        "de sobrescrever qualquer arquivo existente."
    )
