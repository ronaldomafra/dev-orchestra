"""Geração do plano de subida e retomada das sessões (só texto; a pessoa executa cada comando)."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from .contract import CONTRACT_MODEL_TARGET, agents_md_state, missing_files, own_docs_cite_delegator
from .models import GlobalConfig, Project

ROLE_TITLES = {
    "orchestrator": "Orchestrator",
    "developer": "Developer",
    "qa": "QA",
    "planner": "Planner",
}

# flags do Claude por restrição de escrita (o texto do perfil não impede nada; isto sim)
PERMISSION_FLAGS = {
    "plan": ["--permission-mode", "plan"],
    "read-only": ["--disallowedTools", "Edit", "Write", "NotebookEdit"],
}

MODE_CLAUDE = "claude-code"
MODE_CODEX = "codex"
MODE_MIXED = "misto"
MODE_DELEGATOR = "delegador"  # as sessões falam pelo delegador (MCP), seja qual for a CLI de cada papel


@dataclass
class LaunchContext:
    """O que a geração precisa saber do ambiente (obtido por `local_context` e pelo status)."""

    workstreams: set[str] | None = None  # None = não foi possível consultar o AI Memory
    app_server_up: bool | None = None  # None = não verificado
    agent_defs: frozenset[str] = frozenset()  # papéis com .claude/agents/<papel>.md no projeto
    ai_memory_toml: bool = False  # .ai-memory.toml presente na pasta do projeto
    agents_md: str = "integrated"  # absent | integrated | needs_merge (AGENTS.md próprio sem o contrato)
    missing_files: tuple[str, ...] = ()  # arquivos do método que ainda faltam no projeto
    delegator_hooks: bool = (
        True  # `.claude/settings.json` já tem o listener do delegador (só importa no modo delegador)
    )
    own_docs_delegator: bool = True  # método próprio: o AGENTS.md do projeto já aponta para docs/DELEGATOR.md


@dataclass
class LaunchBlock:
    title: str
    commands: list[str]
    prompt: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class LaunchPlan:
    mode: str
    blocks: list[LaunchBlock]
    warnings: list[str]


def local_context(
    project: Project, *, workstreams: set[str] | None = None, app_server_up: bool | None = None
) -> LaunchContext:
    agents_dir = project.path / ".claude" / "agents"
    defs = frozenset(r for r in project.roles if (agents_dir / f"{r}.md").is_file())
    own = project.method == "proprio"  # o projeto já tem o seu método: nada a copiar nem a conferir
    return LaunchContext(
        workstreams=workstreams,
        app_server_up=app_server_up,
        agent_defs=defs,
        ai_memory_toml=(project.path / ".ai-memory.toml").is_file(),
        agents_md="integrated" if own else agents_md_state(project),
        missing_files=tuple(missing_files(project)),  # método próprio: só o contrato do delegador, se ligado
        delegator_hooks=_has_listener(project),
        own_docs_delegator=(not own or not project.delegator or own_docs_cite_delegator(project)),
    )


def _has_listener(project: Project) -> bool:
    try:
        text = (project.path / ".claude" / "settings.json").read_text(encoding="utf-8")
    except OSError:
        return False
    return "delegator listen" in text


def role_title(role_id: str) -> str:
    return ROLE_TITLES.get(role_id, role_id)


def transport_mode(project: Project) -> str:
    if project.delegator:
        return MODE_DELEGATOR
    clis = {r.cli for r in project.roles.values()}
    if clis == {"claude"}:
        return MODE_CLAUDE
    if clis == {"codex"}:
        return MODE_CODEX
    return MODE_MIXED


def _q(parts: list[str]) -> str:
    return " ".join(shlex.quote(p) for p in parts)


def _http_url(ws_url: str) -> str:
    if ws_url.startswith("wss://"):
        return "https://" + ws_url[len("wss://") :]
    if ws_url.startswith("ws://"):
        return "http://" + ws_url[len("ws://") :]
    return ws_url


def _task_source_line(project: Project) -> str:
    if project.task_source.kind == "files":
        roles = ", ".join(project.role_order())
        return (
            f"As tarefas estão em {project.tasks_dir}/ (um arquivo .md por tarefa, com metadados TOML "
            "entre linhas +++). Formato do cabeçalho: id, titulo, "
            f"papel (o ID do papel que executa: {roles}; não o nome da sessão), "
            "estado (draft, ready, delegated, running, asked, done, partial, blocked, failed, needs_review ou "
            "cancelled), criado (AAAA-MM-DD) e, opcionais, modelo e esforco; depois as seções ## Objetivo, "
            "## Critérios de aceite e ## Resultado. Ao concluir, atualize o campo estado e a seção ## Resultado "
            "do arquivo da tarefa."
        )
    board = f" Board: {project.task_source.trello_board}." if project.task_source.trello_board else ""
    return (
        f"O Task Source é o Trello (conector MCP).{board} Cópias locais dos cartões, quando existirem, ficam em "
        f"{project.tasks_dir}/ (campo trello = URL do cartão); o Trello continua sendo a fonte oficial do estado."
    )


def role_file(project: Project, role: str) -> str:
    """Arquivo que descreve o papel: `roles/<papel>.md`; num especialista sem esse arquivo, o perfil do projeto."""
    own = f"roles/{role}.md"
    if (project.path / own).is_file():
        return own
    profile = f".dev-orchestra/perfis/{role}.md"
    return profile if (project.path / profile).is_file() else own


def _reference_line(project: Project) -> str:
    roles = ", ".join(role_file(project, r) for r in project.role_order())
    return (
        "Arquivos de referência do método neste projeto: AGENTS.md; "
        f"{roles}; docs/PROTOCOL.md; docs/MODEL-SELECTION.md; templates/handoff.md; templates/result.md."
    )


def _merge_instruction(project: Project) -> str:
    roles = ", ".join(role_file(project, r) for r in project.role_order())
    return (
        "ATENÇÃO — AGENTS.md: este projeto já tinha um AGENTS.md próprio, que NÃO foi alterado. "
        f"O contrato do Dev Orchestra está em {CONTRACT_MODEL_TARGET}. Antes de iniciar o trabalho, incorpore ao "
        "AGENTS.md existente as regras desse contrato (papéis, fontes de informação, comunicação entre sessões, "
        f"Git e escalonamento humano) e a lista de arquivos de referência ({roles}, docs/PROTOCOL.md, "
        "docs/MODEL-SELECTION.md, templates/handoff.md, templates/result.md), preservando todo o conteúdo atual "
        "e sem remover nem reescrever regras existentes. "
        + (
            "Inclua a regra do modo delegador: docs/DELEGATOR.md tem precedência sobre o transporte entre sessões "
            "(SendMessage, codex queue, RETURN_TO). "
            if project.delegator
            else ""
        )
        + "Mostre o que pretende acrescentar e só grave depois da minha aprovação."
    )


def _own_docs_delegator_instruction(project: Project) -> str:
    refs = ", ".join(["AGENTS.md", *[r for r in project.references if r != "AGENTS.md"]])
    return (
        "ATENÇÃO — documentos do projeto: este projeto passou a se comunicar pelo delegador, mas os documentos dele "
        f"({refs}) ainda descrevem a comunicação antiga (SendMessage, codex queue, RETURN_TO). Proponha acrescentar "
        "ao AGENTS.md uma regra curta: 'a comunicação entre sessões é somente pelo delegador; docs/DELEGATOR.md tem "
        "precedência sobre qualquer trecho que cite SendMessage, codex queue ou RETURN_TO', e marque os trechos "
        "afetados dos outros documentos. Preserve todo o conteúdo atual, mostre o que pretende alterar e só grave "
        "depois da minha aprovação."
    )


def _worker_merge_note(ctx: LaunchContext) -> str:
    if ctx.agents_md != "needs_merge":
        return ""
    return (
        f" O AGENTS.md deste projeto ainda não incorpora o contrato do Dev Orchestra; leia também "
        f"{CONTRACT_MODEL_TARGET}."
    )


def _workers(project: Project) -> list[str]:
    return [r for r in project.role_order() if r != "orchestrator"]


def _identity(project: Project, role: str) -> str:
    name = project.agent_name(role)
    if role == "orchestrator":
        return f'Seu nome de sessão e de workstream é "{name}" (projeto {project.id}).'
    orch = project.agent_name("orchestrator")
    return f'Seu nome de sessão e de workstream é "{name}" (projeto {project.id}); o orquestrador é "{orch}".'


def _own_prompt(project: Project, role: str, ctx: LaunchContext, *, force: bool) -> str | None:
    """Texto de abertura de um projeto importado: aponta os documentos do próprio projeto."""
    cfg_role = project.roles[role]
    if cfg_role.agent and role != "orchestrator" and not force:
        return None  # a definição de agente do projeto já carrega o papel
    refs = ["AGENTS.md", *[r for r in project.references if r != "AGENTS.md"]]
    shown = refs[0] if len(refs) == 1 else ", ".join(refs[:-1]) + " e " + refs[-1]
    title = role_title(role)
    agent = f' (definição de agente: "{cfg_role.agent}")' if cfg_role.agent else ""
    if role == "orchestrator":
        return (
            f"Leia {shown}. {_identity(project, role)}{agent} "
            f"Assuma o papel de {title} conforme esses documentos. {_task_source_line(project)} "
            "Aguarde eu informar a tarefa."
        )
    return (
        f"Leia {shown}. {_identity(project, role)}{agent} "
        f"Assuma o papel de {title} conforme esses documentos e aguarde o handoff do orquestrador."
    )


def _delegator_prompt(project: Project, role: str, ctx: LaunchContext) -> str:
    """Abertura no modo delegador: só o registro; o perfil do papel vem do delegador (`register_session`)."""
    name = project.agent_name(role)
    cli = project.roles[role].cli
    title = role_title(role)
    own = ""
    if project.method == "proprio":
        refs = ["AGENTS.md", *[r for r in project.references if r != "AGENTS.md"]]
        own = f"Leia também {', '.join(refs)} para conhecer o projeto. "
    head = (
        f'Você é "{name}", papel {title} do projeto {project.id}. {own}'
        "Registre-se agora no delegador chamando a ferramenta MCP `register_session` com "
        f'project="{project.id}", role="{role}", cli="{cli}", session_ref="{name}", e siga o perfil devolvido. '
        "A comunicação com outras sessões é SOMENTE pelas ferramentas do delegador (isto vale acima de qualquer "
        "outro documento que cite SendMessage ou codex queue). "
    )
    if role == "orchestrator":
        merge = f"\n\n{_merge_instruction(project)}\n\n" if ctx.agents_md == "needs_merge" else " "
        if not ctx.own_docs_delegator:
            merge = f"\n\n{_own_docs_delegator_instruction(project)}\n\n"
        return f"{head}{_task_source_line(project)}{merge}Aguarde eu informar a tarefa."
    return (
        f"{head}Leia também docs/DELEGATOR.md (comunicação neste projeto). "
        "Depois de registrar, encerre o turno: tarefas chegam como mensagem (chame `receive` quando avisado)."
        f"{_worker_merge_note(ctx)}"
    )


def opening_prompt(
    project: Project, cfg: GlobalConfig, role: str, mode: str, ctx: LaunchContext, *, force: bool = False
) -> str | None:
    """Texto de abertura da sessão nesta fase (ainda sem perfis enviados pelo delegador).

    `force=True` devolve o texto completo mesmo quando a definição de agente já carrega o papel."""
    url = cfg.server_url_for(project)
    title = role_title(role)
    if mode == MODE_DELEGATOR:
        return _delegator_prompt(project, role, ctx)
    if project.method == "proprio":
        return _own_prompt(project, role, ctx, force=force)
    if role == "orchestrator":
        merge = f"{_merge_instruction(project)}\n\n" if ctx.agents_md == "needs_merge" else ""
        if mode == MODE_CODEX:
            return (
                f"{merge}"
                "Leia AGENTS.md, roles/orchestrator.md, docs/PROTOCOL.md e docs/MODEL-SELECTION.md.\n"
                f"{_reference_line(project)}\n"
                f"{_identity(project, role)}\n"
                f"Assuma o papel de Orchestrator. O transporte é codex queue no endpoint {url}. "
                "Antes de cada delegação, recomende MODEL, EFFORT e MODEL_REASON e aguarde minha aprovação. "
                "Após enfileirar o handoff, encerre o turno; o resultado chega como nova mensagem. "
                f"{_task_source_line(project)} Aguarde eu informar a tarefa."
            )
        workers = ", ".join(f'"{project.agent_name(w)}"' for w in _workers(project)) or '"developer"'
        return (
            f"{merge}"
            "Leia AGENTS.md, roles/orchestrator.md, docs/PROTOCOL.md e docs/MODEL-SELECTION.md.\n"
            f"{_reference_line(project)}\n"
            f"{_identity(project, role)}\n"
            "Assuma o papel de Orchestrator no modo Claude Code. "
            f'O transporte é SendMessage para as sessões {workers}; RETURN_TO é "{project.agent_name(role)}". '
            "Não use subagents da ferramenta Agent para executar a tarefa. "
            "Antes de cada delegação, recomende MODEL, EFFORT e MODEL_REASON e peça minha aprovação. "
            "Após entregar o handoff, encerre o turno, sem polling. "
            f"{_task_source_line(project)} Aguarde eu informar a tarefa."
        )
    if mode == MODE_CODEX:
        return (
            f"Leia AGENTS.md, {role_file(project, role)}, docs/PROTOCOL.md e templates/result.md. "
            f"{_identity(project, role)} "
            f"Assuma o papel {title} e aguarde o handoff. "
            "Execute somente o escopo recebido e devolva o resultado autonomamente via "
            f"codex queue --remote {url} ao RETURN_TO.{_worker_merge_note(ctx)}"
        )
    if role in ctx.agent_defs and not force:
        # a definição de agente carrega o papel; o handoff basta (salvo o aviso sobre o AGENTS.md próprio)
        return _worker_merge_note(ctx).strip() or None
    return (
        f"Leia AGENTS.md, {role_file(project, role)}, docs/PROTOCOL.md e templates/result.md. "
        f"{_identity(project, role)} "
        f"Assuma o papel {title} no modo Claude Code "
        "e aguarde o handoff. Execute somente o escopo recebido e devolva o resultado por SendMessage "
        f"ao RETURN_TO.{_worker_merge_note(ctx)}"
    )


def _run_command(
    project: Project, cfg: GlobalConfig, role_id: str, ctx: LaunchContext, warnings: list[str]
) -> list[str]:
    role = project.roles[role_id]
    name = project.agent_name(role_id)
    resume = ctx.workstreams is not None and name in ctx.workstreams
    flag = ["--workstream" if resume else "--new", name]
    proj_flag = [] if ctx.ai_memory_toml else ["--project", project.ai_memory_name()]

    profile_flag: list[str] = []
    if role.subscription:
        sub = cfg.subscriptions.get(role.subscription)
        if sub is None:
            warnings.append(
                f"papel {role_id}: assinatura {role.subscription!r} não está definida no config.toml; "
                "o comando foi gerado sem --profile"
            )
        else:
            if sub.cli != role.cli:
                warnings.append(
                    f"papel {role_id}: a assinatura {role.subscription!r} é de {sub.cli}, mas o papel usa {role.cli}"
                )
            if sub.profile:
                profile_flag = ["--profile", sub.profile]

    adopted = role.cli == "claude" and bool(role.resume)  # sessão existente do Claude escolhida no cadastro
    bootstrap = adopted and not resume  # 1ª subida: o workstream nasce da sessão adotada; depois o ai-memory a retoma
    head = ["ai-memory", "run", *proj_flag, *flag, *profile_flag]
    if project.delegator:
        head = [f"DEV_ORCHESTRA_AGENT={name}", *head]  # o listener do hook descobre quem é a sessão por esta variável
    if role.cli == "claude":
        tail = ["claude"]
        if bootstrap:
            pending_copy = role.resume_fork and not role.resume_origin  # a cópia ainda não foi criada pelo cadastro
            tail += ["--resume", role.resume or "", *(["--fork-session"] if pending_copy else [])]
        tail += ["--name", name]
        if role.agent and not adopted:
            tail += ["--agent", role.agent]
        elif role_id in ctx.agent_defs and project.method == "padrao" and not adopted:
            tail += ["--agent", role_id]
        tail += PERMISSION_FLAGS.get(role.permission or "", [])
        if role.default_model:
            tail += ["--model", role.default_model]
        if role.default_effort:
            tail += ["--effort", role.default_effort]
    else:
        tail = ["codex", *(["resume", role.codex_resume] if role.codex_resume else []), "--remote"]
        tail.append(cfg.server_url_for(project))
        if role.default_model:
            tail += ["--model", role.default_model]
        if role.default_effort:
            tail += ["-c", f'model_reasoning_effort="{role.default_effort}"']
    command = _q([*head, *tail])
    if bootstrap and role.resume_origin:
        warnings.append(
            f"papel {role_id}: sessão adotada em CÓPIA ({role.resume[:8]}, da original {role.resume_origin[:8]}, "
            "que fica intacta). Na primeira subida o comando é `ai-memory run --new … claude --resume <cópia>`: "
            "o workstream passa a apontar para a cópia e as subidas seguintes a retomam"
        )
    elif bootstrap and role.resume_fork:
        warnings.append(
            f"papel {role_id}: a cópia da sessão {role.resume[:8]} ainda não foi criada: salve o cadastro (Editar "
            "cadastro → Salvar) antes de subir. Sem isso o comando usa --fork-session e o ai-memory passaria a "
            "retomar a ORIGINAL nas subidas seguintes"
        )
    elif bootstrap:
        warnings.append(
            f"papel {role_id}: sessão existente adotada SEM cópia ({role.resume[:8]}): o workstream continua a sessão "
            "original e a altera"
        )
    elif adopted:
        warnings.append(
            f"papel {role_id}: a sessão adotada ({role.resume[:8]}) já virou o workstream {name!r}; a subida o retoma"
        )
    return [f"cd {shlex.quote(str(project.path))}", command]


def build_launch_plan(project: Project, cfg: GlobalConfig, ctx: LaunchContext) -> LaunchPlan:
    mode = transport_mode(project)
    warnings: list[str] = []
    blocks: list[LaunchBlock] = []

    if mode == MODE_DELEGATOR:
        if not ctx.delegator_hooks:
            warnings.append(
                f"o projeto está no modo delegador, mas o Claude ainda não tem o listener: rode "
                f"dev-orchestra delegator install {project.id} (e dev-orchestra delegator docker up, se o serviço "
                "ainda não estiver no ar)."
            )
    elif mode == MODE_MIXED and project.method == "padrao":
        warnings.append(
            "Os papéis usam CLIs diferentes. Nesta fase o fluxo entre sessões usa um único transporte "
            "(SendMessage ou codex queue); o transporte misto depende do delegador (fase 2). "
            "Os comandos de subida abaixo funcionam, mas os textos de abertura não cobrem a troca de mensagens."
        )
    if "orchestrator" not in project.roles:
        warnings.append("o projeto não tem o papel orchestrator")
    if ctx.missing_files:
        shown = ", ".join(ctx.missing_files[:3]) + ("…" if len(ctx.missing_files) > 3 else "")
        warnings.append(
            f"faltam {len(ctx.missing_files)} arquivos do método no projeto ({shown}); "
            f"rode: dev-orchestra scaffold {project.id}"
        )
    if ctx.agents_md == "needs_merge":
        warnings.append(
            "o AGENTS.md do projeto não incorpora o contrato do Dev Orchestra; o texto de abertura do orquestrador "
            "pede que ele o incorpore (com a sua aprovação antes de gravar)."
        )
    if ctx.workstreams is None:
        warnings.append(
            "não foi possível consultar os workstreams do AI Memory; foi usado --new em todos os papéis. "
            "Se já existirem, troque por --workstream (confira: "
            f"ai-memory workstreams --project {project.ai_memory_name()})."
        )
    if not ctx.ai_memory_toml:
        warnings.append(
            f"a pasta do projeto não tem .ai-memory.toml; os comandos usam --project {project.ai_memory_name()}."
        )

    if any(r.cli == "codex" for r in project.roles.values()):
        url = cfg.server_url_for(project)
        http = _http_url(url)
        if ctx.app_server_up:
            block = LaunchBlock(
                "App Server do Codex",
                [],
                notes=[f"Já responde em {http}; reutilize (não inicie outro na mesma porta)."],
            )
        else:
            notes = [f"Deixe o processo aberto. Confira: curl -i {http}/readyz e curl -i {http}/healthz (HTTP 200)."]
            if ctx.app_server_up is None:
                notes.insert(0, "Não foi possível verificar se o App Server já está no ar.")
            block = LaunchBlock("App Server do Codex", [f"codex app-server --listen {url}"], notes=notes)
        blocks.append(block)

    for role_id in project.role_order():
        role = project.roles[role_id]
        name = project.agent_name(role_id)
        resume = ctx.workstreams is not None and name in ctx.workstreams
        notes = ["Retomada de workstream existente." if resume else "Primeira criação do workstream."]
        if role.cli == "codex":
            notes.append(
                f"Depois de abrir, use /rename {name} (a TUI também pode nomear a thread) e confira o UUID em /status."
            )
        if role_id != "orchestrator":
            notes.append("Modelo e esforço do cadastro são o padrão; confirme-os a cada tarefa antes do handoff.")
        blocks.append(
            LaunchBlock(
                f"{role_title(role_id)} ({role.cli})",
                _run_command(project, cfg, role_id, ctx, warnings),
                prompt=opening_prompt(project, cfg, role_id, mode, ctx),
                notes=notes,
            )
        )
    return LaunchPlan(mode=mode, blocks=blocks, warnings=warnings)


def format_plan(plan: LaunchPlan) -> str:
    """Renderiza o plano como texto simples: um bloco por terminal, na ordem.

    Comandos e texto de abertura saem sem prefixos (`$`, `|`), para colar direto no terminal."""
    out: list[str] = []
    for warning in plan.warnings:
        out.append(f"AVISO: {warning}")
    if plan.warnings:
        out.append("")
    for n, block in enumerate(plan.blocks, start=1):
        out.append(f"Terminal {n} — {block.title}")
        for note in block.notes:
            out.append(f"  # {note}")
        for command in block.commands:
            out.append(f"  {command}")
        if block.prompt:
            out.append("  Texto de abertura (cole na sessão depois de aberta):")
            out.append("  " + "-" * 60)
            for line in block.prompt.splitlines():
                out.append(f"  {line}")
            out.append("  " + "-" * 60)
        out.append("")
    return "\n".join(out).rstrip() + "\n"
