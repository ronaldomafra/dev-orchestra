"""Listener do hook, instalador do Claude, container (comandos), CLI e cadastro do modo delegador."""

from __future__ import annotations

import io
import json

from dev_orchestra import cli
from dev_orchestra.core import config
from dev_orchestra.core.models import GlobalConfig, Project, Role
from dev_orchestra.delegator import docker
from dev_orchestra.delegator.client import DelegatorUnavailable
from dev_orchestra.delegator.install import (
    LISTENER_MARK,
    PERMISSION,
    install_claude,
    listener_command,
    merge_settings,
)
from dev_orchestra.delegator.listener import RENOTIFY_SECONDS, agent_from_env, listen, notice
from dev_orchestra.delegator.wake import CodexWaker, RoutingWaker


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def pending(self, project, agent):
        self.calls += 1
        item = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(item, Exception):
            raise item
        return item


def idle():
    return {"count": 0, "ids": [], "types": []}


def pend(*ids, kind="delegate"):
    return {"count": len(ids), "ids": list(ids), "types": [kind] * len(ids)}


class Time:
    def __init__(self):
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run_listen(tmp_path, client, **kw):
    t = Time()
    out = io.StringIO()
    code = listen(client, "demo", "demo-developer", state_dir=tmp_path, out=out, sleep=t.sleep, clock=t.clock,
                  wall=lambda: 1000 + t.t, **kw)  # fmt: skip
    return code, out.getvalue(), t


# ------------------------------------------------------------------ listener


def test_listener_acorda_quando_ha_pendencia(tmp_path):
    code, text, t = run_listen(tmp_path, FakeClient([idle(), idle(), pend(7)]), interval=2, max_seconds=100)
    assert code == 2 and "ids: 7" in text and "delegate" in text and "receive" in text and t.t == 4


def test_listener_nao_leva_conteudo_so_aviso():
    text = notice({"count": 1, "ids": [3], "types": ["delegate"]})
    assert "não traz conteúdo" in text and "receive" in text


def test_listener_expira_sem_pendencia(tmp_path):
    code, text, t = run_listen(tmp_path, FakeClient([idle()]), interval=2, max_seconds=10)
    assert code == 0 and text == "" and t.t >= 10


def test_listener_sobrevive_a_servidor_fora_do_ar(tmp_path):
    code, _, _ = run_listen(
        tmp_path,
        FakeClient([DelegatorUnavailable("x"), DelegatorUnavailable("x"), pend(1)]),
        interval=1,
        max_seconds=50,
    )
    assert code == 2


def test_listener_desiste_se_o_projeto_e_desconhecido(tmp_path):
    code, _, _ = run_listen(tmp_path, FakeClient([RuntimeError("unknown project")]), interval=1, max_seconds=50)
    assert code == 0


def test_so_um_listener_por_agente(tmp_path):
    import fcntl

    lock = (tmp_path / "demo__demo-developer.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # já existe um listener
    code, text, t = run_listen(tmp_path, FakeClient([pend(1)]), interval=1, max_seconds=50)
    assert (code, text, t.t) == (0, "", 0)
    lock.close()


def test_mesmo_aviso_nao_se_repete_antes_do_prazo(tmp_path):
    first, _, _ = run_listen(tmp_path, FakeClient([pend(5)]), interval=60, max_seconds=60)
    assert first == 2
    # o agente ignorou a mensagem: um novo listener com as mesmas ids não acorda de novo logo em seguida
    second, _, _ = run_listen(tmp_path, FakeClient([pend(5)]), interval=60, max_seconds=60)
    assert second == 0
    third, _, _ = run_listen(
        tmp_path, FakeClient([pend(5)]), interval=RENOTIFY_SECONDS + 1, max_seconds=RENOTIFY_SECONDS + 5
    )
    assert third == 2  # passou o prazo
    other, _, _ = run_listen(tmp_path, FakeClient([pend(5, 6)]), interval=1, max_seconds=5)
    assert other == 2  # mensagem nova acorda na hora


def test_agente_vem_do_ambiente():
    assert agent_from_env({"DEV_ORCHESTRA_AGENT": "a", "AI_MEMORY_WORKSTREAM": "b"}, from_process=False) == "a"
    assert agent_from_env({"AI_MEMORY_WORKSTREAM": "b"}, from_process=False) == "b"
    assert agent_from_env({}, from_process=False) is None


# ------------------------------------------------------------------ instalador


def test_merge_settings_cria_permissao_e_hooks():
    data, changes = merge_settings({}, "dev-orchestra delegator listen --project demo")
    assert PERMISSION in data["permissions"]["allow"]
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "Notification"):
        hook = data["hooks"][event][0]["hooks"][0]
        assert hook["async"] is True and hook["asyncRewake"] is True and hook["timeout"] == 3600
        assert LISTENER_MARK in hook["command"]
    assert len(changes) == 6  # a permissão e um hook por evento


