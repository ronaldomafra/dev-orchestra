"""Container do delegador (decisão do autor: instalação por Docker, rede do host, estado em volume).

A imagem não instala o pacote: copia o código (`dev_orchestra/`) e as dependências mínimas do servidor.
Montagens: a pasta de instalação (cadastros e perfis) somente leitura em /data e um volume para o SQLite em /state.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from .. import __version__
from ..core import paths
from ..core.liveness import Runner, run_command
from . import DEFAULT_PORT

CONTAINER = "dev-orchestra-delegator"
VOLUME = "dev-orchestra-delegator-data"
IMAGE = f"dev-orchestra-delegator:{__version__}"

DOCKERFILE = """\
FROM python:3.12-slim
RUN pip install --no-cache-dir "mcp>=1.12,<2" "pydantic>=2.10,<3" "tomli-w>=1.0,<2" "websockets>=14,<17"
WORKDIR /app
COPY dev_orchestra /app/dev_orchestra
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 DEV_ORCHESTRA_HOME=/data DEV_ORCHESTRA_DB=/state/estado.db
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:{port}/healthz', timeout=2).status == 200 else 1)"
CMD ["python", "-m", "dev_orchestra.delegator", "--host", "127.0.0.1", "--port", "{port}"]
"""


def package_dir() -> Path:
    return Path(__file__).resolve().parent.parent


HASH_LABEL = "dev-orchestra.hash"


def code_hash() -> str:
    """Impressão digital do código que vai para a imagem (o pacote sem a TUI): muda quando a ferramenta é atualizada."""
    digest = hashlib.sha256()
    root = package_dir()
    for file in sorted(root.rglob("*.py")):
        rel = file.relative_to(root)
        if rel.parts[0] in ("tui", "__pycache__") or "__pycache__" in rel.parts:
            continue
        digest.update(str(rel).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:16]


def build_command(context: Path) -> list[str]:
    return ["docker", "build", "--label", f"{HASH_LABEL}={code_hash()}", "-t", IMAGE, str(context)]


def image_hash(run: Runner = run_command) -> str | None:
    """Impressão digital da imagem instalada (None = sem imagem)."""
    fmt = '{{index .Config.Labels "' + HASH_LABEL + '"}}'
    code, out = run(["docker", "image", "inspect", "-f", fmt, IMAGE], timeout=15.0)
    return out.strip() if code == 0 else None


def run_command_args(port: int = DEFAULT_PORT, home: Path | None = None) -> list[str]:
    return [
        "docker", "run", "-d", "--name", CONTAINER, "--restart", "unless-stopped",
        "--network", "host",  # o container precisa alcançar o App Server do Codex em 127.0.0.1 do host
        "-v", f"{VOLUME}:/state",
        "-v", f"{home or paths.install_dir()}:/data:ro",
        IMAGE, "python", "-m", "dev_orchestra.delegator", "--host", "127.0.0.1", "--port", str(port),
    ]  # fmt: skip


def prepare_context(port: int = DEFAULT_PORT) -> Path:
    """Contexto de build temporário: o Dockerfile e uma cópia do pacote (sem caches nem a TUI)."""
    ctx = Path(tempfile.mkdtemp(prefix="dev-orchestra-ctx-"))
    shutil.copytree(package_dir(), ctx / "dev_orchestra", ignore=shutil.ignore_patterns("__pycache__", "tui", "*.pyc"))
    (ctx / "Dockerfile").write_text(DOCKERFILE.format(port=port), encoding="utf-8")
    return ctx


def container_state(run: Runner = run_command) -> str | None:
    """`running`, `exited`... ou None se o container não existe (ou o Docker não responde)."""
    code, out = run(["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER], timeout=15.0)
    return out.strip() if code == 0 and out.strip() else None


def up(port: int = DEFAULT_PORT, run: Runner = run_command, *, rebuild: bool = False) -> list[str]:
    """Constrói a imagem (se faltar ou estiver desatualizada) e sobe o container. Devolve as linhas de relatório.

    Com o container rodando, só reconstrói com `rebuild` (reiniciar derruba as conexões por alguns segundos;
    o estado fica no volume)."""
    lines: list[str] = []
    state = container_state(run)
    installed = image_hash(run)
    outdated = installed is not None and installed != code_hash()
    if state == "running" and not rebuild:
        if outdated:
            return [
                "O delegador está rodando com uma versão antiga do código.",
                "Atualize com `dev-orchestra delegator docker up --rebuild` (reinicia o serviço; o estado fica no volume).",
            ]
        return ["O delegador já está rodando."]
    if installed is None or outdated or rebuild:
        ctx = prepare_context(port)
        try:
            code, out = run(build_command(ctx), timeout=600.0)
        finally:
            shutil.rmtree(ctx, ignore_errors=True)
        if code != 0:
            return ["Falha ao construir a imagem do delegador (rode `docker build` e veja o erro)."]
        lines.append(f"Imagem {IMAGE} construída.")
    if state is not None:  # existe parado: recria (o estado fica no volume)
        run(["docker", "rm", "-f", CONTAINER], timeout=30.0)
    code, out = run(run_command_args(port), timeout=60.0)
    if code != 0:
        return [*lines, "Falha ao iniciar o container do delegador."]
    lines.append(f"Container {CONTAINER} iniciado (rede do host, 127.0.0.1:{port}).")
    return lines


def down(run: Runner = run_command) -> list[str]:
    if container_state(run) is None:
        return ["O delegador não está instalado."]
    run(["docker", "rm", "-f", CONTAINER], timeout=30.0)
    return [f"Container {CONTAINER} removido (o estado continua no volume {VOLUME})."]
