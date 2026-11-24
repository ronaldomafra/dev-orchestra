"""Sessões do Claude Code de um projeto (os transcritos em `~/.claude/projects/`), para adotar uma como agente."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .liveness import Services

HEAD_LINES = 60
TAIL_BYTES = 262_144


@dataclass(frozen=True)
class SessionInfo:
    id: str
    title: str
    size: int
    started: datetime | None
    last: datetime | None
    model: str | None
    first_message: str = ""
    last_message: str = ""
    open_pid: int | None = None  # a sessão está aberta agora (não pode ser adotada sem fechar)

    @property
    def size_text(self) -> str:
        mb = self.size / 1_048_576
        return (
            f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB".replace(".", ",") if mb >= 0.1 else f"{self.size // 1024} KB"
        )


def projects_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"


def encode_path(path: Path | str) -> str:
    """O Claude nomeia a pasta dos transcritos trocando tudo que não é letra ou número por `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).expanduser().resolve()))


def _when(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _text_of(message: object) -> str:
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _clean(text: str, limit: int = 110) -> str:
    text = re.sub(r"<[^>]+>", " ", text)  # tags de sistema dos transcritos
    return " ".join(text.split())[:limit]


def read_session(path: Path) -> SessionInfo:
    """Lê só o começo e o fim do arquivo (transcritos podem ter dezenas de MB)."""
    stat = path.stat()
    title = started = last = model = None
    first_message = last_message = ""
    with path.open("rb") as fh:
        for index, raw in enumerate(fh):
            if index >= HEAD_LINES:
                break
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if started is None and d.get("timestamp"):
                started = _when(d["timestamp"])
            title = d.get("customTitle") or d.get("title") or title
            if d.get("type") == "user" and not first_message:
                first_message = _clean(_text_of(d.get("message")))
    tail_lines: list[bytes] = []
    with path.open("rb") as fh:
        fh.seek(max(0, stat.st_size - TAIL_BYTES))
        tail_lines = fh.read().splitlines()[1:] if stat.st_size > TAIL_BYTES else fh.read().splitlines()
    for raw in reversed(tail_lines):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        last = last or (_when(d.get("timestamp")) if d.get("timestamp") else None)
        title = title or d.get("customTitle")
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        model = model or msg.get("model")
        if d.get("type") == "user" and not last_message:
            last_message = _clean(_text_of(msg))
        if last and model and last_message:
            break
    return SessionInfo(path.stem, title or "(sem título)", stat.st_size, started, last or datetime.fromtimestamp(stat.st_mtime).astimezone(),
                       (model or "").removeprefix("claude-") or None, first_message, last_message)  # fmt: skip


def list_sessions(
    project_path: Path, services: Services | None = None, *, root: Path | None = None
) -> list[SessionInfo]:
    """Sessões do projeto, da mais recente para a mais antiga (marca as que estão abertas agora)."""
    folder = (root or projects_root()) / encode_path(project_path)
    if not folder.is_dir():
        return []
    open_by_id: dict[str, int | None] = {}
    sessions_fn = (services.claude_sessions if services else None) or Services().claude_sessions
    for s in sessions_fn() or []:
        if s.session_id:
            open_by_id[s.session_id] = s.pid
    out = []
    for file in folder.glob("*.jsonl"):
        try:
            info = read_session(file)
        except OSError:
            continue
        out.append(SessionInfo(**{**info.__dict__, "open_pid": open_by_id.get(info.id)}))
    return sorted(out, key=lambda s: s.last or datetime.min.astimezone(), reverse=True)


class SessionError(Exception):
    """Falha ao copiar uma sessão do Claude."""


def fork_session(project_path: Path, base_id: str, *, root: Path | None = None) -> str:
    """Copia a sessão `base_id` para uma sessão nova (outro id) e devolve o id da cópia.

    É o que `claude --fork-session` faz, só que sem abrir o Claude nem gastar uma chamada. Precisamos da cópia ANTES da
    primeira subida: o `ai-memory run` guarda o id passado em `--resume` como a sessão do workstream, e o Claude só cria
    o id novo depois. Com a cópia já feita, o workstream aponta para ela e as subidas seguintes a retomam (a original
    nunca é tocada)."""
    return copy_session(project_path, base_id, project_path, root=root)


def copy_session(source_path: Path, base_id: str, target_path: Path, *, root: Path | None = None) -> str:
    """Copia a sessão `base_id` da pasta de projeto `source_path` para a de `target_path` com um id novo.

    O Claude só retoma (`--resume`) sessões da pasta de trabalho atual: a cópia vai para a pasta do destino."""
    base = root or projects_root()
    source_folder, folder = base / encode_path(source_path), base / encode_path(target_path)
    source = source_folder / f"{base_id}.jsonl"
    if not source.is_file():
        raise SessionError(f"sessão {base_id} não encontrada em {source_folder}")
    folder.mkdir(parents=True, exist_ok=True)
    new_id = str(uuid.uuid4())
    target = folder / f"{new_id}.jsonl"
    old, new = base_id.encode(), new_id.encode()
    try:
        with source.open("rb") as src, target.open("wb") as dst:
            for line in src:
                dst.write(line.replace(old, new))
        target.chmod(0o600)
        extras = source_folder / base_id  # subpastas da sessão (resultados de ferramentas, subagentes)
        if extras.is_dir():
            shutil.copytree(extras, folder / new_id)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise SessionError(f"não consegui copiar a sessão {base_id}: {exc}") from exc
    return new_id


def materialize_forks(project, *, root: Path | None = None) -> list[str]:
    """Cria as cópias que o cadastro pede e ainda não existem. Altera os papéis do `project` e devolve o relato."""
    notes = []
    for role_id, role in project.roles.items():
        if role.cli != "claude" or not role.resume or not role.resume_fork or role.resume_origin:
            continue
        copy = fork_session(project.path, role.resume, root=root)
        role.resume_origin, role.resume = role.resume, copy
        notes.append(f"{role_id}: cópia {copy[:8]} criada da sessão {role.resume_origin[:8]} (a original fica intacta)")
    return notes
