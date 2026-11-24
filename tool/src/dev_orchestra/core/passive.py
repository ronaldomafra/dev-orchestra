"""Agentes passivos: auxiliares sem sessão aberta, fora dos projetos, chamados pelo id através do delegador.

Cada agente vive em `<instalação>/agentes/<id>/`:

- `agente.toml`: configuração (CLI, modelo, esforço, permissões, pastas de leitura, tempo limite);
- `perfil.md`: instruções do agente, inclusive o formato de resposta;
- `arquivos/`: material de consulta do agente;
- `sessao.json`: estado da sessão (id para o `--resume`, consultas feitas). Escrito pelo executor, não pela pessoa.

A pasta do agente é o diretório de trabalho da CLI: a sessão dele nunca se mistura com as dos projetos.
Esta camada não sabe nada do delegador: monta e roda a chamada de uma consulta e devolve o resultado com o uso.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field

from . import paths
from .config import atomic_write_text

CLIS = ("claude", "codex", "ollama", "opencode", "comando")
PassiveCli = Literal["claude", "codex", "ollama", "opencode", "comando"]
SESSION_CLIS = ("claude", "codex", "opencode")  # as que retomam a própria sessão (`--resume`)
READ_CLIS = ("claude", "codex", "opencode")  # as que leem arquivos sozinhas
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 300
INLINE_FILES_LIMIT = 20_000  # Ollama: arquivos de consulta pequenos vão junto do pedido (caracteres)
FILE_NAME = "agente.toml"
PROFILE_NAME = "perfil.md"
FILES_DIR = "arquivos"
SESSION_NAME = "sessao.json"


class PassiveError(Exception):
    """Cadastro inválido ou ausente."""


class Permissions(BaseModel):
    read: bool = True
    write: bool = False
    commands: list[str] = []  # prefixos liberados (ex.: "sqlite3 -readonly", "jq")
    any_command: bool = False
    web: bool = False
    user_mcps: bool = False  # MCPs configurados para o usuário (o padrão é nenhum)
    user_settings: bool = False  # carrega ~/.claude/settings.json (hooks como os do ai-memory, plugins)
    read_dirs: list[str] = []  # pastas extras, além da do próprio agente

    def labels(self) -> list[str]:
        out = []
        if self.read:
            out.append("leitura")
        if self.write:
            out.append("escrita")
        if self.any_command:
            out.append("comandos: todos")
        elif self.commands:
            out.append(f"comandos: {len(self.commands)}")
        if self.web:
            out.append("web")
        if self.user_mcps:
            out.append("MCPs")
        if self.user_settings:
            out.append("config. do usuário")
        return out or ["sem ferramentas"]


class PassiveAgent(BaseModel):
    id: str
    description: str = ""
    cli: PassiveCli = "claude"
    model: str | None = None
    effort: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    reset_every: int = 0  # recomeça a sessão a cada N consultas (0 = nunca)
    command: str | None = None  # CLI "comando": linha de comando que lê o pedido no stdin e responde no stdout
    ollama_url: str | None = None
    permissions: Permissions = Field(default_factory=Permissions)

    @property
    def folder(self) -> Path:
        return agent_dir(self.id)


@dataclass
class SessionState:
    id: str | None = None
    origin: str | None = None  # sessão de onde a atual foi copiada (adoção)
    calls: int = 0  # consultas feitas nesta sessão
    total_calls: int = 0


@dataclass
class Outcome:
    ok: bool
    result: str = ""
    error: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    model: str | None = None
    duration_ms: int = 0


# ------------------------------------------------------------------ armazenamento


def root() -> Path:
    return paths.install_dir() / "agentes"


def agent_dir(agent_id: str) -> Path:
    return root() / agent_id


def validate_id(agent_id: str) -> str:
    if not ID_RE.match(agent_id or ""):
        raise PassiveError("id inválido: use minúsculas, números, - e _ (começando por letra ou número)")
    return agent_id


def _to_toml(agent: PassiveAgent) -> dict[str, Any]:
    p = agent.permissions
    data: dict[str, Any] = {
        "agente": {
            k: v
            for k, v in {
                "id": agent.id,
                "descricao": agent.description,
                "cli": agent.cli,
                "modelo": agent.model,
                "esforco": agent.effort,
                "tempo_limite": agent.timeout,
                "recomecar_a_cada": agent.reset_every,
                "comando": agent.command,
                "ollama_url": agent.ollama_url,
            }.items()  # fmt: skip
            if v not in (None, "")
        },
        "permissoes": {
            "ler": p.read,
            "escrever": p.write,
            "comandos": list(p.commands),
            "qualquer_comando": p.any_command,
            "web": p.web,
            "mcps": p.user_mcps,
            "config_usuario": p.user_settings,
            "pastas": list(p.read_dirs),
        },  # fmt: skip
    }
    return data


def _from_toml(data: dict[str, Any]) -> PassiveAgent:
    a, p = data.get("agente", {}), data.get("permissoes", {})
    try:
        return PassiveAgent(
            id=a.get("id", ""), description=a.get("descricao", ""), cli=a.get("cli", "claude"), model=a.get("modelo"),
            effort=a.get("esforco"), timeout=int(a.get("tempo_limite", DEFAULT_TIMEOUT)),
            reset_every=int(a.get("recomecar_a_cada", 0)), command=a.get("comando"), ollama_url=a.get("ollama_url"),
            permissions=Permissions(
                read=bool(p.get("ler", True)), write=bool(p.get("escrever", False)), commands=list(p.get("comandos", [])),
                any_command=bool(p.get("qualquer_comando", False)), web=bool(p.get("web", False)),
                user_mcps=bool(p.get("mcps", False)), user_settings=bool(p.get("config_usuario", False)),
                read_dirs=list(p.get("pastas", [])),
            ),
        )  # fmt: skip
    except (ValueError, TypeError) as exc:
        raise PassiveError(f"agente.toml inválido: {exc}") from exc


def load(agent_id: str) -> PassiveAgent:
    path = agent_dir(agent_id) / FILE_NAME
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PassiveError(f"agente passivo '{agent_id}' não existe") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PassiveError(f"não consegui ler {path}: {exc}") from exc
    agent = _from_toml(data)
    if agent.id != agent_id:
        agent = agent.model_copy(update={"id": agent_id})  # a pasta manda
    return agent


def list_agents() -> list[PassiveAgent]:
    folder = root()
    if not folder.is_dir():
        return []
    out = []
    for item in sorted(folder.iterdir()):
        if item.is_dir() and (item / FILE_NAME).is_file() and ID_RE.match(item.name):
            try:
                out.append(load(item.name))
            except PassiveError:
                continue
    return out


def exists(agent_id: str) -> bool:
    return (agent_dir(agent_id) / FILE_NAME).is_file()


def save(agent: PassiveAgent, profile: str | None = None) -> Path:
    """Grava o cadastro (e o perfil, se vier). Cria `arquivos/`. Devolve a pasta do agente."""
    validate_id(agent.id)
    folder = agent_dir(agent.id)
    (folder / FILES_DIR).mkdir(parents=True, exist_ok=True)
    atomic_write_text(folder / FILE_NAME, tomli_w.dumps(_to_toml(agent)))
    if profile is not None:
        atomic_write_text(folder / PROFILE_NAME, profile.strip() + "\n")
    return folder


def read_profile(agent_id: str) -> str:
    try:
        return (agent_dir(agent_id) / PROFILE_NAME).read_text(encoding="utf-8")
    except OSError:
        return ""


def list_files(agent_id: str) -> list[Path]:
    folder = agent_dir(agent_id) / FILES_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file())


def load_session(agent_id: str) -> SessionState:
    try:
        data = json.loads((agent_dir(agent_id) / SESSION_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SessionState()
    if not isinstance(data, dict):
        return SessionState()
    return SessionState(
        id=data.get("id") or None, origin=data.get("origem") or None, calls=int(data.get("consultas", 0) or 0),
        total_calls=int(data.get("total", 0) or 0),
    )  # fmt: skip


def save_session(agent_id: str, state: SessionState) -> None:
    body = {"id": state.id, "origem": state.origin, "consultas": state.calls, "total": state.total_calls}
    atomic_write_text(agent_dir(agent_id) / SESSION_NAME, json.dumps(body, ensure_ascii=False, indent=2) + "\n")


def session_files(agent: PassiveAgent) -> list[Path]:
    """Arquivos de sessão do agente no armazenamento da CLI (para mostrar e, se pedido, apagar ao remover)."""
    if agent.cli != "claude":
        return []
    from .sessions import encode_path, projects_root

    folder = projects_root() / encode_path(agent_dir(agent.id))
    return [folder] if folder.is_dir() else []


def remove(agent_id: str, *, sessions: bool = False) -> list[str]:
    """Apaga a pasta do agente (e as sessões da CLI, se pedido). O histórico no delegador fica."""
    done = []
    agent = load(agent_id)
    if sessions:
        for item in session_files(agent):
            shutil.rmtree(item, ignore_errors=True)
            done.append(f"sessões do Claude apagadas: {item}")
    shutil.rmtree(agent_dir(agent_id), ignore_errors=True)
    done.append(f"pasta do agente apagada: {agent_dir(agent_id)}")
    return done


def adopt_session(agent: PassiveAgent, source_project: Path, session_id: str) -> str:
    """Claude: copia uma sessão existente para a pasta do agente (id novo; a original não é tocada)."""
    from .sessions import copy_session

    if agent.cli != "claude":
        raise PassiveError("adotar sessão existe só para o Claude")
    agent_dir(agent.id).mkdir(parents=True, exist_ok=True)
    new_id = copy_session(source_project, session_id, agent_dir(agent.id))
    state = load_session(agent.id)
    save_session(agent.id, SessionState(id=new_id, origin=session_id, calls=0, total_calls=state.total_calls))
    return new_id


# ---------------------------------------------------------------------- execução


@dataclass
class Call:
    args: list[str]
    stdin: str
    cwd: Path
    output_file: Path | None = None  # Codex: a última mensagem vai para um arquivo


def claude_tools(perm: Permissions) -> tuple[list[str], list[str]]:
    """(liberadas, negadas). No modo `-p` o que não está liberado é recusado; negar explicitamente é a garantia."""
    allowed, denied = [], []
    (allowed if perm.read else denied).extend(["Read", "Grep", "Glob"])
    (allowed if perm.write else denied).extend(["Edit", "Write", "NotebookEdit"])
    if perm.any_command:
        allowed.append("Bash")
    elif perm.commands:
        allowed.extend(f"Bash({c.strip()}:*)" for c in perm.commands if c.strip())
    else:
        denied.append("Bash")
    (allowed if perm.web else denied).extend(["WebFetch", "WebSearch"])
    return allowed, denied


def session_to_resume(agent: PassiveAgent, state: SessionState) -> str | None:
    if agent.cli not in SESSION_CLIS or not state.id:
        return None
    if agent.reset_every and state.calls >= agent.reset_every:
        return None  # hora de recomeçar a sessão
    return state.id


def read_dirs(agent: PassiveAgent) -> list[str]:
    return [str(Path(d).expanduser()) for d in agent.permissions.read_dirs if d.strip()]


def build_call(agent: PassiveAgent, prompt: str, state: SessionState | None = None) -> Call:
    """Monta a linha de comando de UMA consulta (não serve ao Ollama, que é HTTP)."""
    state = state or SessionState()
    folder = agent_dir(agent.id)
    resume = session_to_resume(agent, state)
    perm = agent.permissions
    if agent.cli == "claude":
        args = ["claude", "-p", "--output-format", "json"]
        if agent.model:
            args += ["--model", agent.model]
        if agent.effort:
            args += ["--effort", agent.effort]
        if resume:
            args += ["--resume", resume]
        if (folder / PROFILE_NAME).is_file():
            args += ["--append-system-prompt-file", str(folder / PROFILE_NAME)]
        if not perm.user_mcps:
            args += ["--strict-mcp-config"]  # sem `--mcp-config`: nenhum MCP do usuário
        if not perm.user_settings:
            args += ["--setting-sources", "project,local"]  # sem hooks/plugins do usuário (ex.: ai-memory)
        dirs = read_dirs(agent)
        if dirs:
            args += ["--add-dir", *dirs]
        allowed, denied = claude_tools(perm)
        if denied:
            args += ["--disallowedTools", *denied]
        args += ["--permission-mode", "acceptEdits" if perm.write else "default"]
        if allowed:
            args += ["--allowedTools", *allowed]
        return Call(args, prompt, folder)
    if agent.cli == "codex":
        out = Path(tempfile.mkstemp(prefix="passivo-", suffix=".txt")[1])
        sandbox = "workspace-write" if perm.write else "read-only"
        common = ["--json", "--skip-git-repo-check", "-o", str(out), "-c", f'sandbox_mode="{sandbox}"']
        if agent.model:
            common += ["-m", agent.model]
        if agent.effort:
            common += ["-c", f'model_reasoning_effort="{agent.effort}"']
        if resume:
            args = ["codex", "exec", "resume", resume, *common, "-"]
        else:
            args = ["codex", "exec", *common, "-C", str(folder), "-"]
        return Call(args, _with_profile(agent, prompt), folder, output_file=out)
    if agent.cli == "opencode":
        args = ["opencode", "run", "--format", "json"]
        if agent.model:
            args += ["-m", agent.model]
        if resume:
            args += ["--session", resume]
        return Call([*args, _with_profile(agent, prompt)], "", folder)
    if agent.cli == "comando":
        if not agent.command:
            raise PassiveError("CLI 'comando' sem a linha de comando")
        return Call(shlex.split(agent.command), _with_profile(agent, prompt, inline_files=True), folder)
    raise PassiveError(f"CLI sem linha de comando: {agent.cli}")


def _with_profile(agent: PassiveAgent, prompt: str, *, inline_files: bool = False) -> str:
    """CLIs sem perfil de sistema: o perfil (e, para quem não lê arquivos, os arquivos pequenos) vai no pedido."""
    profile = read_profile(agent.id).strip()
    parts = []
    if profile:
        parts.append(f"<perfil>\n{profile}\n</perfil>")
    if inline_files:
        files = inline_files_text(agent.id)
        if files:
            parts.append(files)
    parts.append(f"<pedido>\n{prompt}\n</pedido>" if parts else prompt)
    return "\n\n".join(parts)


def inline_files_text(agent_id: str, limit: int = INLINE_FILES_LIMIT) -> str:
    out, used = [], 0
    for file in list_files(agent_id):
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if used + len(text) > limit:
            continue
        used += len(text)
        rel = file.relative_to(agent_dir(agent_id) / FILES_DIR)
        out.append(f'<arquivo nome="{rel}">\n{text.strip()}\n</arquivo>')
    return "\n".join(out)


def describe(agent: PassiveAgent) -> list[str]:
    """Linhas legíveis de como cada consulta roda (resumo do formulário e `passive show`)."""
    state = load_session(agent.id)
    if agent.cli == "ollama":
        url = agent.ollama_url or OLLAMA_URL
        return [
            f"POST {url}/api/chat  modelo {agent.model or '?'}",
            "sem sessão: perfil e arquivos pequenos vão no pedido",
        ]
    try:
        first = build_call(agent, "<pedido>", SessionState())
    except PassiveError as exc:
        return [str(exc)]
    if first.output_file:
        first.output_file.unlink(missing_ok=True)
    folder = str(agent_dir(agent.id)) + "/"
    shown = [a.replace(folder, "") for a in first.args if a != "-"]  # a pasta do agente é o diretório de trabalho
    lines = ["1ª  " + shlex.join(shown)]
    if agent.cli in SESSION_CLIS:
        lines.append(
            "depois  … "
            + {"claude": "--resume", "codex": "exec resume", "opencode": "--session"}[agent.cli]
            + " <sessão do agente>"
        )
        if state.id:
            lines.append(f"sessão atual {state.id[:8]} · {state.calls} consultas")
    lines.append("pedido pelo stdin · sai ao responder" if first.stdin else "pedido como argumento · sai ao responder")
    return lines


Runner = Callable[..., "subprocess.CompletedProcess[str]"]
HttpPost = Callable[[str, dict, float], dict]


def _http_post(url: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - Ollama local
        return json.loads(response.read() or b"{}")


def execute(
    agent: PassiveAgent,
    prompt: str,
    *,
    run: Runner = subprocess.run,
    http_post: HttpPost = _http_post,
    clock: Callable[[], float] = time.monotonic,
    persist: bool = True,
) -> Outcome:
    """Roda UMA consulta e devolve o resultado. Nunca levanta: o erro volta em `Outcome.error`.

    Com `persist`, atualiza `sessao.json` (id novo, contagem) para a próxima consulta retomar a mesma sessão."""
    started = clock()
    state = load_session(agent.id)
    resumed = session_to_resume(agent, state)
    try:
        if agent.cli == "ollama":
            outcome = _run_ollama(agent, prompt, http_post)
        else:
            outcome = _run_cli(agent, prompt, state, run)
    except PassiveError as exc:
        outcome = Outcome(False, error=str(exc))
    outcome.duration_ms = int((clock() - started) * 1000)
    if persist and agent.cli in SESSION_CLIS and outcome.session_id:
        same = resumed is not None and outcome.session_id == resumed
        save_session(
            agent.id,
            SessionState(
                id=outcome.session_id,
                origin=state.origin if same else None,
                calls=(state.calls if same else 0) + 1,
                total_calls=state.total_calls + 1,
            ),
        )
    elif persist and outcome.ok:
        save_session(agent.id, SessionState(state.id, state.origin, state.calls + 1, state.total_calls + 1))
    return outcome


def _run_cli(agent: PassiveAgent, prompt: str, state: SessionState, run: Runner) -> Outcome:
    call = build_call(agent, prompt, state)
    call.cwd.mkdir(parents=True, exist_ok=True)
    try:
        try:
            done = run(
                call.args, cwd=str(call.cwd), input=call.stdin, capture_output=True, text=True,
                timeout=agent.timeout, check=False,
            )  # fmt: skip
        except FileNotFoundError:
            return Outcome(False, error=f"{call.args[0]}: comando não encontrado")
        except subprocess.TimeoutExpired:
            return Outcome(False, error=f"tempo limite ({agent.timeout} s) excedido")
        stdout, stderr = done.stdout or "", (done.stderr or "").strip()
        if agent.cli == "claude":
            return parse_claude(stdout, stderr, done.returncode)
        if agent.cli == "codex":
            last = ""
            if call.output_file and call.output_file.is_file():
                last = call.output_file.read_text(encoding="utf-8", errors="replace")
            return parse_codex(stdout, last, stderr, done.returncode)
        if agent.cli == "opencode":
            return parse_opencode(stdout, stderr, done.returncode)
        if done.returncode != 0:
            return Outcome(False, error=(stderr or stdout)[-1500:] or f"saiu com código {done.returncode}")
        return Outcome(True, result=stdout.strip())
    finally:
        if call.output_file:
            call.output_file.unlink(missing_ok=True)


def parse_claude(stdout: str, stderr: str = "", code: int = 0) -> Outcome:
    data = None
    for line in reversed(stdout.strip().splitlines()):  # o JSON final é a última linha
        try:
            data = json.loads(line)
            break
        except ValueError:
            continue
    if not isinstance(data, dict):
        return Outcome(False, error=(stderr or stdout)[-1500:] or f"saída inesperada (código {code})")
    usage = data.get("usage") or {}
    model = next(iter(data.get("modelUsage") or {}), None)
    out = Outcome(
        ok=not data.get("is_error") and code == 0,
        result=str(data.get("result") or ""),
        session_id=data.get("session_id"),
        model=model,
        usage={
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_creation_input_tokens") or 0),
            "cost_usd": float(data.get("total_cost_usd") or 0.0),
        },
    )
    if not out.ok:
        out.error = out.result or (stderr or "")[-1500:] or str(data.get("subtype") or "erro")
    return out


def parse_codex(stdout: str, last_message: str, stderr: str = "", code: int = 0) -> Outcome:
    session_id, usage, error, text = None, {}, "", ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "thread.started":
            session_id = event.get("thread_id")
        elif kind == "turn.completed":
            u = event.get("usage") or {}
            usage = {
                "input_tokens": int(u.get("input_tokens") or 0),
                "output_tokens": int(u.get("output_tokens") or 0),
                "cache_read_tokens": int(u.get("cached_input_tokens") or 0),
            }
        elif kind in ("turn.failed", "error"):
            error = str((event.get("error") or {}).get("message") or event.get("message") or "erro")
        elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
            text = str(event["item"].get("text") or text)
    result = (last_message or text).strip()
    if code != 0 or error or not result:
        return Outcome(
            False,
            error=error or (stderr or stdout)[-1500:] or f"saiu com código {code}",
            session_id=session_id,
            usage=usage,
        )
    return Outcome(True, result=result, session_id=session_id, usage=usage)


def parse_opencode(stdout: str, stderr: str = "", code: int = 0) -> Outcome:
    session_id, texts, usage = None, [], {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        session_id = event.get("sessionID") or event.get("session_id") or session_id
        part = event.get("part") or {}
        if event.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
        tokens = (part.get("tokens") if isinstance(part, dict) else None) or {}
        if tokens:
            usage = {"input_tokens": int(tokens.get("input") or 0), "output_tokens": int(tokens.get("output") or 0)}
    result = "".join(texts).strip()
    if code != 0 or not result:
        return Outcome(False, error=(stderr or stdout)[-1500:] or f"saiu com código {code}", session_id=session_id)
    return Outcome(True, result=result, session_id=session_id, usage=usage)


def _run_ollama(agent: PassiveAgent, prompt: str, http_post: HttpPost) -> Outcome:
    system = read_profile(agent.id).strip()
    files = inline_files_text(agent.id)
    if files:
        system = f"{system}\n\nArquivos de consulta:\n{files}".strip()
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    url = (agent.ollama_url or OLLAMA_URL).rstrip("/")
    try:
        reply = http_post(
            f"{url}/api/chat", {"model": agent.model, "messages": messages, "stream": False}, agent.timeout
        )
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return Outcome(False, error=f"Ollama indisponível em {url}: {exc}")
    text = str(((reply or {}).get("message") or {}).get("content") or "").strip()
    if not text:
        return Outcome(False, error=str((reply or {}).get("error") or "resposta vazia do Ollama"))
    usage = {
        "input_tokens": int(reply.get("prompt_eval_count") or 0),
        "output_tokens": int(reply.get("eval_count") or 0),
        "cost_usd": 0.0,
    }
    return Outcome(True, result=text, usage=usage, model=reply.get("model") or agent.model)


# ------------------------------------------------------------------------ perfil

PROFILE_TEMPLATE = """\
# Agente passivo: {id}

