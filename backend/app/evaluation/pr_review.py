"""PR Review Evaluator: requirement alignment, design conformance, scope and documentation.

Deterministic static checks run by default. An optional ``LLMJudge`` can add
qualitative review notes; its output is reported separately and never changes
the deterministic pass/fail of a component.
"""

from __future__ import annotations

import ast
import re
import time
from typing import Any

from app.agents.base import LLMJudge
from app.evaluation.support import EvidenceCollector
from app.schemas import Check, CheckStatus, EvaluatorReport, Severity

# keyword in ticket text -> required component
COMPONENT_KEYWORDS = {
    "authentication": [r"authenticat", r"bearer token"],
    "authorization": [r"only read their own", r"authoriz", r"ownership"],
    "pagination": [r"paginat", r"page_size"],
    "response_validation": [r"validat"],
    "documentation": [r"document"],
}

# approved design: the modules this change may touch
APPROVED_DESIGN_PATHS = ["service/transaction_history.py", "service/api/", "service/auth/", "service/payments/",
                         "docs/", "migrations/", "requirements.txt", "tests/", "config/feature_flags.yaml", "helm/"]


def required_components(ticket: dict[str, Any]) -> list[str]:
    text = " ".join([ticket.get("description", ""), *ticket.get("functional_requirements", []),
                     *ticket.get("non_functional_requirements", [])]).lower()
    return [c for c, pats in COMPONENT_KEYWORDS.items() if any(re.search(p, text) for p in pats)]


def _handler(tree: ast.Module) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("get_") and \
                "request" in [a.arg for a in node.args.args]:
            return node
    return None


def detect_components(files: dict[str, str]) -> dict[str, tuple[bool, str]]:
    src = files.get("service/transaction_history.py", "")
    found: dict[str, tuple[bool, str]] = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    handler = _handler(tree) if tree else None
    handler_src = ast.unparse(handler) if handler else ""
    calls = {c.func.id for c in ast.walk(handler) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)} \
        if handler else set()
    found["authentication"] = (bool(calls & {"authenticate", "verify_token"}) and "401" in handler_src,
                               "handler authenticates the caller and returns 401" if handler else "no handler found")
    found["authorization"] = (bool(re.search(r"caller\s*!=\s*customer_id", handler_src)) and "403" in handler_src,
                              "ownership comparison with 403 response")
    found["pagination"] = ("MAX_PAGE_SIZE" in src and bool(re.search(r"<=\s*MAX_PAGE_SIZE", src))
                           and "(page - 1) * page_size" in src, "bounded page_size and zero-based offset")
    found["response_validation"] = ("validate_item" in calls or "validate_item(" in handler_src,
                                    "items validated against ITEM_SCHEMA before returning")
    docs = [p for p in files if p.startswith("docs/") and "/transactions" in files[p]]
    found["documentation"] = (bool(docs), f"documentation files: {', '.join(docs) or 'none'}")
    return found


def evaluate_pr_review(files: dict[str, str], diff: str, ticket: dict[str, Any], ev: EvidenceCollector,
                       judge: LLMJudge | None = None) -> EvaluatorReport:
    t0 = time.time()
    required = required_components(ticket)
    detected = detect_components(files)
    unnecessary = [p for p in files if not any(p.startswith(a) or p == a for a in APPROVED_DESIGN_PATHS)]
    evidence = ev.add(type="diff", source="pr-review-static-analyzer", executed=True, simulated=False,
                      summary=f"Reviewed {len(files)} changed files against {len(required)} required components "
                              "and the approved design.",
                      data={"required_components": required, "detected": {k: v[0] for k, v in detected.items()},
                            "unnecessary_files": unnecessary, "diff_lines": diff.count("\n")})
    checks: list[Check] = []
    implemented = 0
    for comp in required:
        ok, why = detected.get(comp, (False, "not detectable"))
        implemented += ok
        checks.append(Check(check_id=f"PR-COMP-{comp}", name=f"Required component: {comp}",
                            category="requirement_alignment", status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
                            severity=Severity.INFO if ok else Severity.MEDIUM, criterion_id=f"component:{comp}",
                            message=why if ok else f"Missing or incomplete: {why}",
                            evidence_ids=[evidence.evidence_id]))
    for path in unnecessary:
        checks.append(Check(check_id=f"PR-SCOPE-{path}", name=f"Change outside approved design: {path}",
                            category="scope", status=CheckStatus.FAILED, severity=Severity.LOW,
                            criterion_id="unnecessary_change", message="File is not part of the approved design.",
                            evidence_ids=[evidence.evidence_id]))
    judge_notes: dict[str, Any] = {"status": "not_configured"}
    if judge is not None:
        judge_notes = judge.review(ticket, diff)
    checks.append(Check(check_id="PR-LLM-JUDGE", name="LLM-as-a-judge qualitative review", category="llm_judge",
                        status=CheckStatus.SKIPPED if judge is None else CheckStatus.PASSED,
                        message="No LLM judge configured (offline mode)." if judge is None else "See judge notes."))
    coverage = implemented / len(required) if required else 1.0
    missing = [c for c in required if not detected.get(c, (False,))[0]]
    explanation = [f"Requirement coverage {implemented}/{len(required)} ({coverage:.0%})."]
    if missing:
        explanation.append(f"Missing components: {', '.join(missing)}.")
    if unnecessary:
        explanation.append(f"Unnecessary changes outside the approved design: {', '.join(unnecessary)}.")
    return EvaluatorReport(
        evaluator="pr_review", status="COMPLETED", score=round(coverage, 4), passed=not missing and not unnecessary,
        checks=checks, evidence=[evidence],
        metrics={"required_components": required, "implemented": implemented, "coverage": round(coverage, 4),
                 "missing_components": missing, "unnecessary_files": unnecessary, "llm_judge": judge_notes},
        explanation=explanation, duration_ms=int((time.time() - t0) * 1000))
