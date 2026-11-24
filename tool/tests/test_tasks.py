from __future__ import annotations

from datetime import date

import pytest

from dev_orchestra.core import tasks
from dev_orchestra.core.tasks import TaskError

EXEMPLO = """+++
id = "DEMO-001"
titulo = "Meu primeiro fluxo"
papel = "developer"
estado = "ready"
modelo = "gpt-6-luna"
esforco = "low"
criado = 2026-11-14
+++

## Objetivo
Criar docs/demo/primeiro-fluxo.md.

## Critérios de aceite
- [ ] Criar somente esse arquivo.

## Resultado
(preenchido pelo orquestrador ao concluir)
"""


def test_parse_exemplo_da_especificacao():
    t = tasks.parse_task(EXEMPLO)
    assert (t.id, t.title, t.role, t.state) == ("DEMO-001", "Meu primeiro fluxo", "developer", "ready")
    assert (t.model, t.effort, t.created) == ("gpt-6-luna", "low", date(2026, 11, 14))
    assert t.body.startswith("## Objetivo")


def test_render_e_parse_ida_e_volta():
    original = tasks.parse_task(EXEMPLO)
    again = tasks.parse_task(tasks.render_task(original))
    assert (again.id, again.title, again.role, again.state, again.model, again.effort, again.created) == (
        original.id,
        original.title,
        original.role,
        original.state,
        original.model,
        original.effort,
        original.created,
    )
    assert again.body == original.body


def test_resultado_ignora_marcador():
    assert tasks.parse_task(EXEMPLO).result_text() == ""


def test_resultado_preenchido():
    text = EXEMPLO.replace("(preenchido pelo orquestrador ao concluir)", "STATUS: DONE\nEVIDENCE: ok")
    assert tasks.parse_task(text).result_text() == "STATUS: DONE\nEVIDENCE: ok"


@pytest.mark.parametrize(
    ("texto", "trecho"),
    [
        ("sem cabeçalho", "começar com uma linha"),
        ("+++\nid = 'x'\n", "fecha os metadados"),
        ("+++\nid = [\n+++\n", "TOML inválidos"),
        ('+++\nid = "A"\n+++\n', "obrigatório ausente"),
        ('+++\nid = "A"\ntitulo = "t"\npapel = "p"\nestado = "voando"\n+++\n', "estado inválido"),
        ('+++\nid = "A"\ntitulo = "t"\npapel = "p"\ncriado = "ontem"\n+++\n', "data TOML"),
        ('+++\nid = "../x"\ntitulo = "t"\npapel = "p"\n+++\n', "id de tarefa inválido"),
    ],
)
def test_erros_de_formato(texto, trecho):
    with pytest.raises(TaskError, match=trecho):
        tasks.parse_task(texto)


def test_criar_listar_e_mudar_estado(project):
    t = tasks.create_task(project, "T-1", "Primeira", "developer", objective="Fazer X")
    assert t.model == "gpt-6-luna" and t.effort == "low"  # herda o padrão do papel
    assert t.path == project.tasks_path / "T-1.md"
    listed, warnings = tasks.list_tasks(project)
    assert [x.id for x in listed] == ["T-1"] and warnings == []
    assert "Fazer X" in listed[0].body

    done = tasks.set_task_state(project, "T-1", "done")
    assert done.state == "done"
    assert tasks.read_task(project, "T-1").state == "done"


def test_criar_tarefa_duplicada(project):
    tasks.create_task(project, "T-1", "Primeira", "developer")
    with pytest.raises(TaskError, match="já existe"):
        tasks.create_task(project, "T-1", "Outra", "developer")


def test_criar_tarefa_para_papel_inexistente(project):
    with pytest.raises(TaskError, match="papel desconhecido"):
        tasks.create_task(project, "T-1", "x", "qa")


def test_estado_invalido(project):
    tasks.create_task(project, "T-1", "x", "developer")
    with pytest.raises(TaskError, match="estado inválido"):
        tasks.set_task_state(project, "T-1", "voando")


def test_listagem_reporta_arquivo_invalido(project):
    tasks.create_task(project, "T-1", "ok", "developer")
    (project.tasks_path / "ruim.md").write_text("lixo", encoding="utf-8")
    listed, warnings = tasks.list_tasks(project)
    assert [t.id for t in listed] == ["T-1"]
    assert len(warnings) == 1 and warnings[0].startswith("ruim.md")


def test_listar_sem_pasta_de_tarefas(project):
    assert tasks.list_tasks(project) == ([], [])


def test_mudar_estado_preserva_o_corpo(project):
    tasks.create_task(project, "T-1", "x", "developer", objective="Objetivo especial")
    tasks.set_task_state(project, "T-1", "running")
    assert "Objetivo especial" in tasks.read_task(project, "T-1").body


# ------------------------------------------------- arquivos escritos por agentes de IA (nomes variados)

AI_WRITTEN = """+++
id = "meu-bot-001"
titulo = "Homologação"
estado = "em_andamento"
modelo = "sonnet"
responsavel = "meu-bot-bot-analise"
criada = "2026-11-15"
+++

## Resultado
(preenchido pelo orquestrador ao concluir)
"""


def test_aceita_nomes_variados_de_agentes_de_ia(tmp_path):
    from dev_orchestra.core.models import Project, Role

    project = Project(
        id="trade", name="T", path=tmp_path, prefix="meu-bot",
        roles={"orchestrator": Role(cli="claude"), "bot-analise": Role(cli="claude")},
    )  # fmt: skip
    task = tasks.parse_task(AI_WRITTEN, project=project)
    assert task.role == "bot-analise"  # o nome da sessão vira o id do papel
    assert task.state == "running" and str(task.created) == "2026-11-15"
    assert (
        tasks.parse_task(AI_WRITTEN.replace('"meu-bot-bot-analise"', '"orchestrator"'), project=project).role
        == "orchestrator"
    )
    assert tasks.parse_task(AI_WRITTEN.replace("em_andamento", "Concluída")).state == "done"
    sem_papel = AI_WRITTEN.replace('responsavel = "meu-bot-bot-analise"\n', "")
    with pytest.raises(tasks.TaskError, match="papel"):
        tasks.parse_task(sem_papel)
    with pytest.raises(tasks.TaskError, match="estado inválido"):
        tasks.parse_task(AI_WRITTEN.replace("em_andamento", "quase"))


def test_listar_tarefas_do_projeto_usa_a_resolucao_de_papel(tmp_path):
    from dev_orchestra.core.models import Project, Role

    project = Project(id="trade", name="T", path=tmp_path, prefix="meu-bot", roles={"bot-analise": Role(cli="claude")})
    project.tasks_path.mkdir(parents=True)
    (project.tasks_path / "meu-bot-001.md").write_text(AI_WRITTEN)
    found, warnings = tasks.list_tasks(project)
    assert warnings == [] and [(t.id, t.role) for t in found] == [("meu-bot-001", "bot-analise")]
