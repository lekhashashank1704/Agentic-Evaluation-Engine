"""Security Evaluator.

Uses Semgrep when it is installed (rules in ``governance/semgrep_rules.yml``),
otherwise a built-in deterministic rule engine (regex + Python AST) that
implements the same rules. Dependencies are checked against a bundled offline
advisory snapshot, which is labelled as such in the evidence.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from app.evaluation.support import EvidenceCollector, ThresholdManager
from app.schemas import Check, CheckStatus, EvaluatorReport, Finding, Severity
from app.simulation.code_templates import BASE_REPO

SEMGREP_RULES = Path(__file__).resolve().parent.parent / "governance" / "semgrep_rules.yml"

# Offline advisory snapshot (subset of public advisories) used for deterministic demos.
ADVISORIES = [
    {"package": "requests", "vulnerable_below": "2.32.0", "id": "CVE-2024-35195", "severity": "HIGH",
     "title": "requests Session keeps verify=False for subsequent requests", "fixed_in": "2.32.0"},
    {"package": "pyyaml", "vulnerable_below": "5.4", "id": "CVE-2020-14343", "severity": "CRITICAL",
     "title": "Arbitrary code execution via full_load", "fixed_in": "5.4"},
    {"package": "jinja2", "vulnerable_below": "3.1.4", "id": "CVE-2024-34064", "severity": "MEDIUM",
     "title": "Attribute injection via xmlattr filter", "fixed_in": "3.1.4"},
]

SECRET_PATTERNS = [
    (re.compile(r"sk_live_[0-9A-Za-z]{16,}"), "Live payment API secret key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"), "Private key material"),
    (re.compile(r"(?i)(password|passwd|secret|api_key|apikey|token)\s*=\s*[\"'][^\"'\s]{12,}[\"']"),
     "Hardcoded credential assignment"),
]
SQL_KEYWORDS = re.compile(r"(?i)\b(select|insert|update|delete)\b")
PII_LOG = re.compile(r"(?i)(print|logger\.\w+|logging\.\w+)\(.*(token|password|authorization|card_number)")


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def parse_requirements(text: str) -> dict[str, str]:
    reqs = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            reqs[name.strip().lower()] = ver.strip()
    return reqs


class _SQLVisitor(ast.NodeVisitor):
    def __init__(self):
        self.hits: list[int] = []
        self.dynamic_vars: dict[str, int] = {}

    @staticmethod
    def _is_dynamic(node: ast.AST) -> bool:
        if isinstance(node, ast.JoinedStr):
            return any(isinstance(v, ast.FormattedValue) for v in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return True
        return False

    def visit_Assign(self, node: ast.Assign):
        if self._is_dynamic(node.value) and SQL_KEYWORDS.search(ast.unparse(node.value)):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.dynamic_vars[t.id] = node.lineno
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany", "executescript"} \
                and node.args:
            arg = node.args[0]
            if (self._is_dynamic(arg) and SQL_KEYWORDS.search(ast.unparse(arg))) or \
                    (isinstance(arg, ast.Name) and arg.id in self.dynamic_vars):
                self.hits.append(node.lineno)
        self.generic_visit(node)


def _builtin_scan(files: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    n = 0

    def add(**kw):
        nonlocal n
        n += 1
        findings.append(Finding(finding_id=f"SF-{n:03d}", tool="builtin-sast", **kw))

    for path, content in sorted(files.items()):
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            for pattern, title in SECRET_PATTERNS:
                if pattern.search(line):
                    add(rule_id="SEC-SECRET-001", title=title, severity=Severity.CRITICAL, file=path, line=i,
                        snippet=re.sub(r"([\"'])[^\"']{6}([^\"']*)", r"\1******\2", line.strip())[:120],
                        category="credentials", remediation="Load secrets from the secret manager at runtime.")
                    break
            if PII_LOG.search(line):
                add(rule_id="SEC-PII-001", title="Sensitive value written to logs", severity=Severity.MEDIUM,
                    file=path, line=i, snippet=line.strip()[:120], category="data_exposure",
                    remediation="Remove or redact sensitive fields before logging.")
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            add(rule_id="SEC-PARSE-001", title=f"File could not be parsed: {exc.msg}", severity=Severity.MEDIUM,
                file=path, line=exc.lineno, category="analysis")
            continue
        visitor = _SQLVisitor()
        visitor.visit(tree)
        for line_no in sorted(set(visitor.hits)):
            add(rule_id="SEC-SQLI-001", title="SQL query built from untrusted input", severity=Severity.HIGH,
                file=path, line=line_no, snippet=lines[line_no - 1].strip()[:120], category="injection",
                remediation="Use parameterised queries (placeholders) instead of string concatenation.")
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            args = [a.arg for a in fn.args.args]
            if fn.name.startswith(("get_", "post_", "put_", "delete_", "handle_")) and "request" in args:
                calls = {c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
                         for c in ast.walk(fn) if isinstance(c, ast.Call)}
                if not calls & {"authenticate", "verify_token", "require_auth"}:
                    add(rule_id="SEC-AUTH-001", title=f"Endpoint handler '{fn.name}' performs no authentication",
                        severity=Severity.HIGH, file=path, line=fn.lineno, category="authentication",
                        remediation="Authenticate the caller and verify resource ownership before data access.")
        base = BASE_REPO.get(path)
        if base and "token_validator" in path:
            skew_old = re.search(r"CLOCK_SKEW_SECONDS\s*=\s*(\d+)", base)
            skew_new = re.search(r"CLOCK_SKEW_SECONDS\s*=\s*(\d+)", content)
            if skew_old and skew_new and int(skew_new.group(1)) > int(skew_old.group(1)):
                add(rule_id="SEC-AUTHN-002", title="Token clock-skew tolerance increased", severity=Severity.MEDIUM,
                    file=path, category="authentication",
                    remediation="Justify the wider tolerance or keep the existing value.")
            if "ACCEPTED_ISSUERS" in content and content.count("https://") > base.count("https://"):
                add(rule_id="SEC-AUTHN-003", title="Additional trusted token issuer", severity=Severity.MEDIUM,
                    file=path, category="authentication",
                    remediation="Security review required for new identity providers.")
    return findings


def _semgrep_scan(files: dict[str, str]) -> list[Finding] | None:
    exe = shutil.which("semgrep")
    if not exe or not SEMGREP_RULES.exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        for path, content in files.items():
            p = Path(tmp) / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        proc = subprocess.run([exe, "--config", str(SEMGREP_RULES), "--json", "--quiet", tmp],
                              capture_output=True, text=True, timeout=120)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
    out = []
    for i, r in enumerate(data.get("results", []), 1):
        sev = {"ERROR": Severity.HIGH, "WARNING": Severity.MEDIUM, "INFO": Severity.LOW}.get(
            r.get("extra", {}).get("severity", "WARNING"), Severity.MEDIUM)
        meta = r.get("extra", {}).get("metadata", {})
        if meta.get("governance_severity"):
            sev = Severity(meta["governance_severity"])
        out.append(Finding(finding_id=f"SG-{i:03d}", rule_id=meta.get("governance_rule", r.get("check_id")),
                           title=r.get("extra", {}).get("message", ""), severity=sev,
                           file=str(Path(r["path"]).relative_to(tmp)) if r.get("path") else None,
                           line=r.get("start", {}).get("line"), tool="semgrep", category=meta.get("category", "sast")))
    return out


def dependency_scan(files: dict[str, str]) -> tuple[list[Finding], dict[str, Any]]:
    findings = []
    new = parse_requirements(files.get("requirements.txt", ""))
    old = parse_requirements(BASE_REPO.get("requirements.txt", ""))
    added = sorted(set(new) - set(old))
    changed = sorted(k for k in set(new) & set(old) if new[k] != old[k])
    for i, (pkg, ver) in enumerate(sorted(new.items()), 1):
        for adv in ADVISORIES:
            if adv["package"] == pkg and _version_tuple(ver) < _version_tuple(adv["vulnerable_below"]):
                findings.append(Finding(
                    finding_id=f"DEP-{i:03d}", rule_id=f"SEC-DEP-{adv['id']}", title=f"{pkg}=={ver}: {adv['title']}",
                    severity=Severity(adv["severity"]), file="requirements.txt", category="dependency",
                    tool="offline-advisory-db", remediation=f"Upgrade {pkg} to >= {adv['fixed_in']}."))
    return findings, {"added": added, "changed": changed, "all": new}


def evaluate_security(files: dict[str, str], ev: EvidenceCollector, tm: ThresholdManager) -> EvaluatorReport:
    t0 = time.time()
    findings = _semgrep_scan(files) if tm.get("security.prefer_semgrep", True) else None
    tool = "semgrep" if findings is not None else "builtin-sast (semgrep-compatible rules)"
    if findings is None:
        findings = _builtin_scan(files)
    sast_ev = ev.add(type="static_analysis", source=tool, executed=True, simulated=False,
                     summary=f"{tool} scanned {len(files)} files and reported {len(findings)} findings.",
                     data={"findings": [f.model_dump() for f in findings],
                           "semgrep_available": shutil.which("semgrep") is not None})
    dep_findings, dep_info = dependency_scan(files)
    dep_ev = ev.add(type="dependency_scan", source="offline-advisory-db", executed=True, simulated=True,
                    summary=f"Checked {len(dep_info['all'])} pinned dependencies against the bundled advisory "
                            f"snapshot; {len(dep_findings)} vulnerable.",
                    data={**dep_info, "advisory_snapshot": "bundled-2026-10 (offline, may be stale)"})
    findings = findings + dep_findings
    points = tm.get("security.severity_points", {})
    block_sev = set(tm.get("security.block_severities", ["CRITICAL"]))
    max_high = tm.get("quality.development.mandatory_gates.max_high_security_findings", 0)
    counts = {s.value: sum(1 for f in findings if f.severity == s) for s in Severity}
    penalty = sum(points.get(f.severity.value, 0) for f in findings)
    score = max(0.0, 1 - penalty / 100)
    blocking = [f for f in findings if f.severity.value in block_sev]
    checks = []
    for f in findings:
        ev_id = dep_ev.evidence_id if f.category == "dependency" else sast_ev.evidence_id
        checks.append(Check(
            check_id=f"SEC-{f.finding_id}", name=f.title, category=f"security:{f.category}",
            status=CheckStatus.FAILED if f.severity in (Severity.CRITICAL, Severity.HIGH) else CheckStatus.WARNING,
            severity=f.severity, mandatory=f.severity.value in block_sev or f.severity == Severity.HIGH,
            message=f"{f.rule_id} at {f.file}:{f.line or '-'}", criterion_id=f.rule_id, evidence_ids=[ev_id]))
    if not findings:
        checks.append(Check(check_id="SEC-CLEAN", name="No security findings", category="security",
                            status=CheckStatus.PASSED, evidence_ids=[sast_ev.evidence_id, dep_ev.evidence_id]))
    passed = not blocking and counts["HIGH"] <= max_high
    explanation = [f"{tool}: {counts['CRITICAL']} critical, {counts['HIGH']} high, {counts['MEDIUM']} medium, "
                   f"{counts['LOW']} low findings."]
    if blocking:
        explanation.append("Mandatory block: " + "; ".join(f"{f.rule_id} {f.title} ({f.file})" for f in blocking))
    elif counts["HIGH"] > max_high:
        explanation.append(f"{counts['HIGH']} high-severity findings exceed the policy maximum of {max_high}.")
    return EvaluatorReport(
        evaluator="security", status="COMPLETED", score=round(score, 4), passed=passed, checks=checks,
        findings=findings, evidence=[sast_ev, dep_ev],
        metrics={"counts": counts, "penalty_points": penalty, "blocking": len(blocking), "tool": tool,
                 "dependencies": dep_info},
        explanation=explanation, duration_ms=int((time.time() - t0) * 1000))
