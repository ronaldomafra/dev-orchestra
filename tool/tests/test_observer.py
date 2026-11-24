"""Observador: o que muda nas tarefas e nas sessões vira atividade (Monitor) mesmo fora do delegador."""

from __future__ import annotations

from dev_orchestra.core import config, monitor, observer, state, tasks
from dev_orchestra.core.liveness import FECHADA, OCIOSA, OCUPADA, RoleStatus, StatusReport
from dev_orchestra.core.models import Project, Role


def report(**states) -> StatusReport:
    roles = [RoleStatus(r, "claude", s, "", name=f"demo-{r}") for r, s in states.items()]
    return StatusReport(roles=roles, workstream_names=None, app_server_up=None)


def make(tmp_path) -> Project:
    p = Project(
        id="demo", name="Demo", path=tmp_path, roles={"orchestrator": Role(cli="claude"), "analyst": Role(cli="claude")}
    )
    config.save_project(p)
    return p


def texts(project) -> list[str]:
    return [a["texto"] for a in reversed(state.recent_activity(project.id, 50))]


def test_primeira_observacao_so_guarda_o_retrato(tmp_path):
    p = make(tmp_path)
    tasks.create_task(p, "T-1", "Primeira", "analyst")
    assert observer.observe(p, report(orchestrator=OCIOSA)) == [] and texts(p) == []
    assert state.observed("demo")["tarefas"]["T-1"]["estado"] == "ready"


def test_registra_tarefa_nova_mudanca_de_estado_e_resultado(tmp_path):
    p = make(tmp_path)
    observer.observe(p, report(orchestrator=OCIOSA))  # retrato inicial vazio
    tasks.create_task(p, "T-1", "Analisar", "analyst")
    assert observer.observe(p, report(orchestrator=OCIOSA)) == ["Nova tarefa T-1 «Analisar» → analyst (pronta)"]
    tasks.set_task_state(p, "T-1", "running")
    assert observer.observe(p, report(orchestrator=OCIOSA)) == ["Tarefa T-1: pronta → em andamento"]
    file = p.tasks_path / "T-1.md"
    file.write_text(
        file.read_text()
        .replace("(preenchido pelo orquestrador ao concluir)", "Feito: números X.")
        .replace('estado = "running"', 'estado = "done"')
    )
    got = observer.observe(p, report(orchestrator=OCIOSA))
    assert got == ["Tarefa T-1: em andamento → concluída", "Tarefa T-1: resultado registrado no arquivo"]
    assert observer.observe(p, report(orchestrator=OCIOSA)) == []  # sem mudança, sem evento


def test_registra_transicoes_das_sessoes(tmp_path):
    p = make(tmp_path)
    observer.observe(p, report(orchestrator=OCIOSA, analyst=FECHADA))
    assert observer.observe(p, report(orchestrator=OCIOSA, analyst=OCIOSA)) == ["demo-analyst: sessão aberta (ociosa)"]
    assert observer.observe(p, report(orchestrator=OCIOSA, analyst=OCUPADA)) == ["demo-analyst começou a trabalhar"]
    assert observer.observe(p, report(orchestrator=OCUPADA, analyst=OCIOSA)) == [
        "demo-orchestrator começou a trabalhar",
        "demo-analyst terminou o turno",
    ]
    assert observer.observe(p, report(orchestrator=OCIOSA, analyst=FECHADA))[-1] == "demo-analyst: sessão fechada"


def test_o_monitor_mostra_o_que_o_observador_anotou(tmp_path):
    p = make(tmp_path)
    observer.observe(p, report(orchestrator=OCIOSA))
    observer.observe(p, report(orchestrator=OCUPADA))
    data = monitor.load([p], config.load_global(), None, None)
    assert any("começou a trabalhar" in e.text for e in data.entries)


def test_arquivo_de_tarefa_invalido_nao_derruba_o_observador(tmp_path):
    p = make(tmp_path)
    p.tasks_path.mkdir(parents=True)
    (p.tasks_path / "quebrada.md").write_text("sem cabeçalho")
    observer.observe(p, report(orchestrator=OCIOSA))
    assert observer.observe(p, report(orchestrator=OCIOSA)) == []
