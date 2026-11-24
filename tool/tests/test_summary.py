from __future__ import annotations

from datetime import datetime, timedelta

from dev_orchestra.core import state, summary, tasks
from dev_orchestra.core.liveness import ClaudeSession, CodexThread, Services, collect_status
from dev_orchestra.core.models import GlobalConfig, Project, Role


def make(path, pid="demo", **roles) -> Project:
    return Project(id=pid, name=pid.title(), path=path, roles={k: Role(cli=v) for k, v in roles.items()})


def services(claude=None, codex=None, ws=None, projects=None) -> Services:
    return Services(
        claude_sessions=lambda: claude if claude is not None else [],
        codex_threads=lambda u, c: codex,
        workstreams=lambda n, c: ws if ws is not None else [],
        ai_memory_projects=lambda: projects if projects is not None else [("default", "demo")],
    )


def report(project, **kw):
    return collect_status(project, GlobalConfig(), services(**kw))


# ---------------------------------------------------------------- atividade


def test_atividade_mais_recente_primeiro_e_limite():
    total = state.ACTIVITY_LIMIT + 10
    for n in range(total):
        state.log_activity("demo", f"evento {n}", "ok")
    recent = state.recent_activity("demo", 3)
    assert [r["texto"] for r in recent] == [f"evento {total - 1}", f"evento {total - 2}", f"evento {total - 3}"]
    assert len(state.load("demo")["atividade"]) == state.ACTIVITY_LIMIT


def test_atividade_vazia_e_estado_corrompido():
    assert state.recent_activity("nada") == []
    from dev_orchestra.core import paths

    paths.state_file("demo").parent.mkdir(parents=True, exist_ok=True)
    paths.state_file("demo").write_text('{"atividade": "ruim"}', encoding="utf-8")
    assert state.recent_activity("demo") == []
    state.log_activity("demo", "ok")  # não quebra com o formato antigo
    assert len(state.recent_activity("demo")) == 1


def test_ago():
    now = datetime(2026, 11, 14, 12, 0).astimezone()

    def iso(**kw):
        return (now - timedelta(**kw)).isoformat()

    assert state.ago(iso(seconds=5), now=now) == "agora"
    assert state.ago(iso(minutes=5), now=now) == "há 5 min"
    assert state.ago(iso(hours=3), now=now) == "há 3 h"
    assert state.ago(iso(days=1), now=now) == "há 1 dia"
    assert state.ago(iso(days=4), now=now) == "há 4 dias"
    assert state.ago(None) == "-" and state.ago("lixo") == "?"


# ------------------------------------------------------------------ tarefas


def test_contadores_de_tarefas(project_dir):
    p = make(project_dir, developer="claude")
    for tid, st in [
        ("A", "draft"),
        ("B", "ready"),
        ("C", "running"),
        ("D", "delegated"),
        ("E", "needs_review"),
        ("F", "blocked"),
        ("G", "done"),
        ("H", "done"),
        ("I", "cancelled"),
    ]:
        tasks.create_task(p, tid, tid, "developer")
        tasks.set_task_state(p, tid, st)
    c = summary.task_counts(p)
    assert (c.draft, c.ready, c.running, c.review, c.problem, c.done, c.cancelled) == (1, 1, 2, 1, 1, 2, 1)
    assert c.total == 8 and c.percent == 25  # as canceladas ficam fora do progresso


def test_contadores_sem_tarefas(project_dir):
    c = summary.task_counts(make(project_dir, developer="claude"))
    assert c.total == 0 and c.percent == 0


# ------------------------------------------------------------------- resumo


def sessions(project_dir, *names, status="idle"):
    return [ClaudeSession(i, str(project_dir), n, "s", status, "interactive") for i, n in enumerate(names, 1)]


def test_saude_do_projeto(project_dir):
    p = make(project_dir, orchestrator="claude", developer="claude")
    assert summary.build_summary(p, None).health == "loading"
    todas = report(p, claude=sessions(project_dir, "demo-orchestrator", "demo-developer"))
    assert summary.build_summary(p, todas).health == "ok"
    parcial = report(p, claude=sessions(project_dir, "demo-orchestrator"))
    s = summary.build_summary(p, parcial)
    assert s.health == "warn" and (s.opened, s.total) == (1, 2)
    nenhuma = report(p, claude=[])
    assert summary.build_summary(p, nenhuma).health == "idle"
    novo = report(p, claude=[], projects=[])
    assert summary.build_summary(p, novo).health == "new"


def test_linhas_de_agentes_trazem_instrucoes_e_modelo(project_dir):
    p = make(project_dir, orchestrator="claude")
    p.roles["orchestrator"].default_model = "sonnet"
    state.mark_sent("demo", "demo-orchestrator", via="SendMessage", resume=False)
    s = summary.build_summary(p, report(p, claude=sessions(project_dir, "demo-orchestrator")))
    row = s.agents[0]
    assert (row.name, row.model, row.state, row.sent) == ("demo-orchestrator", "sonnet", "ociosa", True)
    assert row.sent_label.startswith("enviadas ") and s.sent == 1
    carregando = summary.build_summary(p, None).agents[0]
    assert carregando.state == summary.LOADING


