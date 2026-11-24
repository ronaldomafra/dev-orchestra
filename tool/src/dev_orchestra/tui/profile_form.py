"""Novo perfil de agente: descreve o que o papel deve fazer, uma IA escreve o rascunho, a pessoa revisa e aprova."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from ..core import config, generator, profiles
from ..core.models import Project, explain
from . import theme as T
from .fields import selected, text_of

READ_ONLY_ROLES = {"analyst"}


class ProfileScreen(ModalScreen["str | None"]):
    """Gera, revisa e salva o perfil de um papel. Devolve o papel salvo ou None."""

    BINDINGS = [("escape", "cancel", "Cancelar"), ("ctrl+g", "generate", "Gerar"), ("ctrl+s", "save", "Salvar")]

    def __init__(self, project: Project, role: str, generate=None) -> None:
        super().__init__()
        self._generate = generate or generator.generate  # injetável nos testes
        self.project = project
        self.role = role
        self.infos = generator.providers()
        self.busy = False
        self._extra_dirs: list = []

    def compose(self) -> ComposeResult:
        available = [(f"{i.label} — {i.note}", i.key) for i in self.infos if i.available]
        first = available[0][1] if available else "generic"
        with Vertical(id="pf"):
            yield Label(
                f"Novo perfil · {self.project.agent_name(self.role)}   (Ctrl+G gera · Ctrl+S salva · Esc cancela)",
                classes="title",
            )
            with Horizontal(id="pf-cols"):
                with Vertical(id="pf-left"):
                    with Vertical(classes="card"):
                        yield Label("① O que este agente deve fazer", classes="card-title")
                        yield TextArea("", id="pg-desc", placeholder=profiles_hint(self.role))
                        yield Label("Caminhos específicos para analisar (opcional; relativos ao projeto ou absolutos)")
                        yield Input(placeholder="ex.: docs/SPEC.md, data/sessions", id="pg-focus")
                        yield Static("", id="pg-auto")
                        yield Checkbox("Incluir a estrutura de data/ no contexto (só nomes)", value=False, id="pg-data")
                        cfg = self.project.roles.get(self.role)
                        if cfg is not None and cfg.cli == "claude":
                            yield Checkbox(
                                "Criar .claude/agents/" + self.role + ".md com ferramentas restritas (não sobrescreve)",
                                value=cfg.permission == "read-only",
                                id="pg-agentdef",
                            )
                    with Vertical(classes="card"):
                        yield Label("② Quem escreve o rascunho", classes="card-title")
                        yield Select(
                            available or [("Genérico", "generic")], allow_blank=False, value=first, id="pg-provider"
                        )
                        yield Label("Modelo (em branco = o padrão do provedor)")
                        yield Input(value=generator.DEFAULT_MODELS.get(first, ""), id="pg-model")
                        yield Button("Gerar rascunho", id="pg-generate", variant="primary")
                        yield Static("", id="pg-status")
                with Vertical(id="pf-summary"):
                    yield Label("③ Rascunho (editável) — nada vale até você salvar", classes="card-title")
                    yield TextArea("", id="pg-draft", language="markdown")
                    yield Static("", id="pg-check")
            with Horizontal(classes="buttons"):
                yield Button("Salvar perfil", id="save", variant="success", disabled=True)
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#pg-desc", TextArea).focus()
        self._sync_provider()

    def _sync_provider(self) -> None:
        """Quem lê arquivos explora o projeto sozinho; os outros recebem o contexto embutido."""
        provider = selected(self.query_one("#pg-provider", Select)) or "generic"
        reads = provider in generator.CAN_READ
        self.query_one("#pg-data", Checkbox).display = not reads
        note = self.query_one("#pg-auto", Static)
        note.update(
            Text(
                f"✓ {generator.LABELS[provider]} explora a pasta do projeto sozinho (somente leitura): não precisa listar arquivos."
                if reads
                else f"{generator.LABELS[provider]} não abre arquivos: recebe o AGENTS.md e o índice de docs/ embutidos.",
                style=T.C_OK if reads else T.C_MUTED,
            )
        )

    # ----------------------------------------------------------------- ações

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "pg-provider" and isinstance(event.value, str):
            self.query_one("#pg-model", Input).value = generator.DEFAULT_MODELS.get(event.value, "")
            self._sync_provider()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "pg-draft":
            self._check()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id
        if wid == "pg-generate":
            self.action_generate()
        elif wid == "save":
            self.action_save()
        elif wid == "cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _check(self) -> None:
        text = self.query_one("#pg-draft", TextArea).text
        ok, missing = generator.validate_profile(text) if text.strip() else (False, [])
        t = Text()
        if not text.strip():
            t.append("Sem rascunho ainda.", style=T.C_DIM)
        elif ok:
            t.append(
                "✓ estrutura completa (missão, responsabilidades, limites, entregáveis, ferramentas)", style=T.C_OK
            )
        else:
            t.append(f"▲ faltam seções: {', '.join(missing)} (você pode salvar assim, se quiser)", style=T.C_BUSY)
        self.query_one("#pg-check", Static).update(t)
        self.query_one("#save", Button).disabled = not text.strip()

    def action_generate(self) -> None:
        if self.busy:
            return
        description = self.query_one("#pg-desc", TextArea).text
        if not description.strip():
            self.query_one("#pg-status", Static).update(Text("Descreva o que o agente deve fazer.", style=T.C_OFF))
            return
        provider = selected(self.query_one("#pg-provider", Select)) or "generic"
        model = text_of(self.query_one("#pg-model", Input)) or ""
        focus, warnings = generator.focus_paths(self.project, self.query_one("#pg-focus", Input).value)
        if warnings:
            self.query_one("#pg-status", Static).update(Text("; ".join(warnings), style=T.C_OFF))
            return
        outside = generator.outside_project(self.project, focus)
        if outside and provider != "claude":
            names = ", ".join(str(p) for p in outside)
            msg = f"Fora do projeto só o Claude lê ({names}); escolha o Claude ou use caminhos do projeto."
            self.query_one("#pg-status", Static).update(Text(msg, style=T.C_OFF))
            return
        prompt = generator.build_prompt(
            self.project,
            self.role,
            description,
            data=self.query_one("#pg-data", Checkbox).value,
            can_read=provider in generator.CAN_READ,
            focus=focus,
        )
        self._extra_dirs = outside
        self.busy = True
        self.query_one("#pg-generate", Button).disabled = True
        self.query_one("#pg-status", Static).update(
            Text(f"Gerando com {generator.LABELS[provider]}… (pode levar um minuto)", style=T.C_BUSY)
        )
        self._run(provider, model, prompt)

    @work(thread=True, exclusive=True)
    def _run(self, provider: str, model: str, prompt: str) -> None:
        command = config.load_global().generator_command
        result = self._generate(
            provider, model, prompt, self.project, generic_command=command, extra_dirs=self._extra_dirs
        )
        self.app.call_from_thread(self._done, result)

    def _done(self, result: generator.Generated) -> None:
        self.busy = False
        self.query_one("#pg-generate", Button).disabled = False
        status = self.query_one("#pg-status", Static)
        if not result.ok:
            status.update(Text(f"✗ {result.error}", style=T.C_OFF))
            return
        self.query_one("#pg-draft", TextArea).load_text(result.text)
        status.update(
            Text(
                f"✓ rascunho de {generator.LABELS.get(result.provider, result.provider)}: revise e edite antes de salvar.",
                style=T.C_OK,
            )
        )
        self._check()

    def action_save(self) -> None:
        text = self.query_one("#pg-draft", TextArea).text
        if not text.strip():
            return
        try:
            source, target = profiles.save_profile(self.project, self.role, text)
        except OSError as exc:
            self.query_one("#pg-status", Static).update(Text(f"✗ {explain(exc)}", style=T.C_OFF))
            return
        message = f"Perfil salvo em {source}\nsincronizado em {target}"
        if self.query("#pg-agentdef") and self.query_one("#pg-agentdef", Checkbox).value:
            cfg = self.project.roles[self.role]
            restricted = cfg.permission in ("read-only", "plan")
            tools = list(profiles.READ_TOOLS) if restricted else [*profiles.READ_TOOLS, "Edit", "Write", "Bash"]
            summary = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), self.role)
            try:
                message += f"\ndefinição de agente: {profiles.write_agent_def(self.project, self.role, summary, tools)}"
            except FileExistsError:
                message += "\n(.claude/agents já existe: mantido como está)"
        self.app.notify(message, title="Perfil do agente", timeout=10, markup=False)
        self.dismiss(self.role)


def profiles_hint(role: str) -> str:
    return {
        "analyst": "Analisa os resultados do bot (somente leitura): ...",
        "developer": "Implementa as tarefas do projeto seguindo ...",
        "qa": "Valida de forma independente ...",
        "planner": "Investiga e planeja ...",
        "orchestrator": "Coordena o trabalho ...",
    }.get(role, "Descreva a missão, o que pode e o que não pode fazer: ...")
