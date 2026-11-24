"""Modo delegador no cadastro: textos de abertura, comando de subida, aviso e mensagem de tarefa."""

from __future__ import annotations

from dev_orchestra.core import sender, tasks
from dev_orchestra.core.commands import (
    MODE_DELEGATOR,
    LaunchContext,
    build_launch_plan,
    local_context,
    opening_prompt,
    transport_mode,
)
from dev_orchestra.core.models import GlobalConfig, Project, Role


def make(tmp_path, delegator=True, **kw) -> Project:
    roles = {"orchestrator": Role(cli="claude", default_model="sonnet"), "developer": Role(cli="claude")}
    return Project(id="demo", name="Demo", path=tmp_path, roles=roles, delegator=delegator, **kw)


def test_modo_e_delegador_mesmo_com_clis_diferentes(tmp_path):
    p = make(tmp_path)
    p.roles["developer"] = Role(cli="codex")
    assert transport_mode(p) == MODE_DELEGATOR
    assert transport_mode(make(tmp_path, delegator=False)) == "claude-code"


def test_abertura_manda_registrar_e_usar_so_o_delegador(tmp_path):
    p = make(tmp_path)
    text = opening_prompt(p, GlobalConfig(), "developer", MODE_DELEGATOR, LaunchContext())
    assert '"demo-developer"' in text and "register_session" in text
    assert 'project="demo", role="developer", cli="claude", session_ref="demo-developer"' in text
    assert "SOMENTE pelas ferramentas do delegador" in text and "receive" in text
    orch = opening_prompt(p, GlobalConfig(), "orchestrator", MODE_DELEGATOR, LaunchContext())
    assert "Aguarde eu informar a tarefa" in orch and ".dev-orchestra/tarefas" in orch


def test_abertura_do_worker_nao_some_por_ter_definicao_de_agente(tmp_path):
    p = make(tmp_path)
    ctx = LaunchContext(agent_defs=frozenset({"developer"}))
    assert opening_prompt(p, GlobalConfig(), "developer", MODE_DELEGATOR, ctx)  # o registro é obrigatório


def test_projeto_importado_cita_os_documentos_proprios(tmp_path):
    p = make(tmp_path, method="proprio", references=["AGENTS.md", "docs/FLUXO.md"])
    text = opening_prompt(p, GlobalConfig(), "developer", MODE_DELEGATOR, LaunchContext())
    assert "AGENTS.md, docs/FLUXO.md" in text and "acima de qualquer outro documento" in text


def test_comando_de_subida_leva_o_nome_do_agente_para_o_listener(tmp_path):
    p = make(tmp_path)
    plan = build_launch_plan(p, GlobalConfig(), local_context(p, workstreams=set()))
    lines = [c for b in plan.blocks for c in b.commands if "ai-memory run" in c]
    assert lines and lines[0].startswith("DEV_ORCHESTRA_AGENT=demo-orchestrator ai-memory run")
    assert lines[1].startswith("DEV_ORCHESTRA_AGENT=demo-developer ai-memory run")
    plain = build_launch_plan(make(tmp_path, delegator=False), GlobalConfig(), local_context(p, workstreams=set()))
    assert not any("DEV_ORCHESTRA_AGENT" in c for b in plain.blocks for c in b.commands)


def test_avisa_quando_o_listener_nao_esta_instalado(tmp_path):
    p = make(tmp_path)
    plan = build_launch_plan(p, GlobalConfig(), local_context(p, workstreams=set()))
    assert any("delegator install demo" in w for w in plan.warnings)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"x": "dev-orchestra delegator listen"}')
    plan = build_launch_plan(p, GlobalConfig(), local_context(p, workstreams=set()))
    assert not any("delegator install" in w for w in plan.warnings)
    assert not any("Os papéis usam CLIs diferentes" in w for w in plan.warnings)


def test_tarefa_ao_orquestrador_manda_usar_delegate(tmp_path):
    p = make(tmp_path)
    task = tasks.Task(id="T-1", title="Melhorar a página", role="developer")
    text = sender.task_message(p, task)
    assert "`delegate`" in text and "user_approved=true" in text and "não use SendMessage" in text


# ------------------------------------------------------------------- TUI


