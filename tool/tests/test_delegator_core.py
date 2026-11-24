"""Regras do delegador: registro, permissões, delegar/receber/reportar/perguntar/responder/cancelar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dev_orchestra.core.models import Project, Role
from dev_orchestra.delegator.core import MAX_BODY, Delegator
from dev_orchestra.delegator.db import Store
from dev_orchestra.delegator.errors import DelegatorError


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Registry:
    def __init__(self, tmp_path) -> None:
        roles = {r: Role(cli="claude") for r in ("orchestrator", "developer", "qa")}
        self.projects = {"demo": Project(id="demo", name="Demo", path=tmp_path, roles=roles)}
        self.projects["other"] = Project(
            id="other", name="Other", path=tmp_path, roles={"orchestrator": Role(cli="codex")}
        )

    def get(self, project_id):
        return self.projects.get(project_id)


class Recorder:
    """Waker de teste: guarda quem foi acordado."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, int]] = []

    def notify(self, project, role, session, message_id):
        self.calls.append((role, session["id"] if session else None, message_id))
        return "recorded"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def waker():
    return Recorder()


@pytest.fixture
def d(tmp_path, clock, waker):
    return Delegator(Store(tmp_path / "estado.db"), Registry(tmp_path), waker, clock=clock)


def reg(d, role, ref=None, project="demo", cli="claude"):
    return d.register_session(project, role, cli, ref or f"demo-{role}")["session_id"]


def delegate(d, orch, task="T-1", to="developer", **kw):
    args = dict(instruction="faça X", model="haiku", effort="low", model_reason="simples", user_approved=True)
    args.update(kw)
    return d.delegate(orch, task, to, **args)


def report(d, sid, task="T-1", status="DONE"):
    return d.report(sid, task, status, "feito", "arquivo alterado")


# ------------------------------------------------------------------- registro


def test_register_devolve_sessao_e_perfil(d):
    out = d.register_session("demo", "developer", "claude", "demo-developer")
    assert out["session_id"].startswith("s-") and out["role_kind"] == "worker"
    assert "Developer" in out["profile"] and "register_session" in out["profile"]
    assert out["agent_name"] == "demo-developer" and out["pending_messages"] == 0


def test_register_valida_projeto_papel_e_cli(d):
    with pytest.raises(DelegatorError, match="unknown project"):
        d.register_session("nope", "developer", "claude", "x")
    with pytest.raises(DelegatorError, match="not in project"):
        d.register_session("demo", "planner", "claude", "x")
    with pytest.raises(DelegatorError, match="configured for cli 'claude'"):
        d.register_session("demo", "developer", "codex", "x")
    with pytest.raises(DelegatorError, match="session_ref"):
        d.register_session("demo", "developer", "claude", "  ")


def test_registrar_de_novo_reusa_a_sessao(d):
    a = reg(d, "developer")
    assert reg(d, "developer") == a
    assert reg(d, "developer", ref="outro") != a  # workers podem ser vários


def test_novo_orquestrador_substitui_o_anterior(d):
    old = reg(d, "orchestrator", ref="o1")
    new = reg(d, "orchestrator", ref="o2")
    assert old != new
    with pytest.raises(DelegatorError, match="replaced"):
        d.heartbeat(old)
    assert d.heartbeat(new)["ok"]


def test_sessao_desconhecida(d):
    with pytest.raises(DelegatorError, match="unknown session_id"):
        d.receive("s-nada")


# ----------------------------------------------------------------- permissões


def test_so_o_orquestrador_delega(d):
    reg(d, "orchestrator")
    dev = reg(d, "developer")
    with pytest.raises(DelegatorError, match="not allowed to call delegate"):
        delegate(d, dev)