def test_atividade_do_cartao(project_dir):
    p = make(project_dir, orchestrator="claude")
    assert "nenhuma sessão aberta" in summary.build_summary(p, report(p, claude=[])).activity
    assert (
        "demo-orchestrator ocupada"
        in summary.build_summary(
            p, report(p, claude=sessions(project_dir, "demo-orchestrator", status="busy"))
        ).activity
    )
    assert "ociosa" in summary.build_summary(p, report(p, claude=sessions(project_dir, "demo-orchestrator"))).activity
    state.log_activity("demo", "Instruções enviadas a demo-orchestrator via SendMessage", "ok")
    text = summary.build_summary(p, report(p, claude=[])).activity
    assert "Instruções enviadas" in text and "agora" in text


def test_proximo_passo(project_dir):
    p = make(project_dir, orchestrator="claude", developer="claude")
    assert "consultando" in summary.next_step(summary.build_summary(p, None))
    fechadas = summary.build_summary(p, report(p, claude=[]))
    passo = summary.next_step(fechadas)
    assert "abra demo-orchestrator, demo-developer" in passo and "tecle u" in passo
    abertas = report(p, claude=sessions(project_dir, "demo-orchestrator", "demo-developer"))
    assert "tecle i" in summary.next_step(summary.build_summary(p, abertas))
    state.mark_sent("demo", "demo-orchestrator", via="x", resume=False)
    state.mark_sent("demo", "demo-developer", via="x", resume=False)
    assert "crie uma tarefa" in summary.next_step(summary.build_summary(p, abertas))
    tasks.create_task(p, "T-1", "x", "developer")
    assert "tecle g" in summary.next_step(summary.build_summary(p, abertas))


def test_proximo_passo_sugere_importar_o_trello(project_dir):
    from dev_orchestra.core.models import TaskSource

    p = make(project_dir, orchestrator="claude")
    p.task_source = TaskSource(kind="trello", trello_board="Board")
    state.mark_sent("demo", "demo-orchestrator", via="x", resume=False)
    abertas = report(p, claude=sessions(project_dir, "demo-orchestrator"))
    assert "tecle y" in summary.next_step(summary.build_summary(p, abertas))
    state.mark_trello_import("demo", "demo-orchestrator", via="x")
    assert "crie uma tarefa" in summary.next_step(summary.build_summary(p, abertas))


# ------------------------------------------------------------ varios projetos


def test_collect_all_consulta_claude_e_ai_memory_uma_vez(tmp_path):
    chamadas = {"claude": 0, "projetos": 0}
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    def claude():
        chamadas["claude"] += 1
        return [ClaudeSession(1, str(a), "a-orchestrator", "s", "idle", "interactive")]

    def projetos():
        chamadas["projetos"] += 1
        return [("default", "a"), ("default", "b")]

    svc = Services(
        claude_sessions=claude,
        codex_threads=lambda u, c: [CodexThread("t1", "b-developer", c, "idle")],
        workstreams=lambda n, c: [],
        ai_memory_projects=projetos,
    )
    pa = make(a, pid="a", orchestrator="claude")
    pb = make(b, pid="b", developer="codex")
    reports, infra = summary.collect_all([pa, pb], GlobalConfig(), svc)
    assert chamadas == {"claude": 1, "projetos": 1}
    assert reports["a"].roles[0].state == "ociosa" and reports["b"].roles[0].state == "ociosa"
    assert infra.ai_memory_ok is True and infra.app_server is True


def test_collect_all_infra_indisponivel(tmp_path):
    p = make(tmp_path, codex_only="codex")
    svc = Services(
        claude_sessions=lambda: None,
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: None,
        ai_memory_projects=lambda: None,
    )
    reports, infra = summary.collect_all([p], GlobalConfig(), svc)
    assert infra.ai_memory_ok is False and infra.app_server is False


def test_collect_all_sem_projetos_e_sem_codex(tmp_path):
    assert summary.collect_all([], GlobalConfig(), services())[0] == {}
    p = make(tmp_path, orchestrator="claude")
    _, infra = summary.collect_all([p], GlobalConfig(), services())
    assert infra.app_server is None  # ninguém usa o App Server


def test_registro_de_atividade_nos_envios(project_dir):
    import json

    from dev_orchestra.core import dispatch

    p = make(project_dir, orchestrator="claude")
    rep = report(p, claude=sessions(project_dir, "demo-orchestrator"))
    items = dispatch.plan_instructions(p, rep)
    ok = lambda a, c=None, t=None, e=None: (0, json.dumps({"is_error": False, "result": "ok"}))  # noqa: E731
    dispatch.execute(p, GlobalConfig(), rep, items, run=ok)
    assert state.recent_activity("demo")[0]["tipo"] == "ok"
    assert "Instruções enviadas a demo-orchestrator via SendMessage" in state.recent_activity("demo")[0]["texto"]
    dispatch.execute(p, GlobalConfig(), rep, items, run=lambda a, c=None, t=None, e=None: (1, ""))
    ultimo = state.recent_activity("demo")[0]
    assert ultimo["tipo"] == "erro" and "Falha ao enviar instruções" in ultimo["texto"]
