"""Cadastro e edição de projeto: cartões (projeto, tarefas, papéis) e um resumo ao vivo do que vai acontecer."""

from __future__ import annotations

import re
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from ..cli import CliError, parse_role_spec
from ..core import catalog, config, contract, pathhints, paths, profiles, removal, sessions
from ..core.commands import build_launch_plan, local_context
from ..core.models import CANONICAL_ROLES, GlobalConfig, Project, Role, TaskSource, explain
from . import theme as T
from .fields import (
    CLI_OPTIONS,
    ChoiceChips,
    FolderPicker,
    ModelInput,
    NameScreen,
    PathInput,
    model_picker,
    selected,
    session_picker,
    text_of,
)
from .forms import ConfirmScreen

PERMISSIONS = [("padrão", "none"), ("somente leitura (sem Edit/Write)", "read-only"), ("plan (só planeja)", "plan")]
ROLE_HINT = {
    "orchestrator": "coordena e delega",
    "developer": "implementa",
    "qa": "valida de forma independente",
    "planner": "investiga e planeja",
}


class RoleCard(Vertical):
    """Cartão de um papel: CLI, modelo, esforço, permissões, sessão adotada e perfil. Serve aos fixos e aos especialistas."""

    def __init__(self, role: str, cfg: Role | None = None, *, removable: bool = False) -> None:
        super().__init__(classes="role-card", id=f"rc-{role}")
        self.role, self.cfg, self.removable = role, cfg, removable

    def compose(self) -> ComposeResult:
        role, cfg = self.role, self.cfg
        hint = ROLE_HINT.get(role, "especialista")
        yield Static(
            Text.assemble((f"● {role} ", f"bold {T.C_OWN}"), (hint, T.C_DIM)),
            classes="role-head",
        )
        with Horizontal(classes="role-row"):
            yield Label("CLI", classes="mini")
            yield Select(
                CLI_OPTIONS,
                prompt="— não usar",
                allow_blank=True,
                value=cfg.cli if cfg else Select.NULL,
                id=f"r-{role}-cli",
            )
            yield Label("Modelo", classes="mini")
            yield ModelInput(
                value=(cfg.default_model or "") if cfg else "",
                placeholder="digite ou ▾ para listar",
                id=f"r-{role}-model",
            )
            yield Button("▾", id=f"r-{role}-models", classes="pick")
            yield Label("Assinatura", classes="mini")
            yield Input(value=(cfg.subscription or "") if cfg else "", placeholder="opcional", id=f"r-{role}-sub")
        with Horizontal(classes="role-row role-detail", id=f"rd-{role}"):
            yield Label("Esforço", classes="mini")
            yield ChoiceChips(catalog.CLAUDE_EFFORTS, cfg.default_effort if cfg else None, id=f"r-{role}-effort")
            yield Label("Permissões", classes="mini")
            yield Select(
                PERMISSIONS,
                allow_blank=False,
                value=(cfg.permission if cfg and cfg.permission else "none"),
                id=f"r-{role}-perm",
            )
        with Horizontal(classes="role-row role-detail", id=f"rs-{role}"):
            yield Label("Sessão", classes="mini")
            yield Button("Adotar sessão existente…", id=f"r-{role}-session")
            yield Static("", id=f"r-{role}-session-info", classes="session-info")
            yield Checkbox("usar cópia (fork)", value=cfg.resume_fork if cfg else True, id=f"r-{role}-fork")
        with Horizontal(classes="role-row role-detail", id=f"rp-{role}"):
            yield Label("Perfil", classes="mini")
            yield Button("Criar perfil com IA…", id=f"r-{role}-profile")
            yield Static("", id=f"r-{role}-profile-info", classes="session-info")
            if self.removable:
                yield Button("Remover agente…", id=f"r-{role}-remove", variant="error")


