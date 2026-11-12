"""Code Quality Evaluator: Ruff (lint, naming, complexity), Radon (maintainability) and duplication."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.evaluation.support import EvidenceCollector, ThresholdManager
from app.schemas import Check, CheckStatus, EvaluatorReport, Severity


def _ruff_cmd() -> list[str] | None:
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    try:
        import ruff  # noqa: F401
        return [sys.executable, "-m", "ruff"]
    except ImportError:
        return None


def _duplicates(files: dict[str, str], window: int) -> list[dict]:
    seen: dict[tuple[str, ...], tuple[str, int]] = {}
    dups = []
    for path, content in files.items():
        lines = [ln.strip() for ln in content.splitlines()]
        for i in range(len(lines) - window + 1):
            block = tuple(lines[i:i + window])
            if sum(1 for b in block if len(b) > 3) < window:
                continue
            if block in seen and seen[block] != (path, i):
                dups.append({"file": path, "line": i + 1, "duplicate_of": f"{seen[block][0]}:{seen[block][1] + 1}"})
            else:
                seen.setdefault(block, (path, i))
    return dups


def evaluate_code_quality(files: dict[str, str], ev: EvidenceCollector, tm: ThresholdManager) -> EvaluatorReport:
    t0 = time.time()
    cfg = tm.get("quality.code_quality", {})
    py_files = {p: c for p, c in files.items() if p.endswith(".py")}
    checks: list[Check] = []
    evidence = []
    lint: list[dict] = []
    ruff = _ruff_cmd()
    max_cc = cfg.get("max_cyclomatic_complexity", 10)
    with tempfile.TemporaryDirectory() as tmp:
        for path, content in py_files.items():
            p = Path(tmp) / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        if ruff:
            proc = subprocess.run(
                ruff + ["check", "--no-cache", "--output-format", "json", "--line-length", "110",
                        "--select", ",".join(cfg.get("ruff_select", ["E", "F", "W", "N"])),
                        "--config", f"lint.mccabe.max-complexity = {max_cc}", "."],
                cwd=tmp, capture_output=True, text=True, timeout=60)
            try:
                raw = json.loads(proc.stdout or "[]")
            except json.JSONDecodeError:
                raw = []
            for r in raw:
                lint.append({"code": r.get("code"), "message": r.get("message"),
                             "file": r.get("filename", "").replace(tmp + "/", ""),
                             "line": r.get("location", {}).get("row")})
            evidence.append(ev.add(type="static_analysis", source="ruff", executed=True, simulated=False,
                                   summary=f"Ruff reported {len(lint)} findings on {len(py_files)} Python files.",
                                   data={"findings": lint}))
        else:
            evidence.append(ev.add(type="static_analysis", source="ruff", executed=False, simulated=False,
                                   summary="Ruff is not installed; lint check skipped (not counted as passed)."))
    complexity = []
    mi_scores = {}
    try:
        from radon.complexity import cc_visit
        from radon.metrics import mi_visit
        for path, content in py_files.items():
            try:
                for block in cc_visit(content):
                    complexity.append({"file": path, "name": block.name, "complexity": block.complexity,
                                       "line": block.lineno})
                mi_scores[path] = round(mi_visit(content, multi=True), 1)
            except SyntaxError:
                mi_scores[path] = 0.0
        evidence.append(ev.add(type="static_analysis", source="radon", executed=True, simulated=False,
                               summary=f"Radon analysed {len(complexity)} functions.",
                               data={"complexity": complexity, "maintainability_index": mi_scores}))
        radon_ok = True
    except ImportError:
        radon_ok = False
    complex_fns = [c for c in complexity if c["complexity"] > max_cc]
    dups = _duplicates(py_files, cfg.get("max_duplicate_block_lines", 6))
    ev_ids = [e.evidence_id for e in evidence]
    by_code: dict[str, list[dict]] = {}
    for item in lint:
        by_code.setdefault(item["code"] or "?", []).append(item)
    for code, items in sorted(by_code.items()):
        checks.append(Check(
            check_id=f"CQ-{code}", name=f"{code}: {items[0]['message']}", category="lint" if not code.startswith("N")
            else "naming", status=CheckStatus.FAILED, severity=Severity.LOW, criterion_id=code,
            message="; ".join(f"{i['file']}:{i['line']}" for i in items[:5]), evidence_ids=ev_ids))
    for c in complex_fns:
        checks.append(Check(check_id=f"CQ-CC-{c['name']}", name=f"Complexity {c['complexity']} in {c['name']}",
                            category="complexity", status=CheckStatus.FAILED, severity=Severity.LOW,
                            message=f"{c['file']}:{c['line']} exceeds {max_cc}", evidence_ids=ev_ids))
    for d in dups:
        checks.append(Check(check_id=f"CQ-DUP-{d['file']}:{d['line']}", name="Duplicated code block",
                            category="duplication", status=CheckStatus.WARNING, severity=Severity.LOW,
                            message=f"{d['file']}:{d['line']} duplicates {d['duplicate_of']}", evidence_ids=ev_ids))
    if not checks:
        checks.append(Check(check_id="CQ-CLEAN", name="Lint, naming, complexity and duplication clean",
                            category="lint", status=CheckStatus.PASSED if ruff else CheckStatus.SKIPPED,
                            evidence_ids=ev_ids))
    score = max(0.0, 1 - cfg.get("lint_penalty", 0.1) * len(lint) - cfg.get("complexity_penalty", 0.15)
                * len(complex_fns) - 0.05 * len(dups)) if ruff else 0.0
    avg_mi = round(sum(mi_scores.values()) / len(mi_scores), 1) if mi_scores else None
    explanation = [f"{len(lint)} lint/naming findings, {len(complex_fns)} functions above complexity {max_cc}, "
                   f"{len(dups)} duplicated blocks.",
                   f"Average maintainability index: {avg_mi}" if radon_ok else "Radon unavailable."]
    return EvaluatorReport(
        evaluator="code_quality", status="COMPLETED" if ruff else "SKIPPED", score=round(score, 4),
        passed=bool(ruff) and not lint and not complex_fns, checks=checks, evidence=evidence,
        metrics={"lint_findings": len(lint), "by_code": {k: len(v) for k, v in by_code.items()},
                 "complex_functions": complex_fns, "max_complexity": max((c["complexity"] for c in complexity),
                                                                        default=0),
                 "maintainability_index": mi_scores, "avg_maintainability_index": avg_mi, "duplicates": dups},
        explanation=explanation, duration_ms=int((time.time() - t0) * 1000))
