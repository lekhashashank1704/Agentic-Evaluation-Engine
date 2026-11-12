"""Default governance policy (version 1.0).

This is the *seed* for the versioned policy store. At runtime the active policy is
always read from the database (``policy_versions`` table) through the
ThresholdManager, so no threshold below is hardcoded anywhere else in the code.
Policy changes are only made through an approved PolicyProposal, which creates a
new version.
"""

DEFAULT_POLICY: dict = {
    "version": "1.0",
    "description": "Initial enterprise governance policy for agentic SDLC.",
    "quality": {
        "development": {
            "threshold": 0.95,
            "objective": {
                "test_pass_rate": {
                    "weight": 0.70,
                    "definition": "Share of governance-owned acceptance and regression tests that passed.",
                    "formula": "passed_tests / executed_tests",
                    "data_source": "functional_test_evaluator (pytest execution)",
                },
                "requirement_coverage": {
                    "weight": 0.20,
                    "definition": "Share of required ticket components detected in the change by the PR review evaluator.",
                    "formula": "implemented_components / required_components",
                    "data_source": "pr_review_evaluator",
                },
                "code_hygiene": {
                    "weight": 0.10,
                    "definition": "Linting and maintainability score.",
                    "formula": "max(0, 1 - lint_penalty * lint_findings - complexity_penalty * complex_functions)",
                    "data_source": "code_quality_evaluator (ruff + radon)",
                },
            },
            "mandatory_gates": {
                "all_acceptance_tests_pass": True,
                "max_high_security_findings": 0,
            },
        },
        "code_quality": {
            "lint_penalty": 0.1,
            "complexity_penalty": 0.15,
            "max_cyclomatic_complexity": 10,
            "max_duplicate_block_lines": 6,
            "ruff_select": ["E", "F", "W", "N", "B", "C901"],
        },
    },
    "loop": {
        "max_iterations": 5,
        "min_improvement": 0.02,
        "stagnation_window": 2,
        "regression_tolerance": 0.0,
        "max_consecutive_regressions": 2,
        "budget_usd": 2.00,
        "max_tokens": 250000,
        "cost_per_1k_tokens_usd": 0.012,
        "timeout_seconds": 300,
        "critical_security_stop": True,
    },
    "security": {
        "block_severities": ["CRITICAL"],
        "severity_points": {"CRITICAL": 50, "HIGH": 30, "MEDIUM": 10, "LOW": 2, "INFO": 0},
        "prefer_semgrep": True,
    },
    "risk": {
        "weights": {
            "code_change_impact": 0.20,
            "dependency_impact": 0.15,
            "security_exposure": 0.25,
            "business_criticality": 0.20,
            "infrastructure_impact": 0.20,
        },
        "levels": [
            {"name": "LOW", "min": 0, "max": 30},
            {"name": "MEDIUM", "min": 31, "max": 60},
            {"name": "HIGH", "min": 61, "max": 80},
            {"name": "CRITICAL", "min": 81, "max": 100},
        ],
        "level_requirements": {
            "LOW": {"decision": "CONTINUE", "required_approvals": [], "additional_validation": []},
            "MEDIUM": {
                "decision": "HUMAN_APPROVAL_REQUIRED",
                "required_approvals": ["reviewer"],
                "additional_validation": ["extended_regression_suite"],
            },
            "HIGH": {
                "decision": "HUMAN_APPROVAL_REQUIRED",
                "required_approvals": ["tech_lead"],
                "additional_validation": ["extended_regression_suite"],
            },
            "CRITICAL": {
                "decision": "HUMAN_APPROVAL_REQUIRED",
                "required_approvals": ["tech_lead", "security_lead"],
                "additional_validation": ["extended_regression_suite", "security_review"],
            },
        },
        "path_classes": {
            "auth": ["auth/", "authentication", "token_validator", "oauth"],
            "payment": ["payment", "billing", "ledger"],
            "db_schema": ["migrations/", "schema", ".sql"],
            "infrastructure": ["terraform/", "k8s/", "helm/", "Dockerfile", ".github/workflows/"],
            "config": ["config/", ".yaml", ".yml", ".env"],
            "api": ["api/", "routes", "endpoint"],
            "docs": ["docs/", "README", ".md"],
            "tests": ["tests/", "test_"],
        },
        "factors": {
            "code_change_impact": {
                "points_per_file": 6,
                "max_file_points": 30,
                "lines_changed_tiers": [[400, 30], [150, 20], [50, 10]],
                "api_change": 25,
                "unrelated_change": 10,
            },
            "dependency_impact": {
                "new_dependency": 20,
                "version_change": 10,
                "vulnerable_dependency": 40,
                "per_downstream_service": 8,
                "max_downstream_points": 40,
            },
            "security_exposure": {
                "auth_change": 45,
                "payment_change": 35,
                "sensitive_data": 15,
                "findings_multiplier": 1.0,
            },
            "business_criticality": {
                "levels": {"low": 20, "medium": 45, "high": 70, "critical": 90},
                "per_historical_incident": 10,
                "max_incident_points": 30,
            },
            "infrastructure_impact": {
                "db_schema_change": 45,
                "infrastructure_change": 40,
                "config_change": 15,
            },
        },
        "mandatory_overrides": {
            "critical_security_finding": "BLOCK",
            "protected_path_modified_by_agent": "BLOCK",
        },
        "protected_paths": ["governance/", "policies/", ".github/CODEOWNERS", "branch_protection"],
    },
    "health": {
        "rollout_steps": [5, 25, 50, 100],
        "observation_window_samples": 400,
        "thresholds": {
            "error_rate_pct": {"max": 1.0, "label": "Error rate", "unit": "%"},
            "p95_latency_ms": {"max": 800, "label": "p95 latency", "unit": "ms"},
            "p99_latency_ms": {"max": 1500, "label": "p99 latency", "unit": "ms"},
            "cpu_utilization_pct": {"max": 80, "label": "CPU utilization", "unit": "%"},
            "memory_utilization_pct": {"max": 85, "label": "Memory utilization", "unit": "%"},
            "availability_pct": {"min": 99.9, "label": "Availability", "unit": "%"},
            "slo_compliance_pct": {"min": 99.0, "label": "SLO compliance", "unit": "%"},
            "business_txn_success_pct": {"min": 98.5, "label": "Business transaction success", "unit": "%"},
        },
        "breach_action": "ROLLBACK",
        "sev1_metrics": ["availability_pct", "business_txn_success_pct", "error_rate_pct"],
    },
    "feedback": {
        "min_incidents_for_proposal": 2,
        "lookback_days": 180,
        "factor_increase_points": 15,
        "category_to_policy_path": {
            "db_schema_change": "risk.factors.infrastructure_impact.db_schema_change",
            "auth_change": "risk.factors.security_exposure.auth_change",
            "payment_change": "risk.factors.security_exposure.payment_change",
            "dependency_change": "risk.factors.dependency_impact.new_dependency",
            "infrastructure_change": "risk.factors.infrastructure_impact.infrastructure_change",
            "config_change": "risk.factors.infrastructure_impact.config_change",
            "api_change": "risk.factors.code_change_impact.api_change",
        },
    },
    "gateway": {
        "replay_enabled": True,
        "prices_per_1k_tokens_usd": {
            "simulated-deterministic-v1": {"input": 0.003, "output": 0.015},
            "simulated-judge-v1": {"input": 0.001, "output": 0.005},
        },
    },
    "harness": {
        "default_budget_usd": 1.0,
        "max_retries": 2,
        "promotion_max_case_regressions": 0,
        "promotion_approval_roles": ["tech_lead", "governance_admin"],
    },
    "rbac": {
        "approval_roles": ["reviewer", "tech_lead", "security_lead"],
        "incident_roles": ["sre_oncall"],
        "policy_admin_roles": ["governance_admin"],
        "release_trigger_roles": ["engineer", "tech_lead", "release_manager"],
    },
}