async def test_tui_mostra_delegador_no_topo_nos_cartoes_e_na_atividade(tmp_path):
    from rich.text import Text
    from textual.widgets import Static

    from dev_orchestra.core import config
    from dev_orchestra.core.liveness import ClaudeSession, Services
    from dev_orchestra.tui.app import DevOrchestraApp
    from dev_orchestra.tui.widgets import AgentCard, ProjectCard, Tile

    p = make(tmp_path)
    config.save_project(p)
    status = {
        "project": "demo",
        "sessions": [
            {
                "role": "orchestrator",
                "active": True,
                "alive": True,
                "last_seen": "2099-01-01T00:00:00+00:00",
                "pending": 0,
            },
            {
                "role": "developer",
                "active": True,
                "alive": False,
                "last_seen": "2020-01-01T00:00:00+00:00",
                "pending": 1,
            },
        ],
        "tasks": [],
    }
    events = [{"ts": "2026-11-15T12:00:00+00:00", "kind": "delegate", "text": "T-1 -> developer"}]
    sessions = [
        ClaudeSession(1, str(tmp_path), "demo-orchestrator", "s", "idle", "interactive"),
        ClaudeSession(2, str(tmp_path), "demo-developer", "s", "idle", "interactive"),
    ]
    svc = Services(
        claude_sessions=lambda: sessions,
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [("default", "demo")],
        delegator_status=lambda url, project: status,
        delegator_events=lambda url, project: events,
    )
    app = DevOrchestraApp(svc)
    async with app.run_test(size=(170, 60)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Delegador :8787 no ar" in str(app.screen.query_one("#tile-infra", Tile).render())
        assert "DELEGADOR" in str(app.screen.query_one(ProjectCard).render())
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("2")
        await pilot.pause()
        cards = list(app.screen.query(AgentCard))
        assert "registrado · sinal" in str(cards[0].render())
        assert "sem sinal" in str(cards[1].render()) and "1 pendente(s)" in str(cards[1].render())
        assert isinstance(Text(""), Text) and "[delegador] T-1 -> developer" in str(
            app.screen.query_one("#activity", Static).render()
        )


async def test_tui_delegador_fora_do_ar(tmp_path):
    from dev_orchestra.core import config
    from dev_orchestra.core.liveness import Services
    from dev_orchestra.tui.app import DevOrchestraApp
    from dev_orchestra.tui.widgets import Tile

    config.save_project(make(tmp_path))
    svc = Services(
        claude_sessions=lambda: [],
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [("default", "demo")],
        delegator_status=lambda url, project: None,
    )
    app = DevOrchestraApp(svc)
    async with app.run_test(size=(170, 60)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Delegador :8787 fora do ar" in str(app.screen.query_one("#tile-infra", Tile).render())
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("2")
        await pilot.pause()
        from dev_orchestra.tui.widgets import AgentCard

        assert "delegador fora do ar" in str(app.screen.query(AgentCard).first().render())


async def test_tui_cancela_delegacao_pela_pessoa(tmp_path, monkeypatch):
    from dev_orchestra.core import config
    from dev_orchestra.core.liveness import Services
    from dev_orchestra.delegator import client as client_mod
    from dev_orchestra.tui.app import DevOrchestraApp
    from dev_orchestra.tui.forms import ConfirmScreen
    from dev_orchestra.tui.widgets import TaskCard

    p = make(tmp_path)
    config.save_project(p)
    tasks.create_task(p, "T-1", "x", "developer")
    calls = []
    monkeypatch.setattr(
        client_mod.DelegatorClient,
        "cancel",
        lambda self, project, task, reason="": (
            calls.append((project, task)) or {"task_id": task, "task_state": "cancelled"}
        ),
    )
    svc = Services(
        claude_sessions=lambda: [],
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [("default", "demo")],
        delegator_status=lambda url, project: {"sessions": [], "tasks": []},
    )
    app = DevOrchestraApp(svc)
    async with app.run_test(size=(170, 60)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("3")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.focused, TaskCard)
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#yes")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert calls == [("demo", "T-1")]


def test_sessao_ocupada_sem_sinal_nao_aparece_como_perdida(tmp_path):
    from dev_orchestra.core.liveness import AiMemoryState, RoleStatus, StatusReport
    from dev_orchestra.core.summary import build_summary

    p = make(tmp_path)
    stale = {
        "role": "developer",
        "active": True,
        "alive": False,
        "last_seen": "2020-01-01T00:00:00+00:00",
        "pending": 0,
    }
    roles = [
        RoleStatus("orchestrator", "claude", "ociosa", "", name="demo-orchestrator"),
        RoleStatus("developer", "claude", "ocupada", "", name="demo-developer"),
    ]
    report = StatusReport(
        roles=roles,
        workstream_names=set(),
        app_server_up=None,
        ai_memory=AiMemoryState(True, True, 1),
        delegator={"sessions": [stale]},
        delegator_used=True,
    )
    rows = {a.role: a for a in build_summary(p, report).agents}
    assert rows["developer"].deleg_state == "ok" and "trabalhando" in rows["developer"].deleg
    assert rows["orchestrator"].deleg_state == "none"  # ainda não se registrou
    roles[1] = RoleStatus("developer", "claude", "ociosa", "", name="demo-developer")
    assert {a.role: a for a in build_summary(p, report).agents}["developer"].deleg_state == "stale"


def test_instrucoes_do_modo_antigo_nao_valem_no_modo_delegador(tmp_path):
    from dev_orchestra.core import state

    state.mark_sent("demo", "demo-developer", via="SendMessage", resume=False)  # enviadas antes do modo delegador
    assert state.sent_info("demo", "demo-developer") is not None
    assert state.sent_info("demo", "demo-developer", "padrao") is not None
    assert state.sent_info("demo", "demo-developer", "delegador") is None  # o texto mudou: pendentes
    state.mark_sent("demo", "demo-developer", via="SendMessage", resume=False, mode="delegador")
    assert state.sent_info("demo", "demo-developer", "delegador") is not None
    assert state.sent_info("demo", "demo-developer", "padrao") is None


# ----------------------------------- estado de execução do delegador sobre o arquivo da tarefa


def _task(tid, state):
    return tasks.Task(id=tid, title=tid, role="developer", state=state)


def test_apply_live_sobrepoe_o_estado_do_arquivo():
    from dev_orchestra.core.summary import apply_live

    items = [_task("A", "ready"), _task("B", "ready"), _task("C", "ready"), _task("D", "done"), _task("E", "ready")]
    live = [
        {"task_id": "A", "state": "running"},
        {"task_id": "B", "state": "asked"},
        {"task_id": "C", "state": "done"},  # o DONE do worker ainda precisa da conferência do orquestrador
        {"task_id": "D", "state": "running"},  # o arquivo já diz done: o arquivo vale
    ]
    out = {t.id: (t.state, t.live) for t in apply_live(items, live)}
    assert out["A"] == ("running", "running") and out["B"] == ("asked", "asked")
    assert out["C"] == ("needs_review", "done")
    assert out["D"] == ("done", None) and out["E"] == ("ready", None)
    assert apply_live(items, None) is items and apply_live(items, []) is items


async def test_tui_mostra_a_execucao_do_delegador_no_kanban_na_tabela_e_nos_contadores(tmp_path):
    from textual.widgets import DataTable

    from dev_orchestra.core import config
    from dev_orchestra.core.liveness import Services
    from dev_orchestra.tui.app import DevOrchestraApp
    from dev_orchestra.tui.widgets import KanbanBoard, TaskCard

    p = make(tmp_path)
    config.save_project(p)
    tasks.create_task(p, "T-1", "Refatoração", "developer")  # o arquivo continua `ready`
    live = {"sessions": [], "tasks": [{"task_id": "T-1", "state": "running"}]}
    svc = Services(
        claude_sessions=lambda: [],
        codex_threads=lambda u, c: None,
        workstreams=lambda n, c: [],
        ai_memory_projects=lambda: [("default", "demo")],
        delegator_status=lambda url, project: live,
        delegator_events=lambda url, project: [],
    )
    app = DevOrchestraApp(svc)
    async with app.run_test(size=(170, 60)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.screen.query_one("#tasks", DataTable)
        assert table.get_row("T-1")[1].plain == "● executando ↻"
        board = app.screen.query_one(KanbanBoard)
        columns = {str(c.border_title): list(c.query(TaskCard)) for c in board.children}
        running = next(cards for title, cards in columns.items() if "Executando" in title)
        assert [c.task_id for c in running] == ["T-1"]
        assert "↻ running no delegador" in str(running[0].render())
        assert "1 executando" in str(app.screen.query_one("#stepper").render())  # contadores usam o estado vivo
        live["tasks"][0]["state"] = "asked"  # o worker perguntou: a tela acompanha sem mexer no arquivo
        await pilot.press("r")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert table.get_row("T-1")[1].plain == "● pergunta ↻"


# ------------------------------------------------ sessão adotada e permissões por papel


def test_cadastro_guarda_sessao_adotada_e_permissao(tmp_path):
    from dev_orchestra.core import config

    p = make(tmp_path)
    p.roles["developer"] = Role(cli="claude", resume="20500d68-107a", resume_fork=True)
    p.roles["orchestrator"] = Role(cli="claude", permission="plan")
    config.save_project(p)
    back = config.load_project("demo")
    assert back.roles["developer"].resume == "20500d68-107a" and back.roles["developer"].resume_fork is True
    assert back.roles["orchestrator"].permission == "plan" and back.roles["orchestrator"].resume is None
    text = config.paths.project_file("demo").read_text()
    assert 'sessao = "20500d68-107a"' in text and 'permissao = "plan"' in text
    portable = config.project_to_dict(back, portable=True)["papeis"]
    assert "sessao" not in portable["developer"] and portable["orchestrator"]["permissao"] == "plan"


def dev_command(p, workstreams):
    plan = build_launch_plan(p, GlobalConfig(), local_context(p, workstreams=workstreams))
    return next(b for b in plan.blocks if b.title.startswith("Developer")).commands[-1], plan


def test_cadastro_guarda_a_origem_da_copia(tmp_path):
    from dev_orchestra.core import config

    p = make(tmp_path)
    p.roles["developer"] = Role(cli="claude", resume="copia-1111", resume_fork=True, resume_origin="origem-2222")
    config.save_project(p)
    back = config.load_project("demo").roles["developer"]
    assert (back.resume, back.resume_origin, back.resume_fork) == ("copia-1111", "origem-2222", True)
    assert 'sessao_origem = "origem-2222"' in config.paths.project_file("demo").read_text()
    assert (
        "sessao_origem" not in config.project_to_dict(config.load_project("demo"), portable=True)["papeis"]["developer"]
    )


def test_copia_ja_criada_sobe_com_ai_memory_sem_fork_session(tmp_path):
    p = make(tmp_path)
    p.roles["developer"] = Role(
        cli="claude", resume="copia-1111-aaaa", resume_fork=True, resume_origin="origem-2222-bbbb", default_model="opus"
    )
    cmd, plan = dev_command(p, set())
    expected = (
        "DEV_ORCHESTRA_AGENT=demo-developer ai-memory run --project demo --new demo-developer claude "
        "--resume copia-1111-aaaa --name demo-developer --model opus"
    )
    assert cmd == expected and "--fork-session" not in cmd
    warning = next(w for w in plan.warnings if "CÓPIA" in w)
    assert "copia-11" in warning and "origem-2" in warning and "intacta" in warning


def test_copia_ainda_nao_criada_avisa_para_salvar_o_cadastro(tmp_path):
    p = make(tmp_path)
    p.roles["developer"] = Role(cli="claude", resume="20500d68-107a", resume_fork=True)
    cmd, plan = dev_command(p, set())
    assert "--resume 20500d68-107a --fork-session" in cmd
    assert any("ainda não foi criada" in w and "ORIGINAL" in w for w in plan.warnings)


def test_sessao_sem_copia_avisa_que_altera_a_original(tmp_path):
    p = make(tmp_path)
    p.roles["developer"] = Role(cli="claude", resume="20500d68-107a", resume_fork=False)
    cmd, plan = dev_command(p, set())
    assert "--resume 20500d68-107a" in cmd and "--fork-session" not in cmd
    assert any("SEM cópia" in w for w in plan.warnings)


def test_workstream_existente_so_retoma_sem_resume(tmp_path):
    p = make(tmp_path)
    p.roles["developer"] = Role(cli="claude", resume="copia-1111", resume_fork=True, resume_origin="origem-2222")
    cmd, plan = dev_command(p, {"demo-developer"})
    assert "--workstream demo-developer" in cmd and "--resume" not in cmd and "--fork-session" not in cmd
    assert any("já virou o workstream" in w for w in plan.warnings)


def test_permissao_somente_leitura_e_plan_viram_flags_do_claude(tmp_path):
    p = make(tmp_path)
    p.roles["developer"] = Role(cli="claude", permission="read-only")
    p.roles["orchestrator"] = Role(cli="claude", permission="plan")
    plan = build_launch_plan(p, GlobalConfig(), local_context(p, workstreams=set()))
    commands = {b.title.split()[0]: b.commands[-1] for b in plan.blocks}
    assert commands["Developer"].endswith("--disallowedTools Edit Write NotebookEdit")
    assert commands["Orchestrator"].endswith("--permission-mode plan")