def test_worker_nao_responde_nem_orquestrador_reporta(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.receive(dev)
    with pytest.raises(DelegatorError, match="not allowed to call answer"):
        d.answer(dev, 1, "x")
    with pytest.raises(DelegatorError, match="not allowed to call report"):
        d.report(orch, "T-1", "DONE", "s", "e")
    with pytest.raises(DelegatorError, match="not allowed to call ask"):
        d.ask(orch, "T-1", "q")


def test_delegate_exige_aprovacao_modelo_e_papel_valido(d):
    orch = reg(d, "orchestrator")
    with pytest.raises(DelegatorError, match="user_approved"):
        delegate(d, orch, user_approved=False)
    with pytest.raises(DelegatorError, match="model and effort"):
        delegate(d, orch, model="")
    with pytest.raises(DelegatorError, match="worker role"):
        delegate(d, orch, to="orchestrator")
    with pytest.raises(DelegatorError, match="worker role"):
        delegate(d, orch, to="planner")
    with pytest.raises(DelegatorError, match="invalid task_id"):
        delegate(d, orch, task="a b")
    with pytest.raises(DelegatorError, match="instruction"):
        delegate(d, orch, instruction=" ")


# ------------------------------------------------------------- fluxo completo


def test_delegar_receber_reportar(d, waker):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    out = delegate(d, orch)
    assert out["task_state"] == "delegated" and out["delivered_to_session"] == dev and out["wake"] == "recorded"
    assert waker.calls[-1] == ("developer", dev, out["message_id"])
    got = d.receive(dev)["message"]
    assert got["type"] == "delegate" and got["task_id"] == "T-1" and "INSTRUCTION:\nfaça X" in got["body"]
    assert "MODEL: haiku" in got["body"] and "ATTEMPT: 1" in got["body"]
    assert d.status_data("demo")["tasks"][0]["state"] == "running"
    res = report(d, dev)
    assert res["accepted"] and res["task_state"] == "done"
    assert waker.calls[-1][0] == "orchestrator"
    back = d.receive(orch)["message"]
    assert back["type"] == "result" and back["body"].startswith("STATUS: DONE\nTASK: T-1")
    assert d.receive(orch)["message"] is None  # uso único


def test_mensagem_e_de_uso_unico_e_ordenada(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch, "A")
    delegate(d, orch, "B")
    assert d.receive(dev)["message"]["task_id"] == "A"
    second = d.receive(dev)
    assert second["message"]["task_id"] == "B" and second["remaining"] == 0


def test_uma_sessao_nao_consome_mensagem_de_outra(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    qa = reg(d, "qa")
    out = delegate(d, orch)
    assert d.receive(qa)["message"] is None  # endereçada ao developer
    with pytest.raises(DelegatorError, match="not addressed"):
        d.receive(qa, out["message_id"])
    with pytest.raises(DelegatorError, match="not addressed"):
        d.receive(orch, out["message_id"])  # nem o remetente
    assert d.receive(dev, out["message_id"])["message"]["task_id"] == "T-1"
    with pytest.raises(DelegatorError, match="already claimed"):
        d.receive(dev, out["message_id"])


def test_mensagem_sem_destinatario_ativo_fica_pendente_ate_alguem_receber(d):
    orch = reg(d, "orchestrator")
    out = delegate(d, orch)  # nenhum developer registrado
    assert out["delivered_to_session"] is None
    late = reg(d, "developer")  # registra depois: encontra a pendência
    assert d.register_session("demo", "developer", "claude", "demo-developer")["pending_messages"] == 1
    assert d.receive(late)["message"]["task_id"] == "T-1"
    assert d.status_data("demo")["tasks"][0]["session_id"] == late


def test_escolhe_o_worker_menos_carregado(d):
    orch = reg(d, "orchestrator")
    a, b = reg(d, "developer", "dev-a"), reg(d, "developer", "dev-b")
    first = delegate(d, orch, "A")["delivered_to_session"]
    second = delegate(d, orch, "B")["delivered_to_session"]
    assert {first, second} == {a, b}


def test_nao_escolhe_sessao_sem_sinal_de_vida(d, clock):
    orch = reg(d, "orchestrator")
    dev = reg(d, "developer")
    clock.advance(600)  # o developer sumiu
    reg(d, "orchestrator")  # a sessão do orquestrador continua viva
    assert delegate(d, orch)["delivered_to_session"] is None
    d.heartbeat(dev)
    assert delegate(d, orch, "T-2")["delivered_to_session"] == dev


# ---------------------------------------------------------- reportar e perguntar


def test_report_so_da_propria_tarefa_e_em_execucao(d):
    orch, dev, qa = reg(d, "orchestrator"), reg(d, "developer"), reg(d, "qa")
    delegate(d, orch)
    with pytest.raises(DelegatorError, match="not running"):
        report(d, dev)  # ainda não chamou receive
    d.receive(dev)
    with pytest.raises(DelegatorError, match="only works on your own task"):
        report(d, qa)
    with pytest.raises(DelegatorError, match="unknown task"):
        report(d, dev, task="X")
    with pytest.raises(DelegatorError, match="status must be"):
        report(d, dev, status="OK")
    with pytest.raises(DelegatorError, match="summary and evidence"):
        d.report(dev, "T-1", "DONE", "", "")
    report(d, dev, status="NEEDS_REVIEW")
    with pytest.raises(DelegatorError, match="not running"):
        report(d, dev)  # já reportou


@pytest.mark.parametrize("status", ["DONE", "PARTIAL", "BLOCKED", "FAILED", "NEEDS_REVIEW"])
def test_report_mapeia_o_estado_da_tarefa(d, status):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.receive(dev)
    assert report(d, dev, status=status.lower())["task_state"] == status.lower()


def test_perguntar_e_responder(d, waker):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.receive(dev)
    asked = d.ask(dev, "T-1", "azul ou verde?", ["azul", "verde"])
    assert asked["task_state"] == "asked" and waker.calls[-1][0] == "orchestrator"
    with pytest.raises(DelegatorError, match="unanswered question"):
        report(d, dev)
    q = d.receive(orch)["message"]
    assert q["type"] == "question" and "OPTIONS: azul | verde" in q["body"]
    assert f"QUESTION_ID: {asked['question_id']}" in q["body"]
    d.answer(orch, asked["question_id"], "verde")
    assert d.status_data("demo")["tasks"][0]["state"] == "running"
    ans = d.receive(dev)["message"]  # a resposta vai ao mesmo worker
    assert ans["type"] == "answer" and "ANSWER: verde" in ans["body"]
    with pytest.raises(DelegatorError, match="already answered"):
        d.answer(orch, asked["question_id"], "de novo")
    assert report(d, dev)["accepted"]


def test_answer_de_pergunta_inexistente(d):
    orch = reg(d, "orchestrator")
    with pytest.raises(DelegatorError, match="unknown question"):
        d.answer(orch, 99, "x")


def test_so_pergunta_com_a_tarefa_em_execucao(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    with pytest.raises(DelegatorError, match="only ask while it is running"):
        d.ask(dev, "T-1", "q?")


# ------------------------------------------------------------------- cancelar


def test_cancelar_tarefa_pendente_descarta_a_mensagem(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.cancel_task(orch, "T-1", "mudou")
    assert d.receive(dev)["message"] is None
    assert d.status_data("demo")["tasks"][0]["state"] == "cancelled"


def test_cancelar_tarefa_em_execucao_avisa_o_worker(d, waker):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.receive(dev)
    d.cancel_task(orch, "T-1", "mudou")
    note = d.receive(dev)["message"]
    assert note["type"] == "cancel" and "CANCELLED: mudou" in note["body"]
    with pytest.raises(DelegatorError, match="was cancelled"):
        report(d, dev)
    with pytest.raises(DelegatorError, match="only delegated, running or asked"):
        d.cancel_task(orch, "T-1")  # já cancelada


def test_pessoa_cancela_e_o_orquestrador_e_avisado(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.receive(dev)
    d.cancel_task_as_person("demo", "T-1", "decisão da pessoa")
    assert d.receive(orch)["message"]["body"].startswith("TASK: T-1\nCANCELLED by the person")
    assert d.receive(dev)["message"]["type"] == "cancel"
    with pytest.raises(DelegatorError, match="never delegated"):
        d.cancel_task_as_person("demo", "ZZ")


# ------------------------------------------------------------------ redelegar


def test_redelegar_depois_de_resultado_parcial_incrementa_a_tentativa(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.receive(dev)
    report(d, dev, status="PARTIAL")
    again = delegate(d, orch, instruction="complete o que faltou")
    assert again["attempt"] == 2
    msg = d.receive(dev)["message"]
    assert "ATTEMPT: 2" in msg["body"] and msg["attempt"] == 2


def test_nao_delega_tarefa_em_andamento_nem_concluida(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    with pytest.raises(DelegatorError, match="already delegated"):
        delegate(d, orch)
    d.receive(dev)
    report(d, dev)
    with pytest.raises(DelegatorError, match="already done"):
        delegate(d, orch)


def test_resultado_vai_ao_orquestrador_que_delegou_e_ao_novo_se_foi_substituido(d):
    old, dev = reg(d, "orchestrator", ref="o1"), reg(d, "developer")
    delegate(d, old)
    d.receive(dev)
    new = reg(d, "orchestrator", ref="o2")  # reiniciou o orquestrador no meio da tarefa
    report(d, dev)
    assert d.receive(new)["message"]["type"] == "result"  # chega por papel ao orquestrador vigente


# --------------------------------------------------------------- limites/status


def test_corpo_muito_grande_e_recusado(d):
    orch = reg(d, "orchestrator")
    with pytest.raises(DelegatorError, match="too long"):
        delegate(d, orch, instruction="x" * (MAX_BODY + 1))


def test_status_do_orquestrador_e_do_worker(d):
    orch, dev, qa = reg(d, "orchestrator"), reg(d, "developer"), reg(d, "qa")
    delegate(d, orch, "A")
    delegate(d, orch, "B", to="qa")
    d.receive(dev)
    full = d.project_status(orch)
    assert "A: running" in full and "B: delegated" in full and "3 pending" not in full
    own = d.project_status(dev)
    assert "A: running" in own and "B:" not in own  # o worker só vê a própria tarefa
    assert "A:" not in d.project_status(qa)


def test_pending_for_agent_nao_entrega_conteudo_e_marca_vida(d, clock):
    orch = reg(d, "orchestrator")
    reg(d, "developer")
    delegate(d, orch)
    info = d.pending_for_agent("demo", "demo-developer")
    assert info["count"] == 1 and info["registered"] and "body" not in info
    clock.advance(500)  # sem sinal: stale
    assert not {s["role"]: s["alive"] for s in d.status_data("demo")["sessions"]}["developer"]
    d.pending_for_agent("demo", "demo-developer")  # o listener consulta: volta a ficar viva
    live = {s["role"]: s["alive"] for s in d.status_data("demo")["sessions"]}
    assert live["developer"] is True
    assert d.pending_for_agent("demo", "ninguem")["count"] == 0
    # agente ainda não registrado: só vê mensagens do papel
    assert d.pending_for_agent("demo", "demo-qa") == {
        "count": 0,
        "ids": [],
        "types": [],
        "registered": False,
        "role": "qa",
    }


def test_push_profile_so_para_sessoes_vivas(d, waker, clock):
    reg(d, "orchestrator")
    dev = reg(d, "developer")
    assert d.push_profile("demo", "developer")["sessions"] == [dev]
    assert d.receive(dev)["message"]["type"] == "profile"
    clock.advance(600)
    assert d.push_profile("demo", "developer")["sessions"] == []


def test_eventos_ficam_registrados(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    d.receive(dev)
    kinds = [e["kind"] for e in d.events("demo")]
    assert "delegate" in kinds and "receive" in kinds and "register" in kinds


def test_wake_com_falha_nao_derruba_a_delegacao(tmp_path, clock):
    class Broken:
        def notify(self, *a):
            raise RuntimeError("boom")

    dd = Delegator(Store(tmp_path / "x.db"), Registry(tmp_path), Broken(), clock=clock)
    orch = reg(dd, "orchestrator")
    reg(dd, "developer")
    assert delegate(dd, orch)["wake"].startswith("wake failed")


def test_delegate_avisa_quando_nenhum_worker_esta_registrado(d):
    orch = reg(d, "orchestrator")
    out = delegate(d, orch)
    assert out["delivered_to_session"] is None and "no live 'developer' session" in out["warning"]
    reg(d, "developer")
    assert "warning" not in delegate(d, orch, "T-2")


# ------------------------------------------------------------------ monitor


def test_timeline_traz_de_para_tarefa_estado_e_latencia(d, clock):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    out = delegate(d, orch, instruction="refatorar   o index\ncom calma")
    clock.advance(3)
    d.receive(dev)
    report(d, dev)
    events = {e["kind"]: e for e in d.timeline("demo")}  # do mais novo ao mais antigo
    delegate_ev = events["delegate"]
    assert (delegate_ev["role_from"], delegate_ev["role_to"], delegate_ev["task_id"]) == (
        "orchestrator",
        "developer",
        "T-1",
    )
    assert (
        delegate_ev["state"] == "delegated"
        and delegate_ev["attempt"] == 1
        and delegate_ev["message_id"] == out["message_id"]
    )
    assert "haiku / low" in delegate_ev["text"] and "refatorar o index com calma" in delegate_ev["text"]
    receive_ev = events["receive"]
    assert (
        receive_ev["latency_ms"] == 3000 and receive_ev["state"] == "running" and receive_ev["role_to"] == "developer"
    )
    report_ev = events["report"]
    assert report_ev["state"] == "done" and report_ev["text"].startswith("DONE · feito")


def test_timeline_incremental_e_filtrada_por_projeto(d):
    orch = reg(d, "orchestrator")
    first = d.timeline("demo")[0]["id"]
    reg(d, "developer")
    assert [e["kind"] for e in d.timeline("demo", after_id=first)] == ["register"]
    assert d.timeline("other") == []
    assert len(d.timeline(None)) >= 2 and orch


def test_recusas_viram_evento_com_quem_tentou(d):
    dev = reg(d, "developer")
    with pytest.raises(DelegatorError):
        delegate(d, dev)
    with pytest.raises(DelegatorError):
        d.register_session("demo", "developer", "codex", "x")  # cli errada
    refused = [e for e in d.timeline("demo") if e["kind"] == "refused"]
    assert len(refused) == 2
    assert refused[1]["role_from"] == "developer" and "delegate negado" in refused[1]["text"]
    assert "configured for cli" in refused[0]["text"]
    with pytest.raises(DelegatorError):
        d.receive("s-nada")  # sessão desconhecida: não há como atribuir a um projeto
    assert len([e for e in d.timeline("demo") if e["kind"] == "refused"]) == 2


def test_mensagem_completa_para_o_monitor(d):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    out = delegate(d, orch)
    d.receive(dev)
    asked = d.ask(dev, "T-1", "azul ou verde?", ["azul", "verde"])
    msg = d.message("demo", asked["message_id"] if "message_id" in asked else out["message_id"] + 1)
    assert msg["type"] == "question" and "azul ou verde?" in msg["body"] and msg["question"]["state"] == "open"
    first = d.message("demo", out["message_id"])
    assert first["type"] == "delegate" and first["state"] == "claimed"
    with pytest.raises(DelegatorError, match="unknown message"):
        d.message("demo", 999)
    assert [h["kind"] for h in d.task_history("demo", "T-1")] == ["delegate", "receive", "ask"]


def test_estatisticas_do_monitor(d, clock):
    orch, dev = reg(d, "orchestrator"), reg(d, "developer")
    delegate(d, orch)
    clock.advance(2)
    d.receive(dev)
    d.ask(dev, "T-1", "q?")
    with pytest.raises(DelegatorError):
        delegate(d, dev)
    stats = d.stats("demo")
    assert stats["sessions"] == 2 and stats["alive"] == 2
    assert stats["open_questions"] == 1 and stats["pending"] == 1  # a pergunta ainda não foi recebida
    assert stats["refusals_today"] == 1 and stats["avg_latency_ms"] == 2000 and stats["events_today"] >= 5
    assert d.stats()["sessions"] == 2 and d.stats("other")["sessions"] == 0


def test_banco_antigo_ganha_as_colunas_novas(tmp_path):
    import sqlite3

    path = tmp_path / "velho.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, project TEXT, kind TEXT, text TEXT)"
    )
    conn.execute("INSERT INTO events (ts, project, kind, text) VALUES ('2026-01-01', 'demo', 'register', 'velho')")
    conn.commit()
    conn.close()
    store = Store(path)
    cols = {r["name"] for r in store.query("PRAGMA table_info(events)")}
    assert {"role_from", "task_id", "latency_ms", "message_id"} <= cols
    assert store.one("SELECT text FROM events")["text"] == "velho"  # o histórico antigo continua


def test_especialista_com_nome_composto_completa_o_fluxo(tmp_path, clock, waker):
    """Papel próprio do projeto (ex.: bot-results-analyst): registra, recebe a delegação, pergunta e reporta."""
    roles = {r: Role(cli="claude") for r in ("orchestrator", "developer", "bot-results-analyst")}
    registry = Registry(tmp_path)
    registry.projects["demo"] = Project(id="demo", name="Demo", path=tmp_path, roles=roles)
    install = tmp_path / "instalacao"
    (install / "perfis" / "demo").mkdir(parents=True)
    (install / "perfis" / "demo" / "bot-results-analyst.md").write_text("# Papel: Analista\nSomente leitura.\n")
    d = Delegator(Store(tmp_path / "estado.db"), registry, waker, clock=clock, install=install)
    orch = reg(d, "orchestrator")
    out = d.register_session("demo", "bot-results-analyst", "claude", "demo-bot-results-analyst")
    assert out["role_kind"] == "worker" and "Somente leitura" in out["profile"]
    analyst = out["session_id"]
    delegate(d, orch, to="bot-results-analyst", instruction="resuma as carteiras")
    got = d.receive(analyst)["message"]
    assert got["task_id"] == "T-1" and "resuma as carteiras" in got["body"]
    assert report(d, analyst)["task_state"] == "done"
    with pytest.raises(DelegatorError, match="forbidden|not allowed"):
        d.delegate(analyst, "T-2", "developer", "x", "haiku", "low", "m", True)  # worker não delega
