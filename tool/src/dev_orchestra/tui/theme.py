"""Paleta e pequenos componentes visuais padronizados (as mesmas cores em todas as telas)."""

from __future__ import annotations

from rich.text import Text

# --- estado das sessões
C_OK = "#5fd787"  # ociosa / tudo certo
C_BUSY = "#ffd75f"  # ocupada / atenção
C_OFF = "#ff6f6f"  # fechada / erro
C_UNK = "#8b95a5"  # desconhecida / neutro
# --- informação
C_NEW = "#5fafff"  # primeira vez / informação / tarefa pronta
C_OWN = "#af87ff"  # método próprio / CLI claude
C_TRELLO = "#ffaf5f"  # Trello / CLI codex
C_FILES = "#87d7d7"  # arquivos locais
C_REV = "#d787d7"  # em teste
# --- texto e fundo
C_TEXT = "#c9d1d9"
C_STRONG = "#e6edf3"
C_MUTED = "#8b95a5"
C_DIM = "#5c6773"
C_BG = "#0f1419"
C_PANEL = "#131a22"
C_PANEL_FOCUS = "#18222e"
C_BORDER = "#2b3440"

STATE_COLOR = {
    "ociosa": C_OK,
    "ocupada": C_BUSY,
    "fechada": C_OFF,
    "desconhecida": C_UNK,
    "erro": C_OFF,
    "consultando": C_DIM,
}
STATE_DOT = {"ociosa": "●", "ocupada": "◐", "fechada": "○", "desconhecida": "?", "erro": "✗", "consultando": "…"}

# cor da borda do cartão por saúde do projeto
HEALTH_BORDER = {"ok": "#3f9b66", "warn": "#b8962e", "idle": "#4a5563", "new": "#3f7fbf", "loading": "#2b3440"}
HEALTH_TEXT = {"ok": C_OK, "warn": C_BUSY, "idle": C_UNK, "new": C_NEW, "loading": C_DIM}

# estados das tarefas: (rótulo, cor)
TASK_STATE = {
    "draft": ("rascunho", C_UNK),
    "ready": ("pronta", C_NEW),
    "delegated": ("delegada", C_BUSY),
    "running": ("executando", C_BUSY),
    "asked": ("pergunta", C_BUSY),
    "needs_review": ("em teste", C_REV),
    "done": ("concluída", C_OK),
    "partial": ("parcial", C_BUSY),
    "blocked": ("bloqueada", C_OFF),
    "failed": ("falhou", C_OFF),
    "cancelled": ("cancelada", C_DIM),
}

ACTIVITY_COLOR = {"ok": C_OK, "info": C_NEW, "aviso": C_BUSY, "erro": C_OFF}


def badge(label: str, color: str) -> Text:
    return Text(f" {label} ", style=f"bold #10141a on {color}")


def method_badge(method: str) -> Text:
    return badge("MÉTODO PRÓPRIO", C_OWN) if method == "proprio" else badge("MÉTODO PADRÃO", "#6c7a89")


def source_badge(kind: str) -> Text:
    return badge("TRELLO", C_TRELLO) if kind == "trello" else badge("ARQUIVOS", C_FILES)


def bar(done: int, total: int, width: int = 22) -> Text:
    filled = round(width * done / total) if total else 0
    t = Text()
    t.append("█" * filled, style=C_OK)
    t.append("░" * (width - filled), style="#3b4452")
    return t


def state_text(state: str, *, with_dot: bool = True) -> Text:
    color = STATE_COLOR.get(state, C_UNK)
    label = f"{STATE_DOT.get(state, '?')} {state}" if with_dot else state
    return Text(label, style=color)


def task_state_text(state: str, live: str | None = None) -> Text:
    """`● executando`; com `↻` quando o estado vem do delegador e o arquivo da tarefa ainda não foi atualizado."""
    label, color = TASK_STATE.get(state, (state, C_UNK))
    return Text(f"● {label}" + (" ↻" if live else ""), style=color)
