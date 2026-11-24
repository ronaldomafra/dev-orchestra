"""Agentes passivos: cadastro, linha de comando por CLI, execução (com CLIs simuladas) e sessão."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from dev_orchestra.core import passive, sessions


def make(agent_id="analista", **kw) -> passive.PassiveAgent:
    return passive.PassiveAgent(id=agent_id, **kw)


def done(stdout="", stderr="", code=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def claude_json(result="ok", session="s-1", **extra):
    body = {
        "type": "result", "is_error": False, "result": result, "session_id": session, "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 300,
                  "cache_creation_input_tokens": 40},
        "modelUsage": {"claude-haiku-5-5": {}},
    }  # fmt: skip
    body.update(extra)
    return json.dumps(body)


# ------------------------------------------------------------------ cadastro


def test_salva_le_e_lista(isolated_home):
    agent = make(
        description="Analisa trades",
        model="sonnet",
        effort="high",
        timeout=90,
        reset_every=50,
        permissions=passive.Permissions(commands=["sqlite3 -readonly", "jq"], read_dirs=["/dados"], web=True),
    )
    folder = passive.save(agent, "# perfil\n")
    assert folder == isolated_home / "agentes" / "analista"
    assert (folder / "arquivos").is_dir() and passive.read_profile("analista") == "# perfil\n"
    loaded = passive.load("analista")
    assert loaded == agent
    assert [a.id for a in passive.list_agents()] == ["analista"]
    text = (folder / "agente.toml").read_text()
    assert "[permissoes]" in text and "comandos = [" in text and "descricao" in text


def test_id_invalido_e_agente_ausente():
    with pytest.raises(passive.PassiveError):
        passive.validate_id("Com Espaço")
    with pytest.raises(passive.PassiveError):
        passive.load("nao-existe")
    assert passive.list_agents() == []


def test_rotulos_de_permissao():
    assert passive.Permissions().labels() == ["leitura"]
    p = passive.Permissions(read=False, write=True, any_command=True, web=True, user_mcps=True, user_settings=True)
    assert p.labels() == ["escrita", "comandos: todos", "web", "MCPs", "config. do usuário"]
    assert passive.Permissions(read=False).labels() == ["sem ferramentas"]


def test_perfil_padrao_tem_formato_de_resposta():
    text = passive.default_profile(make(description="Resume logs"))
    assert "Resume logs" in text and "## Formato de resposta" in text and "JSON" in text


def test_remove_apaga_pasta_e_opcionalmente_as_sessoes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    agent = make()
    passive.save(agent, "x")
    claude_folder = sessions.projects_root() / sessions.encode_path(agent.folder)
    claude_folder.mkdir(parents=True)
    assert passive.session_files(agent) == [claude_folder]
    passive.remove("analista")
    assert not agent.folder.exists() and claude_folder.exists()  # sem pedir, as sessões ficam
    passive.save(agent, "x")
    passive.remove("analista", sessions=True)
    assert not claude_folder.exists()


# --------------------------------------------------------------- linha de comando


def test_claude_primeira_chamada_e_retomada():
    agent = make(model="haiku", effort="low", permissions=passive.Permissions(commands=["jq"], read_dirs=["/d"]))
    passive.save(agent, "perfil")
    first = passive.build_call(agent, "pedido")
    args = first.args
    assert args[:4] == ["claude", "-p", "--output-format", "json"] and "--resume" not in args
    assert args[args.index("--model") + 1] == "haiku" and args[args.index("--effort") + 1] == "low"
    assert str(agent.folder / "perfil.md") in args and "--strict-mcp-config" in args
    assert args[args.index("--setting-sources") + 1] == "project,local"
    assert args[args.index("--add-dir") + 1] == "/d"
    denied = args[args.index("--disallowedTools") + 1 : args.index("--permission-mode")]
    assert denied == ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"]
    assert args[args.index("--allowedTools") + 1 :] == ["Read", "Grep", "Glob", "Bash(jq:*)"]
    assert first.stdin == "pedido" and first.cwd == agent.folder
    again = passive.build_call(agent, "x", passive.SessionState(id="abc", calls=3))
    assert again.args[again.args.index("--resume") + 1] == "abc"


def test_claude_recomeca_a_sessao_e_libera_escrita():
    agent = make(reset_every=3, permissions=passive.Permissions(write=True, any_command=True, user_settings=True))
    call = passive.build_call(agent, "x", passive.SessionState(id="abc", calls=3))
    assert "--resume" not in call.args  # chegou ao limite: sessão nova
    assert "--setting-sources" not in call.args
    assert call.args[call.args.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash" in call.args and "Edit" in call.args[call.args.index("--allowedTools") :]


def test_codex_opencode_e_comando():
    codex = passive.build_call(make(cli="codex", model="gpt-6-luna", effort="high"), "p", passive.SessionState(id="t1"))
    assert codex.args[:4] == ["codex", "exec", "resume", "t1"] and 'sandbox_mode="read-only"' in codex.args
    assert codex.output_file is not None
    codex.output_file.unlink(missing_ok=True)
    fresh = passive.build_call(make(cli="codex"), "p")
    assert fresh.args[:3] == ["codex", "exec", "--json"] and "-C" in fresh.args
    fresh.output_file.unlink(missing_ok=True)
    oc = passive.build_call(make(cli="opencode", model="x/y"), "p", passive.SessionState(id="ses"))
    assert oc.args[:4] == ["opencode", "run", "--format", "json"] and oc.args[oc.args.index("--session") + 1] == "ses"
    passive.save(make(cli="comando", command="meu-llm --rapido"), "PERFIL")
    cmd = passive.build_call(passive.load("analista"), "pergunta")
    assert cmd.args == ["meu-llm", "--rapido"] and "PERFIL" in cmd.stdin and "pergunta" in cmd.stdin
    with pytest.raises(passive.PassiveError):
        passive.build_call(make(cli="comando"), "x")


def test_describe_mostra_caminhos_relativos():
    agent = make()
    passive.save(agent, "p")
    lines = passive.describe(agent)
    assert lines[0].startswith("1ª  claude -p") and "--append-system-prompt-file perfil.md" in lines[0]
    assert any("--resume" in line for line in lines)
    assert "POST" in passive.describe(make(cli="ollama", model="m"))[0]


# ------------------------------------------------------------------- execução


def test_execucao_claude_guarda_a_sessao_e_o_uso():
    agent = make()
    passive.save(agent, "p")
    calls = []

    def run(args, **kw):
        calls.append(args)
        return done(claude_json("resposta", session="s-1"))

    out = passive.execute(agent, "pedido", run=run)
    assert out.ok and out.result == "resposta" and out.session_id == "s-1" and out.model == "claude-haiku-5-5"
    assert out.usage == {"input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 300, "cache_write_tokens": 40,
                         "cost_usd": 0.0123}  # fmt: skip
    state = passive.load_session("analista")
    assert (state.id, state.calls, state.total_calls) == ("s-1", 1, 1)
    passive.execute(agent, "de novo", run=run)
    assert "--resume" in calls[1] and calls[1][calls[1].index("--resume") + 1] == "s-1"
    assert passive.load_session("analista").calls == 2


def test_execucao_claude_com_erro_timeout_e_cli_ausente():
    agent = make()
    out = passive.execute(agent, "x", run=lambda a, **k: done(claude_json("limite atingido", is_error=True)))
    assert not out.ok and out.error == "limite atingido"
    out = passive.execute(agent, "x", run=lambda a, **k: done("lixo", "falhou feio", 1))
    assert not out.ok and "falhou feio" in out.error

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, 1)

    assert "tempo limite" in passive.execute(agent, "x", run=slow).error

    def missing(args, **kw):
        raise FileNotFoundError

    assert "não encontrado" in passive.execute(agent, "x", run=missing).error


def test_execucao_ollama_sem_sessao_com_perfil_e_arquivos():
    agent = make(cli="ollama", model="qwen")
    passive.save(agent, "Você resume.")
    (agent.folder / "arquivos" / "glossario.md").write_text("RSI = índice de força relativa")
    seen = {}

    def post(url, body, timeout):
        seen.update(url=url, body=body)
        return {"message": {"content": "resumo"}, "prompt_eval_count": 50, "eval_count": 7, "model": "qwen"}

    out = passive.execute(agent, "texto", http_post=post)
    assert out.ok and out.result == "resumo" and out.usage["input_tokens"] == 50 and out.usage["output_tokens"] == 7
    assert seen["url"].endswith("/api/chat") and seen["body"]["model"] == "qwen"
    system = seen["body"]["messages"][0]["content"]
    assert "Você resume." in system and "RSI = índice" in system
    assert seen["body"]["messages"][-1] == {"role": "user", "content": "texto"}

    def down(url, body, timeout):
        raise OSError("conexão recusada")

    assert "Ollama indisponível" in passive.execute(agent, "x", http_post=down).error


def test_parse_codex_eventos():
    events = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "thread.started", "thread_id": "th-9"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "parcial"}},
            {"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 2, "output_tokens": 3}},
        ]
    )
    out = passive.parse_codex(events, "final\n")
    assert out.ok and out.result == "final" and out.session_id == "th-9" and out.usage["cache_read_tokens"] == 2
    bad = passive.parse_codex(json.dumps({"type": "turn.failed", "error": {"message": "402"}}), "", "", 1)
    assert not bad.ok and bad.error == "402"


def test_execucao_codex_le_o_arquivo_da_ultima_mensagem():
    agent = make(cli="codex")

    def run(args, **kw):
        out = args[args.index("-o") + 1]
        with open(out, "w") as fh:
            fh.write("análise")
        return done(json.dumps({"type": "thread.started", "thread_id": "th-1"}))

    out = passive.execute(agent, "x", run=run)
    assert out.ok and out.result == "análise" and passive.load_session("analista").id == "th-1"


# ---------------------------------------------------------------------- adoção


def test_adotar_sessao_copia_para_a_pasta_do_agente(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    project = tmp_path / "proj"
    project.mkdir()
    source = sessions.projects_root() / sessions.encode_path(project)
    source.mkdir(parents=True)
    (source / "orig.jsonl").write_text(json.dumps({"sessionId": "orig", "type": "user"}) + "\n")
    agent = make()
    passive.save(agent, "p")
    new_id = passive.adopt_session(agent, project, "orig")
    target = sessions.projects_root() / sessions.encode_path(agent.folder) / f"{new_id}.jsonl"
    assert target.is_file() and "orig" not in target.read_text()
    assert (source / "orig.jsonl").read_text().count("orig") == 1  # a original fica intacta
    state = passive.load_session("analista")
    assert (state.id, state.origin) == (new_id, "orig")
    with pytest.raises(passive.PassiveError):
        passive.adopt_session(make(cli="ollama"), project, "orig")
