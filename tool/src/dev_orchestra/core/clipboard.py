"""Cópia para a área de transferência do sistema (quando houver uma ferramenta instalada).

O OSC 52 do Textual (`App.copy_to_clipboard`) depende do terminal e muitos (como os baseados em VTE)
não o aceitam. Por isso tentamos antes `wl-copy` (Wayland), `xclip`/`xsel` (X11) e `pbcopy` (macOS).
"""

from __future__ import annotations

import os
import shutil
import subprocess

# (executável, argumentos); a ordem depende da sessão gráfica
_TOOLS: list[tuple[str, list[str]]] = [
    ("wl-copy", []),
    ("xclip", ["-selection", "clipboard"]),
    ("xsel", ["--clipboard", "--input"]),
    ("pbcopy", []),
]


def available_tool() -> str | None:
    """Nome da primeira ferramenta de cópia encontrada no PATH, ou None."""
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    tools = sorted(_TOOLS, key=lambda t: (t[0] != "wl-copy") if wayland else (t[0] == "wl-copy"))
    for name, _args in tools:
        if shutil.which(name):
            return name
    return None


def system_copy(text: str) -> str | None:
    """Copia `text` pela ferramenta do sistema e devolve o nome dela; None se não foi possível."""
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    tools = sorted(_TOOLS, key=lambda t: (t[0] != "wl-copy") if wayland else (t[0] == "wl-copy"))
    for name, args in tools:
        path = shutil.which(name)
        if not path:
            continue
        try:
            done = subprocess.run(
                [path, *args],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0:
            return name
    return None
