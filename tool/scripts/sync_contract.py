"""Copia o contrato do Dev Orchestra (arquivos da raiz do repositório) para dentro do pacote.

Uso (a partir de `tool/`):  uv run python scripts/sync_contract.py

O pacote leva uma cópia dos arquivos para poder criá-los nos projetos cadastrados. Esta cópia
precisa acompanhar o repositório; `tests/test_contract.py` falha se ela ficar desatualizada.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUNDLE = Path(__file__).resolve().parents[1] / "src" / "dev_orchestra" / "contract"

# (caminho no repositório, caminho dentro do pacote)
SOURCES = [
    ("AGENTS.md", "AGENTS.md"),
    *[(f"roles/{r}.md", f"roles/{r}.md") for r in ("orchestrator", "developer", "qa", "planner", "social-media")],
    *[
        (f"docs/{d}.md", f"docs/{d}.md")
        for d in ("PROTOCOL", "MODEL-SELECTION", "ADAPTER-CLAUDE-CODE", "ADAPTER-CODEX", "DELEGATOR")
    ],
    ("templates/handoff.md", "templates/handoff.md"),
    ("templates/result.md", "templates/result.md"),
    *[(f"adapters/claude-code/agents/{a}.md", f"claude-agents/{a}.md") for a in ("developer", "planner", "qa")],
]


def main() -> int:
    missing = [src for src, _ in SOURCES if not (REPO / src).is_file()]
    if missing:
        print("arquivos ausentes no repositório:", ", ".join(missing), file=sys.stderr)
        return 1
    for src, dest in SOURCES:
        target = BUNDLE / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / src, target)
    print(f"{len(SOURCES)} arquivos copiados para {BUNDLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
