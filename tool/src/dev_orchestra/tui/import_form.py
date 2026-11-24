"""Importar um projeto que já existe: leitura da pasta (com completar) e confirmação em cartões."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from ..core import catalog, config, importer, pathhints, paths, sessions
from ..core.models import CANONICAL_ROLES, Role, TaskSource, explain
from . import theme as T
from .fields import (
    CLI_OPTIONS,
    ChoiceChips,
    FolderPicker,
    ModelInput,
    PathInput,
    model_picker,
    selected,
    session_picker,
    text_of,
)

TRELLO_HELP = "Listas do Trello → estado da tarefa local (Lista=estado; Lista2=estado2)"


class ImportPathScreen(ModalScreen["importer.ScanResult | None"]):
    """Pede a pasta do projeto existente (com completar) e a lê, somente leitura."""

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def compose(self) -> ComposeResult:
        with Vertical(id="pf-small"):
            yield Label("Importar projeto existente", classes="title")
            yield Label("Pasta do projeto. A ferramenta só lê a pasta e o AI Memory (Tab completa · Ctrl+O navega).")
            yield PathInput(placeholder="~/projetos/pessoal/meu-projeto", id="i-path")
            yield Static("", id="i-preview")
            yield Static("", id="i-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Ler pasta", id="read", variant="primary")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#i-path", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "i-path":
            return
        info = pathhints.describe_folder(Path(event.value).expanduser()) if event.value.strip() else {}
        t = Text()
        if info.get("exists"):
            t.append("✓ existe", style=T.C_OK)
            for key, label in (("git", "Git"), ("agents_md", "AGENTS.md"), ("claude_dir", ".claude/")):
                if info.get(key):
                    t.append(f"   ✓ {label}", style=T.C_OK)
            t.append(
                "   ✓ .ai-memory.toml" if info.get("ai_memory_toml") else "   ○ .ai-memory.toml",
                style=T.C_OK if info.get("ai_memory_toml") else T.C_MUTED,
            )
        elif event.value.strip():
            t.append("✗ a pasta não existe", style=T.C_OFF)
        self.query_one("#i-preview", Static).update(t)

    def on_path_input_browse(self, message: PathInput.Browse) -> None:
        def done(chosen: str | None) -> None:
            if chosen:
                message.input.value = chosen
                message.input.cursor_position = len(chosen)

        self.app.push_screen(FolderPicker(message.input.value), done)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "i-path":
            self._read()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._read()

    def _read(self) -> None:
        raw = text_of(self.query_one("#i-path", Input)) or ""
        path = Path(raw).expanduser()
        if not path.is_dir():
            self.query_one("#i-error", Static).update(Text(f"A pasta não existe: {path}"))
            return
        services = getattr(self.app, "services", None)
        reader = (getattr(services, "scan_project", None) if services else None) or importer.scan
        try:
            result = reader(path)
        except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura vira mensagem
            self.query_one("#i-error", Static).update(Text(f"Falha ao ler a pasta: {exc}"))
            return
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ImportFormScreen(ModalScreen["Project | None"]):
    """Confirma o que foi lido e importa, preservando nomes e agentes existentes (nada é copiado)."""

    BINDINGS = [("escape", "cancel", "Cancelar"), ("ctrl+s", "save", "Importar")]

    def __init__(self, scan_result: importer.ScanResult) -> None:
        super().__init__()
        self.scan = scan_result
        self.suggestions = importer.suggest_roles(scan_result)
        self._resume: dict[str, str | None] = dict.fromkeys(CANONICAL_ROLES)
        self._session_titles: dict[str, str] = {}

    # ------------------------------------------------------------ o que foi lido

    def _read_text(self) -> Text:
        r = self.scan
        t = Text()

        def row(label: str, value: str, style: str = T.C_TEXT) -> None:
            t.append(f"{label:<13}", style=T.C_MUTED)
            t.append(value + "\n", style=style)

        row("Pasta", str(r.path), T.C_STRONG)
        if r.ai_memory_project:
            existe = {True: "existe", False: "ainda não existe", None: "não consultado"}[r.project_exists]
            n = len(r.workstreams or [])
            row(
                "AI Memory",
                f"projeto {r.ai_memory_project} ({existe}) · {n} workstream(s)",
                T.C_OK if r.project_exists else T.C_NEW,
            )
        else:
            row("AI Memory", "sem .ai-memory.toml (projeto não identificado)", T.C_MUTED)
        row(
            "Método",
            ("AGENTS.md" if r.agents_md else "sem AGENTS.md")
            + (f" + {', '.join(r.references[1:])}" if len(r.references) > 1 else "")
            + " (preservados)",
        )
        row("Agentes", ", ".join(f"{d.name}" for d in r.agent_defs) or "nenhuma definição em .claude/agents")
        row(
            "Trello",
            f"board “{r.trello_hint}” detectado" if r.trello_hint else "nenhuma referência encontrada",
            T.C_TRELLO if r.trello_hint else T.C_MUTED,
        )
        t.rstrip()
        return t

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        r = self.scan
        ws_options = [(f"{w.name}  ({w.last_used}; {w.harnesses})", w.name) for w in importer.candidate_workstreams(r)]
        agent_options = [(d.name, d.name) for d in r.agent_defs]
        with Vertical(id="pf"):
            yield Label(
                "Importar projeto existente   (Ctrl+S importa · F2 lista os modelos · Esc cancela)", classes="title"
            )
            with Horizontal(id="pf-cols"):
                with VerticalScroll(id="pf-left"):
                    with Vertical(classes="card"):
                        yield Label("① O que foi lido (somente leitura)", classes="card-title")
                        yield Static(self._read_text(), id="i-summary")
                    with Vertical(classes="card"):
                        yield Label("② Projeto", classes="card-title")
                        yield Label("Id do cadastro (minúsculas, números, - ou _)")
                        yield Input(value=r.suggested_id(), id="i-id")
                        yield Label("Nome")
                        yield Input(value=r.path.name, id="i-name")
                        yield Label("Documentos do método que as instruções citam (separados por vírgula)")
                        yield Input(value=", ".join(r.references), id="i-refs")
                        yield Checkbox(
                            "Comunicar pelo delegador (MCP) — padrão; desmarque só para usar SendMessage / codex queue",
                            value=True,
                            id="i-delegator",
                        )
                    with Vertical(classes="card"):
                        yield Label("③ Papéis ← agentes que já existem (vazio = não usar)", classes="card-title")
                        for role in CANONICAL_ROLES:
                            yield from self._role_card(role, ws_options, agent_options)
                    with Vertical(classes="card"):
                        yield Label("④ Fonte de tarefas", classes="card-title")
                        yield Select([("Arquivos locais", "files"), ("Trello (o orquestrador copia os cartões para texto)", "trello")],
                                     allow_blank=False, value="trello" if r.trello_hint else "files", id="i-source")  # fmt: skip
                        yield Label("Board do Trello (nome ou URL)")
                        yield Input(value=r.trello_hint or "", id="i-board")
                        yield Label(TRELLO_HELP)
                        yield Input(value=importer.format_lists(importer.DEFAULT_TRELLO_LISTS), id="i-lists")
                yield Static(self._side_text(), id="pf-summary")
            yield Static("", id="i-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Importar", id="save", variant="primary")
                yield Button("Cancelar", id="cancel")

    def _role_card(self, role: str, ws_options, agent_options) -> ComposeResult:
        sug = self.suggestions[role]
        with Vertical(classes="role-card"):
            yield Static(Text.assemble((f"● {role}", f"bold {T.C_OWN}")), classes="role-head")
            with Horizontal(classes="role-row"):
                yield Label("Workstream", classes="mini")
                yield Select(
                    ws_options,
                    prompt="(não usar)",
                    allow_blank=True,
                    value=sug.workstream if sug.workstream else Select.NULL,
                    id=f"i-{role}-ws",
                )
                yield Label("CLI", classes="mini")
                yield Select(CLI_OPTIONS, allow_blank=False, value=sug.cli, id=f"i-{role}-cli")
                yield Label("--agent", classes="mini")
                yield Select(
                    agent_options,
                    prompt="(sem --agent)",
                    allow_blank=True,
                    value=sug.agent if sug.agent else Select.NULL,
                    id=f"i-{role}-agent",
                )
            with Horizontal(classes="role-row"):
                yield Label("Modelo", classes="mini")
                yield ModelInput(placeholder="como a sessão; ▾ lista", id=f"i-{role}-model")
                yield Button("▾", id=f"i-{role}-models", classes="pick")
                yield Label("Esforço", classes="mini")
                yield ChoiceChips(catalog.CLAUDE_EFFORTS, None, id=f"i-{role}-effort")
            with Horizontal(classes="role-row"):
                yield Label("Sessão", classes="mini")
                yield Button("Adotar sessão sem workstream…", id=f"i-{role}-session")
                yield Static(Text("nenhuma", style=T.C_DIM), id=f"i-{role}-session-info", classes="session-info")
                yield Input(placeholder="UUID da thread do Codex (opcional)", id=f"i-{role}-uuid")

    def _side_text(self) -> Text:
        r = self.scan
        t = Text()
        t.append("Faz\n", style=f"bold {T.C_OK}")
        for line in ("guarda o cadastro (nomes dos agentes preservados)", "cria .dev-orchestra/tarefas/ (vazia)",
                     "depois: pede ao orquestrador que traga os cartões do Trello para arquivos (só leitura)"):  # fmt: skip
            t.append(f"  ✓ {line}\n", style=T.C_TEXT)
        t.append("\nNão faz\n", style=f"bold {T.C_OFF}")
        for line in (
            "copiar arquivos do método (o projeto tem o seu)",
            "alterar AGENTS.md, docs/ ou .claude/",
            "criar ou apagar workstreams no AI Memory",
        ):
            t.append(f"  ✗ {line}\n", style=T.C_TEXT)
        warns = []
        if r.project_exists is False:
            warns.append("o projeto ainda não existe no AI Memory (será criado na primeira sessão)")
        if not r.agent_defs:
            warns.append("nenhuma definição de agente do Claude em .claude/agents")
        if not r.workstreams:
            warns.append("nenhum workstream encontrado: use a adoção de sessão ou o UUID do Codex")
        if warns:
            t.append("\nAvisos\n", style=f"bold {T.C_BUSY}")
            for w in warns:
                t.append(f"  ▲ {w}\n", style=T.C_BUSY)
        t.append("\nModelo e esforço em branco = os que a sessão já usa.", style=T.C_DIM)
        return t

    # ----------------------------------------------------------- ciclo

    def on_select_changed(self, event: Select.Changed) -> None:
        wid = event.select.id or ""
        if wid.startswith("i-") and wid.endswith("-cli"):
            role = wid[2:-4]
            model = text_of(self.query_one(f"#i-{role}-model", Input))
            self.query_one(f"#i-{role}-effort", ChoiceChips).set_options(
                catalog.catalog_for(event.value if isinstance(event.value, str) else "claude").efforts_for(model)
            )

    def on_model_input_catalog(self, message: ModelInput.Catalog) -> None:
        role = (message.input.id or "")[2:-6]
        cli = selected(self.query_one(f"#i-{role}-cli", Select)) or "claude"

        def done(slug: str | None) -> None:
            if slug:
                message.input.value = slug

        self.app.push_screen(model_picker(catalog.catalog_for(cli), text_of(message.input)), done)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id or ""
        if wid == "cancel":
            self.dismiss(None)
        elif wid == "save":
            self._save()
        elif wid.startswith("i-") and wid.endswith("-models"):
            self.query_one(f"#i-{wid[2:-7]}-model", ModelInput).action_catalog()
        elif wid.startswith("i-") and wid.endswith("-session"):
            self._adopt(wid[2:-8])

    def _adopt(self, role: str) -> None:
        found = sessions.list_sessions(self.scan.path, getattr(self.app, "services", None))
        if not found:
            self.notify("Nenhuma sessão do Claude encontrada para esta pasta.", markup=False)
            return
        by_id = {s.id: s for s in found}

        def done(chosen: str | None) -> None:
            if not chosen:
                return
            if by_id[chosen].open_pid:
                self.notify(
                    f"A sessão está aberta (pid {by_id[chosen].open_pid}): feche-a antes de adotar.",
                    severity="warning",
                    markup=False,
                )
                return
            self._resume[role] = chosen
            self._session_titles[role] = by_id[chosen].title
            self.query_one(f"#i-{role}-session-info", Static).update(
                Text(f"{by_id[chosen].title} ({chosen[:8]}) · cópia", style=T.C_OK)
            )

        self.app.push_screen(session_picker(found), done)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self._save()

    def _error(self, message: str) -> None:
        self.query_one("#i-error", Static).update(Text(message))

    def _save(self) -> None:
        try:
            roles: dict[str, Role] = {}
            for role in CANONICAL_ROLES:
                workstream = selected(self.query_one(f"#i-{role}-ws", Select))
                resume = self._resume.get(role)
                if not workstream and not resume:
                    continue
                roles[role] = Role(
                    cli=selected(self.query_one(f"#i-{role}-cli", Select)) or "claude",
                    name=workstream,
                    agent=selected(self.query_one(f"#i-{role}-agent", Select)),
                    default_model=text_of(self.query_one(f"#i-{role}-model", Input)),
                    default_effort=self.query_one(f"#i-{role}-effort", ChoiceChips).value,
                    codex_resume=text_of(self.query_one(f"#i-{role}-uuid", Input)),
                    resume=resume,
                    resume_fork=True,
                )
            if not roles:
                raise ValueError("escolha o agente existente de ao menos um papel")
            kind = selected(self.query_one("#i-source", Select)) or "files"
            board = text_of(self.query_one("#i-board", Input))
            source = TaskSource(
                kind=kind,
                trello_board=board if kind == "trello" else None,
                trello_lists=importer.parse_lists(self.query_one("#i-lists", Input).value) if kind == "trello" else {},
            )
            refs = [x.strip() for x in self.query_one("#i-refs", Input).value.split(",") if x.strip()]
            project = importer.build_project(
                self.scan,
                project_id=text_of(self.query_one("#i-id", Input)) or "",
                name=text_of(self.query_one("#i-name", Input)) or "",
                roles=roles,
                task_source=source,
                references=refs,
            )
            project.delegator = self.query_one("#i-delegator", Checkbox).value
            if paths.project_file(project.id).exists():
                raise ValueError(f"o projeto {project.id!r} já está cadastrado")
            sessions.materialize_forks(project)  # cópias das sessões adotadas (a original fica intacta)
            config.save_project(project)
            project.tasks_path.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError, config.ConfigError, sessions.SessionError) as exc:
            self._error(f"Erro: {explain(exc)}")
            return
        self.app.notify(
            f"Projeto {project.id} importado. Nomes e agentes preservados; nenhum arquivo foi copiado para o projeto.",
            title="Importação concluída",
            timeout=12,
            markup=False,
        )
        self.dismiss(project)