def test_merge_settings_e_idempotente_e_preserva_o_resto():
    existing = {
        "model": "opus",
        "permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm *)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "meu-hook.sh"}]}]},
    }
    first, _ = merge_settings(existing, "x delegator listen --project demo")
    assert first["model"] == "opus" and first["permissions"]["deny"] == ["Bash(rm *)"]
    assert first["permissions"]["allow"] == ["Bash(ls)", PERMISSION]
    stops = [h["command"] for g in first["hooks"]["Stop"] for h in g["hooks"]]
    assert stops == ["meu-hook.sh", "x delegator listen --project demo"]  # o hook do usuário fica
    again, changes = merge_settings(first, "x delegator listen --project demo")
    assert again == first and changes == []
    updated, changes = merge_settings(first, "y delegator listen --project demo")
    assert changes == [
        f"hook {e} atualizado" for e in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "Notification")
    ]
    assert [h["command"] for g in updated["hooks"]["Stop"] for h in g["hooks"]][-1].startswith("y ")


def test_install_claude_grava_e_registra_o_mcp(tmp_path):
    project = Project(id="demo", name="D", path=tmp_path, roles={"orchestrator": Role(cli="claude")})
    calls = []

    def runner(args, cwd=None, timeout=15.0, env=None):
        calls.append(args)
        return (1, "") if args[:3] == ["claude", "mcp", "get"] else (0, "")

    report = install_claude(project, exe="/x/dev-orchestra", run=runner)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "/x/dev-orchestra delegator listen --project demo" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert ["claude", "mcp", "add", "--transport", "http", "--scope", "local", "dev-orchestra",
            "http://127.0.0.1:8787/mcp"] in calls  # fmt: skip
    assert report.mcp == "registrado (local)" and not report.warnings
    calls.clear()
    again = install_claude(project, exe="/x/dev-orchestra", run=lambda a, cwd=None, timeout=15.0, env=None: (0, ""))
    assert again.changes == [] and again.mcp == "já registrado"


