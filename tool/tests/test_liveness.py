from __future__ import annotations

import json
import threading

import pytest
from websockets.sync.server import serve

from dev_orchestra.core import liveness
from dev_orchestra.core.liveness import (
    ClaudeSession,
    CodexThread,
    Services,
    Workstream,
    collect_status,
)
from dev_orchestra.core.models import GlobalConfig, Project, Role

AGENTS_JSON = json.dumps(
    [
        {
            "pid": 10,
            "cwd": "/p/demo",
            "kind": "interactive",
            "startedAt": 1,
            "sessionId": "s1",
            "name": "demo-orchestrator",
            "status": "busy",
        },
        {
            "pid": 11,
            "cwd": "/outro",
            "kind": "interactive",
            "sessionId": "s2",
            "name": "demo-developer",
            "status": "idle",
        },
        {"cwd": "/p/demo"},
    ]
)

WS_TEXT = """ai-memory: scope default/demo (workspace from /p/demo/.ai-memory.toml)
Managed workstreams for default/demo:
* orchestrator1  just now  [claude-code]
    id: 01a1d737-db48-7660-a51d-21dd8e3381a2
  orchestrator  2 minutes ago  [claude-code]
    id: 01a1d731-0bcc-7420-9be5-0a252301b8df
  developer  7 minutes ago  [no linked harnesses]
    id: 01a1d731-6abb-79f1-8e2f-7e3670a3f0ae
"""


def test_parse_claude_agents_tolera_campos_ausentes():
    sessions = liveness.parse_claude_agents(AGENTS_JSON)
    assert len(sessions) == 3
    assert sessions[0] == ClaudeSession(10, "/p/demo", "demo-orchestrator", "s1", "busy", "interactive")
    assert sessions[2].name is None and sessions[2].status is None


def test_parse_claude_agents_rejeita_formato_inesperado():
    with pytest.raises(ValueError):
        liveness.parse_claude_agents('{"a": 1}')


def test_claude_sessions_falha_do_comando_ou_json_ruim():
    assert liveness.claude_sessions(lambda a: (127, "")) is None
    assert liveness.claude_sessions(lambda a: (0, "não é json")) is None
    assert len(liveness.claude_sessions(lambda a: (0, AGENTS_JSON))) == 3


def test_parse_workstreams_formato_real_do_ai_memory():
    ws = liveness.parse_workstreams(WS_TEXT)
    assert [w.name for w in ws] == ["orchestrator1", "orchestrator", "developer"]
    assert ws[0] == Workstream("orchestrator1", "just now", "claude-code")
    assert ws[2].harnesses == "no linked harnesses"


def test_ai_memory_workstreams_roda_na_pasta_do_projeto():
    """O AI Memory lista os workstreams do checkout atual; sem o cwd correto a lista vem vazia."""
    chamadas = []

    def run(args, cwd):
        chamadas.append((args, cwd))
        return 0, WS_TEXT

    assert len(liveness.ai_memory_workstreams("demo", "/p/demo", run)) == 3
    assert chamadas == [(["ai-memory", "workstreams", "--project", "demo"], "/p/demo")]
    assert liveness.ai_memory_workstreams("demo", "/p/demo", lambda a, c: (1, "")) is None


def test_run_command_usa_cwd(tmp_path):
    code, out = liveness.run_command(["pwd"], cwd=str(tmp_path))
    assert code == 0 and out.strip() == str(tmp_path.resolve())


def test_run_command_comando_inexistente():
    assert liveness.run_command(["comando-que-nao-existe-xyz"]) == (127, "")


def test_saida_sem_workstreams_do_ai_memory():
    texto = "Managed workstreams for default/demo:\nNo managed workstreams for this checkout.\n"
    assert liveness.parse_workstreams(texto) == []


def projeto(tmp_path_factory=None, path="/p/demo", **roles) -> Project:
    return Project(id="demo", name="Demo", path=path, roles={k: Role(cli=v) for k, v in roles.items()})


def fake_services(claude=None, codex=None, ws=None, projetos=None) -> Services:
    return Services(
        claude_sessions=lambda: claude,
        codex_threads=lambda url, cwd: codex,
        workstreams=lambda name, cwd: ws,
        ai_memory_projects=lambda: projetos if projetos is not None else [("default", "demo")],
    )


def test_status_claude_aberta_ocupada_e_fechada():
    p = projeto(orchestrator="claude", developer="claude")
    sessions = liveness.parse_claude_agents(AGENTS_JSON)
    report = collect_status(p, GlobalConfig(), fake_services(claude=sessions, ws=[]))
    by = {r.role: r for r in report.roles}
    assert by["orchestrator"].state == "ocupada" and "pid 10" in by["orchestrator"].detail
    # a sessão developer existe, mas em outra pasta
    assert by["developer"].state == "fechada"
    assert report.app_server_up is None


def test_status_claude_ociosa():
    p = projeto(orchestrator="claude")
    sessions = [ClaudeSession(1, "/p/demo", "demo-orchestrator", "s", "idle", "interactive")]
    report = collect_status(p, GlobalConfig(), fake_services(claude=sessions, ws=[]))
    assert report.roles[0].state == "ociosa"


