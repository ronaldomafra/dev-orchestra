"""Subcomandos `dev-orchestra profiles ...`: perfis por papel, gerados por IA com revisão obrigatória."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from .core import config, generator, profiles
from .core.models import Project


def _project(args) -> Project:
    return config.load_project(args.project)


def _role(project: Project, role: str) -> str:
    if role not in project.roles:
        from .cli import CliError

        raise CliError(f"o projeto {project.id} não tem o papel {role!r} (papéis: {', '.join(project.role_order())})")
    return role


def cmd_list(args, out: TextIO, services) -> int:
    project = _project(args)
    for entry in profiles.list_profiles(project):
        where = f" ({entry.path})" if entry.path else ""
        print(f"{entry.role:<14}{entry.origin}{where}", file=out)
    return 0


def cmd_show(args, out: TextIO, services) -> int:
    project = _project(args)
    text = profiles.read_profile(project, _role(project, args.role))
    print(text if text else "(perfil padrão do delegador: nenhuma personalização salva)", file=out)
    return 0


def cmd_providers(args, out: TextIO, services) -> int:
    for info in generator.providers():
        print(f"{info.key:<10}{'ok ' if info.available else '-- '}{info.note}", file=out)
    return 0


def cmd_generate(args, out: TextIO, services) -> int:
    """Gera um rascunho. Só grava com --save: sem isso é uma pré-visualização."""
    from .cli import CliError

    project = _project(args)
    role = _role(project, args.role)
    description = args.description or (
        Path(args.description_file).read_text(encoding="utf-8") if args.description_file else ""
    )
    if not description.strip():
        raise CliError("informe o que o agente deve fazer: --description TEXTO ou --description-file ARQUIVO")
    prompt = generator.build_prompt(project, role, description, data=args.with_data)
    cfg = config.load_global()
    result = generator.generate(
        args.provider, args.model or generator.DEFAULT_MODELS.get(args.provider, ""), prompt, project,
        generic_command=cfg.generator_command,
    )  # fmt: skip
    if not result.ok:
        print(f"erro: {result.error}", file=sys.stderr)
        return 1
    ok, missing = generator.validate_profile(result.text)
    print(result.text, file=out)
    if not ok:
        print(f"\naviso: faltam seções no rascunho: {', '.join(missing)}", file=sys.stderr)
    if args.save:
        source, target = profiles.save_profile(project, role, result.text)
        print(f"\nperfil salvo em {source} e sincronizado em {target}", file=out)
    else:
        print("\n(rascunho: nada foi gravado; use --save depois de revisar)", file=out)
    return 0


def cmd_sync(args, out: TextIO, services) -> int:
    project = _project(args)
    changed = profiles.sync_to_install(project)
    print(("sincronizado: " + ", ".join(changed)) if changed else "nada a sincronizar", file=out)
    return 0


def add_parser(sub) -> None:
    root = sub.add_parser("profiles", help="perfis dos papéis (geração por IA com revisão)")
    psub = root.add_subparsers(dest="profiles_command", required=True)
    for name, func, helptext in (
        ("list", cmd_list, "perfis por papel e de onde vêm"),
        ("show", cmd_show, "mostra o perfil de um papel"),
        ("sync", cmd_sync, "copia os perfis do projeto para a instalação (o que o delegador lê)"),
    ):
        p = psub.add_parser(name, help=helptext)
        p.add_argument("project")
        if name == "show":
            p.add_argument("role")
        p.set_defaults(func=func)
    psub.add_parser("providers", help="provedores de IA disponíveis").set_defaults(func=cmd_providers)
    gen = psub.add_parser("generate", help="gera o rascunho de um perfil (sem gravar, a menos que use --save)")
    gen.add_argument("project")
    gen.add_argument("role")
    gen.add_argument("--provider", choices=generator.PROVIDERS, default="claude")
    gen.add_argument("--model", default="")
    gen.add_argument("--description", default="")
    gen.add_argument("--description-file", default="")
    gen.add_argument("--with-data", action="store_true", help="inclui a estrutura de data/ no contexto")
    gen.add_argument("--save", action="store_true", help="grava no projeto e na instalação (depois da sua revisão)")
    gen.set_defaults(func=cmd_generate)