def test_install_nao_sobrescreve_settings_ilegivel(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{ruim", encoding="utf-8")
    project = Project(id="demo", name="D", path=tmp_path, roles={"orchestrator": Role(cli="claude")})
    report = install_claude(project, run=lambda *a, **k: (0, ""))
    assert report.warnings and (tmp_path / ".claude" / "settings.json").read_text() == "{ruim"


def test_install_dry_run_nao_grava(tmp_path):
    project = Project(id="demo", name="D", path=tmp_path, roles={"orchestrator": Role(cli="claude")})
    report = install_claude(project, write=False, run=lambda *a, **k: (1, ""))
    assert not (tmp_path / ".claude").exists() and report.mcp == "seria registrado" and report.changes


def test_listener_command_com_url_diferente():
    assert (
        listener_command("demo", "x", "http://127.0.0.1:9999")
        == "x delegator listen --project demo --url http://127.0.0.1:9999"
    )


# ---------------------------------------------------------------------- docker


def test_comando_de_execucao_do_container(tmp_path):
    args = docker.run_command_args(8787, tmp_path)
    assert args[:3] == ["docker", "run", "-d"] and "--network" in args and args[args.index("--network") + 1] == "host"
    assert f"{tmp_path}:/data:ro" in args and f"{docker.VOLUME}:/state" in args
    assert args[-4:] == ["--host", "127.0.0.1", "--port", "8787"]  # só no loopback
    assert "--restart" in args and args[args.index("--restart") + 1] == "unless-stopped"


def test_contexto_de_build_leva_o_pacote_sem_a_tui():
    ctx = docker.prepare_context()
    try:
        assert (ctx / "Dockerfile").is_file() and (ctx / "dev_orchestra" / "delegator" / "server.py").is_file()
        assert not (ctx / "dev_orchestra" / "tui").exists()
        text = (ctx / "Dockerfile").read_text()
        assert "mcp>=1.12" in text and "dev_orchestra.delegator" in text and "HEALTHCHECK" in text
    finally:
        import shutil

        shutil.rmtree(ctx, ignore_errors=True)


def test_up_constroi_e_sobe_e_e_idempotente():
    calls = []

    def runner(args, cwd=None, timeout=15.0, env=None):
        calls.append(args[:3])
        if args[:2] == ["docker", "inspect"]:
            return 1, ""  # não existe
        if args[:3] == ["docker", "image", "inspect"]:
            return 1, ""  # sem imagem
        return 0, ""

    lines = docker.up(8787, runner)
    assert any("construída" in x for x in lines) and any("iniciado" in x for x in lines)
    assert ["docker", "build", "--label"] in calls and ["docker", "run", "-d"] in calls
    current = docker.code_hash()

    def running(args, cwd=None, timeout=15.0, env=None):
        return (0, current + "\n") if args[:3] == ["docker", "image", "inspect"] else (0, "running\n")

    assert docker.up(8787, running) == ["O delegador já está rodando."]


def test_up_detecta_imagem_desatualizada_e_reconstroi_com_rebuild():
    calls = []

    def runner(args, cwd=None, timeout=15.0, env=None):
        calls.append(args[:3])
        if args[:3] == ["docker", "image", "inspect"]:
            return 0, "codigo-antigo\n"
        if args[:2] == ["docker", "inspect"]:
            return 0, "running\n"
        return 0, ""

    lines = docker.up(8787, runner)
    assert "versão antiga" in lines[0] and ["docker", "build", "--label"] not in calls
    lines = docker.up(8787, runner, rebuild=True)
    assert ["docker", "build", "--label"] in calls and ["docker", "rm", "-f"] in calls
    assert any("iniciado" in x for x in lines)


def test_down():
    assert docker.down(lambda *a, **k: (1, "")) == ["O delegador não está instalado."]


# --------------------------------------------------------------------- CLI/cadastro


def test_cadastro_guarda_o_modo_delegador(tmp_path):
    p = Project(id="demo", name="D", path=tmp_path, roles={"orchestrator": Role(cli="claude")}, delegator=True)
    config.save_project(p)
    assert config.load_project("demo").delegator is True
    assert "delegador = true" in config.paths.project_file("demo").read_text()
    config.save_project(p.model_copy(update={"delegator": False}))
    assert config.load_project("demo").delegator is False
    assert "delegador" not in config.paths.project_file("demo").read_text()


def test_config_global_guarda_a_url_do_delegador():
    config.save_global(GlobalConfig(delegator_url="http://127.0.0.1:9000"))
    assert config.load_global().delegator_url == "http://127.0.0.1:9000"
    assert GlobalConfig().delegator_url == "http://127.0.0.1:8787"


def test_cli_status_sem_servico_explica(capsys):
    config.save_project(
        Project(id="demo", name="D", path=config.paths.install_dir(), roles={"orchestrator": Role(cli="claude")})
    )
    code = cli.main(["delegator", "--url", "http://127.0.0.1:9", "status", "demo"], out=io.StringIO())
    assert code == 2 and "docker up" in capsys.readouterr().err


def test_cli_listen_sem_identidade_nao_atrapalha(monkeypatch, capsys):
    monkeypatch.setattr("dev_orchestra.delegator.listener.agent_from_process", lambda *a, **k: None)
    for var in ("DEV_ORCHESTRA_AGENT", "AI_MEMORY_WORKSTREAM", "AI_MEMORY_WORKSTREAM_ID"):
        monkeypatch.delenv(var, raising=False)
    assert cli.main(["delegator", "listen", "--project", "demo"], out=io.StringIO()) == 0
    assert "DEV_ORCHESTRA_AGENT" in capsys.readouterr().err


# ------------------------------------------------------------------------ Codex


def test_codex_sem_sessao_registrada_mantem_pendente(tmp_path):
    p = Project(id="demo", name="D", path=tmp_path, roles={"developer": Role(cli="codex")})
    assert "no codex session" in CodexWaker(background=False).notify(p, "developer", None, 1)


def test_roteamento_pela_cli_do_destino(tmp_path):
    p = Project(
        id="demo", name="D", path=tmp_path, roles={"orchestrator": Role(cli="claude"), "developer": Role(cli="codex")}
    )
    calls = []

    class Fake(CodexWaker):
        def _send(self, url, thread_id):
            calls.append((url, thread_id))
            return "sent"

    router = RoutingWaker(Fake(background=False))
    assert router.notify(p, "orchestrator", {"ref": "x"}, 1) == "listener"  # Claude: o listener acorda
    assert router.notify(p, "developer", {"ref": "thread-uuid"}, 2) == "sent"
    assert calls == [("ws://127.0.0.1:4500", "thread-uuid")]


def _fake_app_server(responses):
    """App Server de mentira: `responses` mapeia método -> 'ok' ou 'error'. Devolve (url, chamadas, parar)."""
    import threading

    from websockets.sync.server import serve

    calls: list[tuple[str, dict]] = []

    def handler(ws):
        for raw in ws:
            msg = json.loads(raw)
            if "id" not in msg:
                continue
            calls.append((msg["method"], msg.get("params", {})))
            if responses.get(msg["method"], "ok") == "error":
                ws.send(json.dumps({"id": msg["id"], "error": {"code": -1, "message": "thread not loaded"}}))
            else:
                ws.send(json.dumps({"id": msg["id"], "result": {}}))

    server = serve(handler, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"ws://127.0.0.1:{server.socket.getsockname()[1]}", calls, server.shutdown


def test_codex_waker_inicia_turno_curto_sem_conteudo(tmp_path):
    url, calls, stop = _fake_app_server({})
    try:
        p = Project(id="demo", name="D", path=tmp_path, roles={"developer": Role(cli="codex")}, app_server_url=url)
        out = CodexWaker(background=False).notify(p, "developer", {"ref": "uuid-1"}, 7)
    finally:
        stop()
    assert out == "turn/start sent"
    methods = [m for m, _ in calls]
    assert methods == ["initialize", "turn/start"]
    params = calls[1][1]
    assert params["threadId"] == "uuid-1"
    text = params["input"][0]["text"]
    assert params["input"][0]["type"] == "text" and "receive" in text and "7" not in text  # sem conteúdo nem id


def test_codex_waker_reabre_thread_descarregada(tmp_path):
    url, calls, stop = _fake_app_server({"turn/start": "error"})
    try:
        p = Project(id="demo", name="D", path=tmp_path, roles={"developer": Role(cli="codex")}, app_server_url=url)
        out = CodexWaker(background=False).notify(p, "developer", {"ref": "uuid-1"}, 7)
    finally:
        stop()
    assert out.startswith("codex wake failed")  # o servidor de mentira sempre recusa turn/start
    assert [m for m, _ in calls] == ["initialize", "turn/start", "thread/resume", "turn/start"]


def test_codex_waker_servidor_fora_do_ar_nao_levanta(tmp_path):
    p = Project(
        id="demo", name="D", path=tmp_path, roles={"developer": Role(cli="codex")}, app_server_url="ws://127.0.0.1:9"
    )
    assert (
        CodexWaker(background=False, timeout=0.5)
        .notify(p, "developer", {"ref": "u"}, 1)
        .startswith("codex wake failed")
    )


# ------------------------------------------------- contrato e definições de agente (modo delegador)


def test_patch_agent_tools_acrescenta_so_o_que_falta():
    from dev_orchestra.delegator.install import WORKER_TOOLS, patch_agent_tools

    text = "---\nname: developer\ntools: Read, Edit, SendMessage\n---\n\ncorpo com tools: no meio\n"
    new, changed = patch_agent_tools(text, WORKER_TOOLS)
    assert changed and "mcp__dev-orchestra__report" in new and new.endswith("corpo com tools: no meio\n")
    assert "delegate" not in new  # worker não ganha a ferramenta do orquestrador
    again, changed = patch_agent_tools(new, WORKER_TOOLS)
    assert not changed and again == new
    inherits = "---\nname: x\ndescription: sem lista\n---\n\ncorpo\n"
    assert patch_agent_tools(inherits, WORKER_TOOLS) == (inherits, False)
    assert patch_agent_tools("sem frontmatter", WORKER_TOOLS) == ("sem frontmatter", False)


def test_install_ajusta_as_definicoes_de_agente_existentes(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.md").write_text("---\nname: developer\ntools: Read, Bash\n---\ncorpo\n", encoding="utf-8")
    (agents / "meu-orquestrador.md").write_text("---\nname: o\ntools: Read\n---\ncorpo\n", encoding="utf-8")
    project = Project(id="demo", name="D", path=tmp_path, roles={"orchestrator": Role(cli="claude")})
    report = install_claude(project, run=lambda *a, **k: (0, ""))
    assert "agente developer: ferramentas do delegador" in report.changes
    assert "mcp__dev-orchestra__report" in (agents / "developer.md").read_text()
    orch = (agents / "meu-orquestrador.md").read_text()
    assert "mcp__dev-orchestra__delegate" in orch and "mcp__dev-orchestra__report" not in orch


def test_contrato_do_modo_delegador(tmp_path):
    from dev_orchestra.core import contract

    p = Project(id="demo", name="D", path=tmp_path, roles={"orchestrator": Role(cli="claude")}, delegator=True)
    plain = p.model_copy(update={"delegator": False})
    assert "docs/DELEGATOR.md" in [e.target for e in contract.applicable(p)]
    assert "docs/DELEGATOR.md" not in [e.target for e in contract.applicable(plain)]
    contract.scaffold(p)
    assert contract.agents_md_state(p) == "integrated"  # o AGENTS.md novo já cita o delegador
    (tmp_path / "AGENTS.md").write_text("roles/orchestrator.md antigo", encoding="utf-8")
    assert contract.agents_md_state(p) == "needs_merge"  # contrato anterior ao modo delegador
    assert contract.agents_md_state(plain) == "integrated"


def test_aviso_de_cancelamento_e_urgente():
    text = notice({"count": 1, "ids": [9], "types": ["cancel"]})
    assert "CANCELAMENTO" in text and "interrompa" in text and "não chame `report`" in text
    assert "CANCELAMENTO" not in notice({"count": 1, "ids": [9], "types": ["delegate"]})


def test_hooks_cobrem_o_turno_inteiro():
    from dev_orchestra.delegator.install import HOOK_EVENTS

    assert set(HOOK_EVENTS) == {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "Notification"}
    data, _ = merge_settings({}, "x delegator listen --project demo")
    assert set(data["hooks"]) == set(HOOK_EVENTS)


def test_comando_padrao_do_hook_usa_o_python_leve():
    import sys

    cmd = listener_command("demo")
    assert cmd == f"{sys.executable} -m dev_orchestra.delegator listen --project demo" and LISTENER_MARK in cmd


def test_listener_leve_pelo_modulo(monkeypatch, capsys):
    monkeypatch.setattr("dev_orchestra.delegator.listener.agent_from_process", lambda *a, **k: None)
    from dev_orchestra.delegator import __main__ as entry

    for var in ("DEV_ORCHESTRA_AGENT", "AI_MEMORY_WORKSTREAM", "AI_MEMORY_WORKSTREAM_ID"):
        monkeypatch.delenv(var, raising=False)
    assert entry.main(["listen", "--project", "demo"]) == 0  # sem identidade: não faz nada
    monkeypatch.setenv("DEV_ORCHESTRA_AGENT", "demo-developer")
    assert entry.main(["listen", "--project", "demo", "--url", "http://127.0.0.1:9", "--max-seconds", "0"]) == 0


# ------------------------------------- identidade do listener (sessões abertas sem DEV_ORCHESTRA_AGENT)


def _fake_proc(tmp_path, chain):
    """Monta um /proc de mentira: `chain` = [(pid, ppid, argv)], do processo atual até o ancestral."""
    for pid, ppid, argv in chain:
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "stat").write_text(f"{pid} (proc name) S {ppid} 0 0")
    return str(tmp_path)


def test_nome_da_sessao_vem_do_processo_claude_ancestral(tmp_path):
    from dev_orchestra.delegator.listener import agent_from_process

    proc = _fake_proc(tmp_path, [
        (30, 20, ["python3", "-m", "dev_orchestra.delegator", "listen"]),
        (20, 10, ["/bin/sh", "-c", "python3 -m dev_orchestra.delegator listen"]),
        (10, 1, ["/home/u/.local/bin/claude", "--name", "dev-o-developer", "--agent", "developer", "--model", "haiku"]),
    ])  # fmt: skip
    assert agent_from_process(30, proc) == "dev-o-developer"
    assert agent_from_process(99, proc) is None  # processo inexistente


def test_nome_com_igual_e_sem_name(tmp_path):
    from dev_orchestra.delegator.listener import _name_from_argv

    assert _name_from_argv(["claude", "--name=abc", "--model", "x"]) == "abc"
    assert _name_from_argv(["claude", "-n", "abc"]) == "abc"
    assert _name_from_argv(["claude", "--model", "x"]) is None


def test_uuid_do_workstream_nunca_vira_nome():
    uuid = "01a1dbf9-7aad-7671-9140-d22f7d30ba0c"
    assert agent_from_env({"AI_MEMORY_WORKSTREAM_ID": uuid}, from_process=False) is None
    assert agent_from_env({"AI_MEMORY_WORKSTREAM": uuid}, from_process=False) is None
    assert agent_from_env({"AI_MEMORY_WORKSTREAM": "dev-o-developer"}, from_process=False) == "dev-o-developer"
    assert agent_from_env({"DEV_ORCHESTRA_AGENT": "x", "AI_MEMORY_WORKSTREAM": "y"}, from_process=False) == "x"
