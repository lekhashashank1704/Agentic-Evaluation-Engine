"""Isolated sandbox used to execute and analyse artifact code."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "simulation" / "sandbox_template"


class Sandbox:
    """Copies the governance-owned harness and the artifact's Python files into a temp dir.

    Governance-owned files (tests, baseline) are written *after* artifact files so an
    artifact can never overwrite the acceptance suite.
    """

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.root: Path | None = None

    def __enter__(self) -> Path:
        self.root = Path(tempfile.mkdtemp(prefix="gov-sbx-"))
        for path, content in self.files.items():
            if not path.endswith(".py") or path.startswith(("tests/", "baseline/")) or path == "conftest.py":
                continue
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        for pkg in {p.parent for p in self.root.rglob("*.py")}:
            init = pkg / "__init__.py"
            if pkg != self.root and not init.exists():
                init.write_text("")
        shutil.copytree(TEMPLATE, self.root, dirs_exist_ok=True)
        return self.root

    def __exit__(self, *exc):
        if self.root:
            shutil.rmtree(self.root, ignore_errors=True)
