"""Functional Test Evaluator: executes the governance-owned acceptance suite with pytest."""

from __future__ import annotations

import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

from app.evaluation.sandbox import Sandbox
from app.evaluation.support import EvidenceCollector
from app.schemas import Check, CheckStatus, EvaluatorReport, Severity

CRITERION_RE = re.compile(r"test_(ac\d+|reg)_")


def criterion_of(test_name: str) -> str:
    m = CRITERION_RE.search(test_name)
    return (m.group(1).upper().replace("AC", "AC-") if m else "UNMAPPED")


def run_pytest(root, timeout: int = 60) -> dict[str, Any]:
    report = root / "junit.xml"
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}", "tests"],
        cwd=root, capture_output=True, text=True, timeout=timeout,
    )
    results: list[dict[str, Any]] = []
    if report.exists():
        tree = ET.parse(report)
        for case in tree.iter("testcase"):
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            status = "failed" if failure is not None else "error" if error is not None else \
                "skipped" if skipped is not None else "passed"
            node = failure if failure is not None else error
            results.append({
                "name": case.get("name"), "classname": case.get("classname"), "status": status,
                "time": float(case.get("time", 0)),
                "message": (node.get("message") or "")[:300] if node is not None else "",
            })
    return {"returncode": proc.returncode, "results": results, "stdout_tail": proc.stdout[-1500:],
            "stderr_tail": proc.stderr[-800:], "duration_s": round(time.time() - started, 3)}


def evaluate_functional(files: dict[str, str], ticket: dict[str, Any], ev: EvidenceCollector) -> EvaluatorReport:
    t0 = time.time()
    with Sandbox(files) as root:
        run = run_pytest(root)
    results = run["results"]
    evidence = ev.add(
        type="test_report", source="pytest (governance-owned acceptance suite)", executed=True, simulated=False,
        summary=f"pytest executed {len(results)} tests (exit code {run['returncode']}).",
        data={"tests": results, "stdout_tail": run["stdout_tail"], "duration_s": run["duration_s"]},
    )
    checks: list[Check] = []
    if not results:
        checks.append(Check(check_id="FT-COLLECT", name="Test collection", category="functional",
                            status=CheckStatus.ERROR, severity=Severity.HIGH, mandatory=True,
                            message="No test results were produced; the artifact could not be imported.",
                            evidence_ids=[evidence.evidence_id]))
    by_criterion: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        crit = criterion_of(r["name"])
        by_criterion.setdefault(crit, []).append(r)
        ok = r["status"] == "passed"
        checks.append(Check(
            check_id=f"FT-{r['name']}", name=r["name"], category="regression" if crit == "REG" else "acceptance",
            status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
            severity=Severity.INFO if ok else Severity.HIGH, mandatory=True,
            message="passed" if ok else (r["message"] or r["status"]), criterion_id=crit,
            evidence_ids=[evidence.evidence_id],
        ))
    executed = [r for r in results if r["status"] != "skipped"]
    passed = [r for r in executed if r["status"] == "passed"]
    pass_rate = len(passed) / len(executed) if executed else 0.0
    ticket_criteria = [c["id"] for c in ticket.get("acceptance_criteria", [])]
    criteria_status = {}
    for crit in ticket_criteria:
        tests = by_criterion.get(crit, [])
        criteria_status[crit] = ("NOT_COVERED" if not tests else
                                 "PASSED" if all(t["status"] == "passed" for t in tests) else "FAILED")
    covered = [c for c, s in criteria_status.items() if s != "NOT_COVERED"]
    satisfied = [c for c, s in criteria_status.items() if s == "PASSED"]
    regression_failures = [r["name"] for r in by_criterion.get("REG", []) if r["status"] != "passed"]
    explanation = [
        f"{len(passed)}/{len(executed)} governance-owned tests passed (pass rate {pass_rate:.0%}).",
        f"Acceptance criteria satisfied: {len(satisfied)}/{len(ticket_criteria)}"
        + (f" (failing: {', '.join(c for c in ticket_criteria if criteria_status[c] == 'FAILED')})"
           if len(satisfied) < len(ticket_criteria) else "."),
    ]
    if regression_failures:
        explanation.append(f"Regression failures: {', '.join(regression_failures)}.")
    return EvaluatorReport(
        evaluator="functional_test", status="COMPLETED", score=round(pass_rate, 4),
        passed=bool(executed) and len(passed) == len(executed), checks=checks, evidence=[evidence],
        metrics={
            "tests_executed": len(executed), "tests_passed": len(passed), "pass_rate": round(pass_rate, 4),
            "unit_tests": {"executed": 0, "note": "No agent-authored unit tests are trusted for scoring."},
            "integration_tests": {"executed": len(executed), "passed": len(passed)},
            "acceptance_criteria": criteria_status, "criteria_coverage": len(covered) / max(1, len(ticket_criteria)),
            "criteria_satisfied": len(satisfied) / max(1, len(ticket_criteria)),
            "regression_failures": regression_failures,
        },
        explanation=explanation, duration_ms=int((time.time() - t0) * 1000),
    )