def test_status_claude_desconhecida_quando_o_comando_falha():
    p = projeto(orchestrator="claude")
    report = collect_status(p, GlobalConfig(), fake_services(claude=None, ws=[]))
    assert report.roles[0].state == "desconhecida"


def test_status_codex_estados():
    p = projeto(orchestrator="codex", developer="codex", qa="codex", planner="codex")
    threads = [
        CodexThread("aaaaaaaa-1", "demo-orchestrator", "/p/demo", "idle"),
        CodexThread("bbbbbbbb-2", "demo-developer", "/p/demo", "notLoaded"),
        CodexThread("cccccccc-3", "demo-qa", "/p/demo", "active"),
        CodexThread("dddddddd-4", "demo-planner", "/p/demo", "systemError"),
    ]
    report = collect_status(p, GlobalConfig(), fake_services(codex=threads, ws=[]))
    estados = {r.role: r.state for r in report.roles}
    assert estados == {"orchestrator": "ociosa", "developer": "fechada", "qa": "ocupada", "planner": "erro"}
    assert report.app_server_up is True


def test_status_codex_escolhe_a_thread_mais_viva():
    p = projeto(developer="codex")
    threads = [
        CodexThread("a1", "demo-developer", "/p/demo", "notLoaded"),
        CodexThread("b2", "demo-developer", "/p/demo", "idle"),
    ]
    report = collect_status(p, GlobalConfig(), fake_services(codex=threads, ws=[]))
    assert report.roles[0].state == "ociosa"


def test_status_codex_app_server_fora_do_ar():
    p = projeto(developer="codex")
    report = collect_status(p, GlobalConfig(), fake_services(codex=None, ws=[]))
    assert report.roles[0].state == "desconhecida" and "ws://127.0.0.1:4500" in report.roles[0].detail
    assert report.app_server_up is False


def test_workstreams_aparecem_no_detalhe_e_no_conjunto():
    p = projeto(orchestrator="claude", developer="claude")
    ws = [
        Workstream("demo-orchestrator", "2 minutes ago", "claude-code"),
        Workstream("developer", "7 minutes ago", "codex"),  # nome antigo, sem prefixo
    ]
    report = collect_status(p, GlobalConfig(), fake_services(claude=[], ws=ws))
    by = {r.role: r for r in report.roles}
    assert "workstream usado 2 minutes ago" in by["orchestrator"].detail
    assert by["orchestrator"].name == "demo-orchestrator"
    assert "sem workstream" in by["developer"].detail and "antigo chamado 'developer'" in by["developer"].detail
    assert report.workstream_names == {"demo-orchestrator", "developer"}


def test_ai_memory_indisponivel_vira_nota():
    p = projeto(orchestrator="claude")
    report = collect_status(p, GlobalConfig(), fake_services(claude=[], ws=None))
    assert report.workstream_names is None and report.notes


def test_nao_consulta_fontes_desnecessarias():
    chamadas = []
    services = Services(
        claude_sessions=lambda: chamadas.append("claude") or [],
        codex_threads=lambda u, c: chamadas.append("codex") or [],
        workstreams=lambda n, c: [],
    )
    collect_status(projeto(orchestrator="claude"), GlobalConfig(), services)
    assert chamadas == ["claude"]


# ------------------------------------------------- cliente real do App Server (servidor falso)


def _fake_app_server(loaded, threads):
    def handler(ws):
        for raw in ws:
            msg = json.loads(raw)
            if "id" not in msg:
                continue
            method = msg["method"]
            if method == "initialize":
                result = {"codexHome": "/x", "platformFamily": "unix", "platformOs": "linux", "userAgent": "fake"}
            elif method == "thread/loaded/list":
                result = {"data": loaded, "nextCursor": None}
            elif method == "thread/list":
                result = {"data": threads, "nextCursor": None, "echo_cwd": msg["params"].get("cwd")}
            else:
                ws.send(json.dumps({"id": msg["id"], "error": {"message": "desconhecido"}}))
                continue
            ws.send(json.dumps({"id": msg["id"], "result": result}))

    server = serve(handler, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"ws://127.0.0.1:{server.socket.getsockname()[1]}"


def test_codex_threads_contra_servidor_falso():
    server, url = _fake_app_server(
        loaded=["t1"],
        threads=[
            {"id": "t1", "name": "orchestrator", "cwd": "/p/demo", "status": {"type": "idle"}},
            {"id": "t2", "name": "developer", "cwd": "/p/demo", "status": {"type": "notLoaded"}},
            {"id": "t3", "name": "qa", "cwd": "/p/demo"},  # sem status: deduzido por 'loaded'
        ],
    )
    try:
        threads = liveness.codex_threads(url, "/p/demo", timeout=3)
    finally:
        server.shutdown()
    assert [(t.name, t.status) for t in threads] == [
        ("orchestrator", "idle"),
        ("developer", "notLoaded"),
        ("qa", "notLoaded"),
    ]


def test_codex_threads_servidor_inacessivel():
    assert liveness.codex_threads("ws://127.0.0.1:1", "/p/demo", timeout=0.5) is None