## Missão
{description}

## Como trabalhar
- Você é consultado sob demanda: cada pedido chega sozinho e deve ser respondido por completo nesta resposta.
- Faça somente análise: não altere arquivos, configuração nem dados{write_note}.
- Use o material de `arquivos/` (na sua pasta de trabalho) quando ele ajudar; cite o arquivo quando usá-lo.
- Se faltar dado para concluir, diga exatamente o que falta em vez de supor.

## Formato de resposta
Responda somente com um objeto JSON, sem texto antes ou depois:
{{"resposta": "...", "confianca": 0.0, "motivo": "...", "faltou": []}}
"""


def default_profile(agent: PassiveAgent) -> str:
    return PROFILE_TEMPLATE.format(
        id=agent.id,
        description=agent.description or "(descreva a missão do agente)",
        write_note=" (a escrita está liberada só para o que o pedido pedir explicitamente)"
        if agent.permissions.write
        else "",
    )


def generation_prompt(agent: PassiveAgent, request: str) -> str:
    """Pedido para uma IA escrever o perfil do agente passivo (a pessoa revisa antes de salvar)."""
    perms = ", ".join(agent.permissions.labels())
    dirs = ", ".join(agent.permissions.read_dirs) or "nenhuma além da pasta do agente"
    return f"""Escreva o PERFIL (instruções de sistema, em português do Brasil, Markdown) de um agente auxiliar PASSIVO.

O agente é consultado sob demanda por outros sistemas: cada pedido chega sozinho, ele responde e a execução termina.
Ele não conversa com pessoas, não abre tarefas e não fala com outros agentes.

Id: {agent.id}
O que ele faz (descrição da pessoa):
{request.strip() or agent.description}

Permissões técnicas já configuradas (não as repita como regra, mas não peça nada além delas): {perms}
Pastas que ele pode ler além da própria: {dirs}
Material de consulta: a pasta `arquivos/` no diretório de trabalho dele.

O perfil deve ter estas seções: `# Agente passivo: {agent.id}`, `## Missão`, `## Como analisar`, `## Não faça`,
`## Formato de resposta`. O formato de resposta deve ser preciso (de preferência JSON com os campos nomeados e
descritos), porque um programa vai ler a resposta. Responda somente com o perfil, sem comentários.
"""
