from __future__ import annotations

import subprocess

from dev_orchestra.core import clipboard


def fake_which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_sem_ferramenta_nao_copia(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", fake_which())
    assert clipboard.available_tool() is None
    assert clipboard.system_copy("texto") is None


def test_wayland_prefere_wl_copy(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard.shutil, "which", fake_which("xclip", "wl-copy"))
    chamadas = []

    def run(args, **kw):
        chamadas.append((args, kw["input"]))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.system_copy("olá") == "wl-copy"
    assert chamadas == [(["/usr/bin/wl-copy"], "olá".encode())]


def test_x11_prefere_xclip(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which", fake_which("wl-copy", "xclip"))
    monkeypatch.setattr(clipboard.subprocess, "run", lambda a, **k: subprocess.CompletedProcess(a, 0))
    assert clipboard.available_tool() == "xclip"
    assert clipboard.system_copy("x") == "xclip"


def test_tenta_a_proxima_se_a_primeira_falha(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard.shutil, "which", fake_which("wl-copy", "xclip"))

    def run(args, **kw):
        return subprocess.CompletedProcess(args, 1 if args[0].endswith("wl-copy") else 0)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.system_copy("x") == "xclip"


def test_timeout_e_erro_de_execucao_sao_ignorados(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", fake_which("wl-copy", "xclip"))

    def run(args, **kw):
        if args[0].endswith("wl-copy"):
            raise subprocess.TimeoutExpired(args, 3)
        raise OSError("falhou")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.system_copy("x") is None