class ProjectFormScreen(ModalScreen["Project | None"]):
    """Cadastro e edição de projeto."""

    BINDINGS = [("escape", "cancel", "Cancelar"), ("ctrl+s", "save", "Salvar")]

    def __init__(self, project: Project | None = None) -> None:
        super().__init__()
        self.editing = project
        extras = sorted(r for r in (project.roles if project else {}) if r not in CANONICAL_ROLES)
        self.role_ids: list[str] = [*CANONICAL_ROLES, *extras]
        saved = project.roles if project else {}
        # `_resume` = a sessão ORIGINAL escolhida; `_copy` = a cópia que a ferramenta já criou dela (se houver)
        self._resume: dict[str, str | None] = {
            r: ((saved[r].resume_origin or saved[r].resume) if r in saved else None) for r in self.role_ids
        }
        self._copy: dict[str, str | None] = {
            r: (saved[r].resume if r in saved and saved[r].resume_origin else None) for r in self.role_ids
        }
        self._session_titles: dict[str, str] = {}

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        p = self.editing
        with Vertical(id="pf"):
            yield Label(
                ("Cadastro de projeto" if p is None else f"Editar projeto {p.id}")
                + "   (Ctrl+S salva · Tab próximo campo · Ctrl+O escolhe a pasta · F2 lista os modelos · Esc cancela)",
                classes="title",
            )
            with Horizontal(id="pf-cols"):
                with VerticalScroll(id="pf-left"):
                    with Vertical(classes="card", id="card-project"):
                        yield Label("① Projeto", classes="card-title")
                        yield Label("Id (minúsculas, números, - ou _)")
                        yield Input(value=p.id if p else "", id="f-id", disabled=p is not None)
                        yield Label("Nome")
                        yield Input(value=p.name if p else "", id="f-name")
                        yield Label("Prefixo dos agentes ([prefixo]-[papel]; vazio = o id do projeto)", markup=False)
                        yield Input(value=(p.prefix or "") if p else "", id="f-prefix")
                        yield Label("Pasta do projeto (Tab completa · Ctrl+O navega)")
                        yield PathInput(
                            value=str(p.path) if p else "", placeholder="~/projetos/pessoal/meu-projeto", id="f-path"
                        )
                        yield Static("", id="f-folder")
                    with Vertical(classes="card", id="card-source"):
                        yield Label("② Tarefas e comunicação", classes="card-title")
                        yield Select([("Arquivos locais", "files"), ("Trello", "trello")], allow_blank=False,
                                     value=p.task_source.kind if p else "files", id="f-source")  # fmt: skip
                        yield Label("Board do Trello (opcional)")
                        yield Input(value=(p.task_source.trello_board or "") if p else "", id="f-trello")
                        yield Checkbox(
                            "Comunicar pelo delegador (MCP): permissões por papel e registro de tudo",
                            value=bool(p.delegator) if p else True,  # padrão do método: comunicação pelo delegador
                            id="f-delegator",
                        )
                    with Vertical(classes="card", id="card-roles"):
                        yield Label("③ Papéis (CLI em branco = papel não usado)", classes="card-title")
                        for role in CANONICAL_ROLES:
                            yield RoleCard(role, p.roles.get(role) if p else None)
                        yield Label("Especialistas (papéis próprios do projeto, ex.: analyst)", classes="card-title")
                        with Vertical(id="specialists"):
                            for role in self.role_ids[len(CANONICAL_ROLES) :]:
                                yield RoleCard(role, p.roles.get(role) if p else None, removable=True)
                        yield Button("＋ Adicionar papel especialista", id="add-specialist")
                yield Static("", id="pf-summary")
            yield Static("", id="f-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Salvar", id="save", variant="primary")
                yield Button("Cancelar", id="cancel")

    # ----------------------------------------------------------- ciclo

    def on_mount(self) -> None:
        for role in self.role_ids:
            self._sync_role(role)
        self._refresh_folder()
        self._refresh_summary()
        self.query_one("#f-name" if self.editing else "#f-id", Input).focus()

    def _cli_of(self, role: str) -> str | None:
        return selected(self.query_one(f"#r-{role}-cli", Select))

    def _catalog(self, cli: str | None) -> catalog.Catalog:
        return catalog.catalog_for(cli or "claude")

    def _sync_role(self, role: str) -> None:
        """Mostra os detalhes do papel só se ele tem CLI e ajusta os esforços ao modelo e à CLI."""
        cli = self._cli_of(role)
        for prefix in ("rd", "rs", "rp"):
            self.query_one(f"#{prefix}-{role}").display = cli is not None and (
                prefix in ("rd", "rp") or cli == "claude"
            )
        chips = self.query_one(f"#r-{role}-effort", ChoiceChips)
        model = text_of(self.query_one(f"#r-{role}-model", Input))
        chips.set_options(self._catalog(cli).efforts_for(model) if cli else [])
        self._profile_info(role)
        info = self.query_one(f"#r-{role}-session-info", Static)
        rid = self._resume.get(role)
        copy = self._copy.get(role)
        if rid and copy:
            label = f"cópia {copy[:8]} da original {rid[:8]}"
        elif rid:
            label = f"{self._session_titles.get(role, '')} ({rid[:8]}) · a cópia é criada ao salvar".strip()
        else:
            label = "nenhuma (abre uma sessão nova)"
        info.update(Text(label, style=T.C_OK if rid else T.C_DIM))

    def _profile_info(self, role: str) -> None:
        raw = text_of(self.query_one("#f-path", Input)) or ""
        saved = bool(raw) and (Path(raw).expanduser() / profiles.PROFILES_DIR / f"{role}.md").is_file()
        self.query_one(f"#r-{role}-profile-info", Static).update(
            Text("perfil próprio ✓" if saved else "perfil padrão do papel", style=T.C_OK if saved else T.C_DIM)
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        wid = event.select.id or ""
        if wid.startswith("r-") and wid.endswith("-cli"):
            self._sync_role(wid[2:-4])
        self._refresh_summary()

    def on_input_changed(self, event: Input.Changed) -> None:
        wid = event.input.id or ""
        if wid.startswith("r-") and wid.endswith("-model"):
            role = wid[2:-6]
            chips = self.query_one(f"#r-{role}-effort", ChoiceChips)
            if self._cli_of(role):
                chips.set_options(self._catalog(self._cli_of(role)).efforts_for(event.value.strip() or None))
        if wid == "f-path":
            self._refresh_folder()
            for role in self.role_ids:
                self._profile_info(role)
        self._refresh_summary()

    def on_choice_chips_changed(self, event: ChoiceChips.Changed) -> None:
        self._refresh_summary()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._refresh_summary()

    def on_model_input_catalog(self, message: ModelInput.Catalog) -> None:
        role = (message.input.id or "")[2:-6]
        cli = self._cli_of(role)
        if cli is None:
            self.notify("Escolha a CLI do papel primeiro.", markup=False)
            return

        def done(slug: str | None) -> None:
            if slug:
                message.input.value = slug

        self.app.push_screen(model_picker(self._catalog(cli), text_of(message.input)), done)

    def on_path_input_browse(self, message: PathInput.Browse) -> None:
        def done(chosen: str | None) -> None:
            if chosen:
                message.input.value = chosen
                message.input.cursor_position = len(chosen)

        self.app.push_screen(FolderPicker(message.input.value), done)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id or ""
        if wid == "cancel":
            self.dismiss(None)
        elif wid == "save":
            self._save()
        elif wid.startswith("r-") and wid.endswith("-models"):
            self.query_one(f"#r-{wid[2:-7]}-model", ModelInput).action_catalog()
        elif wid.startswith("r-") and wid.endswith("-session"):
            self._adopt(wid[2:-8])
        elif wid.startswith("r-") and wid.endswith("-profile"):
            self._open_profile(wid[2:-8])
        elif wid.startswith("r-") and wid.endswith("-remove"):
            self.remove_specialist(wid[2:-7])
        elif wid == "add-specialist":
            self._ask_specialist()

    # ------------------------------------------------------- especialistas e perfil

    def _ask_specialist(self) -> None:
        def done(name: str | None) -> None:
            if not name:
                return
            role = name.strip().lower().replace(" ", "-")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", role):
                self.notify("Nome inválido: use minúsculas, números, - ou _.", severity="warning", markup=False)
            elif role in self.role_ids:
                self.notify(f"O papel {role} já existe neste projeto.", severity="warning", markup=False)
            else:
                self.run_worker(self.add_specialist(role), exclusive=False)

        self.app.push_screen(
            NameScreen("Novo papel especialista", "Id do papel (minúsculas, números, - ou _). Ex.: analyst"), done
        )

    async def add_specialist(self, role: str, cfg: Role | None = None) -> None:
        """Acrescenta o cartão de um papel próprio (já com Claude escolhido para poupar um passo)."""
        cfg = cfg or Role(cli="claude")
        self.role_ids.append(role)
        self._resume[role] = cfg.resume_origin or cfg.resume
        self._copy[role] = cfg.resume if cfg.resume_origin else None
        await self.query_one("#specialists", Vertical).mount(RoleCard(role, cfg, removable=True))
        self._sync_role(role)
        self._refresh_summary()
        self.query_one(f"#r-{role}-model", Input).focus()

    def remove_specialist(self, role: str, *, confirmed_plan=None) -> None:
        """Tira o cartão do papel. Se o papel já está no cadastro, mostra o plano e apaga o que foi gerado para ele."""
        if role not in self.role_ids or role in CANONICAL_ROLES:
            return
        saved = self.editing is not None and role in self.editing.roles
        if not saved:  # ainda não foi salvo: só sai do formulário
            self._drop_card(role)
            return
        live = False
        report = getattr(self.app, "last_status_report", None)
        if report is not None:
            live = any(r.role == role and r.state in ("ociosa", "ocupada") for r in report.roles)
        plan = removal.plan_removal(self.editing, role, live_session=live)
        if plan.blocked:
            self.notify(plan.blocked, title="Agente não removido", severity="warning", markup=False)
            return

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                lines = removal.execute(plan)
            except (ValueError, OSError, config.ConfigError) as exc:
                self.notify(str(exc), severity="error", markup=False)
                return
            self.editing = config.load_project(plan.project.id)
            self._drop_card(role)
            self.notify("\n".join(lines), title=f"Agente {plan.agent} removido", timeout=12, markup=False)

        self.app.push_screen(ConfirmScreen(plan.text()), done)

    def _drop_card(self, role: str) -> None:
        if role in self.role_ids:
            self.role_ids.remove(role)
        self._resume.pop(role, None)
        self._copy.pop(role, None)
        self.query_one(f"#rc-{role}", RoleCard).remove()
        self._refresh_summary()

    def _open_profile(self, role: str) -> None:
        try:
            project = self._build()
        except (ValueError, CliError, TypeError) as exc:
            self.notify(f"Preencha o projeto antes: {explain(exc)}", severity="warning", markup=False)
            return
        if not project.path.is_dir():
            self.notify("Informe a pasta do projeto antes de criar o perfil.", severity="warning", markup=False)
            return
        if not project.delegator:
            self.notify(
                "O perfil só chega ao agente pelo delegador: marque 'Comunicar pelo delegador' para usá-lo.",
                severity="warning",
                markup=False,
            )
        from .profile_form import ProfileScreen

        self.app.push_screen(ProfileScreen(project, role), lambda _saved: self._sync_role(role))

    def _adopt(self, role: str) -> None:
        folder = Path(text_of(self.query_one("#f-path", Input)) or "").expanduser()
        if not folder.is_dir():
            self.notify("Informe a pasta do projeto antes de adotar uma sessão.", markup=False)
            return
        found = sessions.list_sessions(folder, getattr(self.app, "services", None))
        if not found:
            self.notify("Nenhuma sessão do Claude encontrada para esta pasta.", markup=False)
            return
        by_id = {s.id: s for s in found}

        def done(chosen: str | None) -> None:
            if not chosen:
                return
            info = by_id[chosen]
            if info.open_pid:
                self.notify(
                    f"A sessão está aberta (pid {info.open_pid}): feche-a antes de adotar.",
                    severity="warning",
                    markup=False,
                )
                return
            self._resume[role] = chosen
            self._copy[role] = None  # outra sessão escolhida: a cópia antiga não vale mais
            self._session_titles[role] = info.title
            self._sync_role(role)
            self._refresh_summary()

        self.app.push_screen(session_picker(found), done)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self._save()

    # ------------------------------------------------------------ resumo

    def _refresh_folder(self) -> None:
        raw = text_of(self.query_one("#f-path", Input)) or ""
        t = Text()
        if raw:
            info = pathhints.describe_folder(Path(raw).expanduser())
            if not info.get("exists"):
                t.append("✗ a pasta não existe", style=T.C_OFF)
            else:
                t.append("✓ existe", style=T.C_OK)
                if info.get("git"):
                    extra = (
                        f" ({info.get('branch', '?')} · {info.get('modified', 0)} modificados)"
                        if info.get("branch")
                        else ""
                    )
                    t.append(f"   ✓ Git{extra}", style=T.C_OK)
                if info.get("agents_md"):
                    t.append("   ✓ AGENTS.md próprio", style=T.C_OK)
                t.append(
                    "   ✓ .ai-memory.toml" if info.get("ai_memory_toml") else "   ○ .ai-memory.toml",
                    style=T.C_OK if info.get("ai_memory_toml") else T.C_MUTED,
                )
        self.query_one("#f-folder", Static).update(t)

    def _refresh_summary(self) -> None:
        panel = self.query_one("#pf-summary", Static)
        panel.border_title = Text(" Resumo ao vivo ", style=f"bold {T.C_STRONG}")
        try:
            project = self._build()
        except (ValueError, CliError, TypeError) as exc:
            panel.update(Text(f"Preencha os campos obrigatórios.\n\n{explain(exc)}", style=T.C_MUTED))
            return
        t = Text()
        t.append("Agentes\n", style=f"bold {T.C_NEW}")
        for rid in project.role_order():
            r = project.roles[rid]
            parts = [r.cli, r.default_model or "modelo padrão", r.default_effort or "esforço padrão"]
            t.append("  ○ ", style=T.C_UNK)
            t.append(project.agent_name(rid), style=T.C_STRONG)
            t.append("  " + " · ".join(parts) + ("\n" if not r.resume else "  "), style=T.C_MUTED)
            if r.resume:
                label = (
                    f"sessão {(r.resume_origin or r.resume)[:8]}"
                    + (" (cópia criada ao salvar)" if r.resume_fork and not r.resume_origin else "")
                    + (f" → cópia {r.resume[:8]}" if r.resume_origin else "")
                )
                t.append(label + "\n", style=T.C_OWN)
        bad = []
        for rid in project.role_order():
            r = project.roles[rid]
            known = catalog.catalog_for(r.cli).slugs()
            if r.default_model and known and r.default_model not in known:
                bad.append(f"{rid}: modelo {r.default_model!r} não está no catálogo do {r.cli} (use ▾)")
        if bad:
            t.append("\nAvisos\n", style=f"bold {T.C_BUSY}")
            for line in bad:
                t.append(f"  ▲ {line}\n", style=T.C_BUSY)
        t.append("\nSerá criado na pasta (nada é sobrescrito)\n", style=f"bold {T.C_NEW}")
        if project.path.is_dir():
            report = contract.scaffold(project, dry_run=True)
            groups: dict[str, int] = {}
            for file in report.created:
                groups[file.split("/")[0] if "/" in file else file] = (
                    groups.get(file.split("/")[0] if "/" in file else file, 0) + 1
                )
            for folder, count in groups.items():
                t.append(
                    f"  + {folder}{'/ (' + str(count) + ')' if '/' in ''.join(f for f in report.created if f.startswith(folder + '/')) else ''}\n",
                    style=T.C_TEXT,
                )
            if report.kept:
                t.append(f"  = já existem e ficam como estão: {', '.join(report.kept[:4])}\n", style=T.C_MUTED)
            if report.needs_merge:
                t.append(
                    "  = AGENTS.md próprio: mantido; o orquestrador propõe a\n    incorporação do contrato, com a sua aprovação\n",
                    style=T.C_BUSY,
                )
            plan = build_launch_plan(project, GlobalConfig(), local_context(project, workstreams=set()))
            t.append("\nSubida (resumo)\n", style=f"bold {T.C_NEW}")
            for block in plan.blocks:
                if block.commands:
                    cmd = block.commands[-1]
                    t.append(f"  {block.title.split()[0]:<13}", style=T.C_MUTED)
                    t.append(cmd[:70] + ("…" if len(cmd) > 70 else "") + "\n", style=T.C_DIM)
            warns = [w for w in plan.warnings if "sessão existente" in w]
            for w in warns:
                t.append(f"  ▲ {w}\n", style=T.C_BUSY)
        else:
            t.append("  (informe uma pasta existente)\n", style=T.C_DIM)
        panel.update(t)

    # ---------------------------------------------------------- salvar

    def _error(self, message: str) -> None:
        self.query_one("#f-error", Static).update(Text(message))

    def _build(self) -> Project:
        roles = {}
        for role in self.role_ids:
            cli = self._cli_of(role)
            if cli is None:
                continue
            spec = f"{role}={cli}"
            for key, value in (
                ("model", text_of(self.query_one(f"#r-{role}-model", Input))),
                ("effort", self.query_one(f"#r-{role}-effort", ChoiceChips).value),
                ("subscription", text_of(self.query_one(f"#r-{role}-sub", Input))),
            ):
                if value:
                    spec += f",{key}={value}"
            rid, cfg = parse_role_spec(spec)
            perm = selected(self.query_one(f"#r-{role}-perm", Select))
            cfg.permission = perm if perm in ("read-only", "plan") else None
            if cli == "claude" and self._resume.get(role):
                fork = self.query_one(f"#r-{role}-fork", Checkbox).value
                cfg.resume_fork = fork
                if fork and self._copy.get(role):  # a cópia desta sessão já existe: só a reaproveita
                    cfg.resume, cfg.resume_origin = self._copy[role], self._resume[role]
                else:
                    cfg.resume = self._resume[role]
            roles[rid] = cfg
        if self.editing:  # preserva o que o formulário não mostra (nome explícito, agente, retomada do Codex)
            for rid, cfg in roles.items():
                old = self.editing.roles.get(rid)
                if old:
                    cfg.name, cfg.agent, cfg.codex_resume = old.name, old.agent, old.codex_resume
        project_id = self.editing.id if self.editing else (text_of(self.query_one("#f-id", Input)) or "")
        raw_path = text_of(self.query_one("#f-path", Input)) or ""
        return Project(
            id=project_id,
            name=text_of(self.query_one("#f-name", Input)) or project_id,
            path=Path(raw_path).expanduser(),
            task_source=TaskSource(
                kind=selected(self.query_one("#f-source", Select)) or "files",
                trello_board=text_of(self.query_one("#f-trello", Input)),
                trello_lists=self.editing.task_source.trello_lists if self.editing else {},
            ),
            roles=roles,
            app_server_url=self.editing.app_server_url if self.editing else None,
            ai_memory_project=self.editing.ai_memory_project if self.editing else None,
            ai_memory_workspace=self.editing.ai_memory_workspace if self.editing else "default",
            prefix=text_of(self.query_one("#f-prefix", Input)),
            method=self.editing.method if self.editing else "padrao",
            references=self.editing.references if self.editing else [],
            delegator=self.query_one("#f-delegator", Checkbox).value,
        )

    def _save(self) -> None:
        try:
            project = self._build()
            if not project.path.is_dir():
                raise ValueError(f"a pasta do projeto não existe: {project.path}")
            if self.editing is None and paths.project_file(project.id).exists():
                raise ValueError(f"o projeto {project.id!r} já está cadastrado")
            copies = sessions.materialize_forks(project)  # cria as cópias de sessão pedidas (a original fica intacta)
            config.save_project(project)
            project.tasks_path.mkdir(parents=True, exist_ok=True)
            report = contract.scaffold(project)
        except (ValueError, CliError, OSError, config.ConfigError, sessions.SessionError) as exc:
            self._error(f"Erro: {explain(exc)}")
            return
        if copies:
            self.app.notify("\n".join(copies), title="Sessões copiadas", timeout=12, markup=False)
        self.app.notify(contract.format_report(report), title="Arquivos do método", timeout=12, markup=False)
        self.dismiss(project)
