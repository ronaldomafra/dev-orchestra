"""Prontidão do modo delegador num projeto (serviço, hooks, perfis) e as ações que a TUI oferece para resolver."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..core.models import Project
from . import docker
from .client import DelegatorClient
from .install import install_claude


@dataclass(frozen=True)
class Check:
    key: str  # mode | container | service | install | profiles
    label: str
    ok: bool | None  # None = informativo (nem certo nem errado)
    detail: str
    fix: str = ""  # "" | "up" | "install" | "edit"


def project_checks(
    project: Project,
    url: str,
    *,
    container_state: Callable[[], str | None] = docker.container_state,
    health: Callable[[str], bool] = lambda u: DelegatorClient(u).health(),
    install_dry: Callable[[Project, str], object] = lambda p, u: install_claude(p, url=u, write=False),
) -> list[Check]:
    """Quatro verificações, da mais básica à mais específica do projeto (nada é alterado)."""
    from ..core import profiles

    out: list[Check] = []
    if project.delegator:
        out.append(Check("mode", "Cadastro em modo delegador", True, "ligado"))
    else:
        out.append(
            Check("mode", "Cadastro em modo delegador", False, "desligado: marque em Editar cadastro (p)", "edit")
        )
    state = container_state()
    out.append(
        Check(
            "container",
            "Container do delegador",
            state == "running",
            state or "não existe",
            "" if state == "running" else "up",
        )  # fmt: skip
    )
    alive = health(url)
    out.append(Check("service", "Serviço respondendo", alive, url if alive else f"sem resposta em {url}",
                     "" if alive else "up"))  # fmt: skip
    report = install_dry(project, url)
    pending = list(getattr(report, "changes", []))
    mcp = getattr(report, "mcp", "")
    done = not pending and mcp == "já registrado"
    detail = (
        "hooks, agentes e MCP em dia"
        if done
        else "; ".join([*pending[:4], *([f"MCP: {mcp}"] if mcp != "já registrado" else [])])
    )
    out.append(Check("install", "Hooks, agentes e MCP no projeto", done, detail, "" if done else "install"))
    own = [e.role for e in profiles.list_profiles(project) if e.origin != "padrão"]
    out.append(
        Check(
            "profiles", "Perfis personalizados", None, ", ".join(own) if own else "todos usam o perfil padrão do papel"
        )  # fmt: skip
    )
    return out


def ready(checks: list[Check]) -> bool:
    return all(c.ok is not False for c in checks)
