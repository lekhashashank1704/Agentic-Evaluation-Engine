"""Synthetic, seeded runtime telemetry for canary and baseline versions.

Metrics are *computed* from generated request samples (percentiles, rates) rather
than being fixed numbers, so thresholds and statistics are exercised for real.
All output is labelled as simulated.
"""

from __future__ import annotations

import math
import random
from typing import Any

PROFILES = {
    # median latency (ms), lognormal sigma, error probability, cpu mean, mem mean, biz failure prob
    "baseline": {"median": 210, "sigma": 0.45, "error_p": 0.002, "cpu": 41, "mem": 58, "biz_fail": 0.004,
                 "outage_p": 0.0},
    "healthy": {"median": 220, "sigma": 0.48, "error_p": 0.003, "cpu": 44, "mem": 61, "biz_fail": 0.005,
                "outage_p": 0.0},
    "unhealthy": {"median": 520, "sigma": 0.62, "error_p": 0.044, "cpu": 83, "mem": 79, "biz_fail": 0.06,
                  "outage_p": 0.004},
}

SLO_LATENCY_MS = 1000


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_values[int(k)]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def generate(profile: str, samples: int, seed: int, traffic_percent: int) -> dict[str, Any]:
    p = PROFILES[profile]
    rng = random.Random(f"{profile}-{seed}-{traffic_percent}")
    latencies, errors, slo_ok, biz_ok, unavailable = [], 0, 0, 0, 0
    series = []
    bucket = max(1, samples // 20)
    b_lat, b_err = [], 0
    # a breaching canary degrades further as load increases
    load_factor = 1 + (traffic_percent / 100) * (0.5 if profile == "unhealthy" else 0.05)
    for i in range(samples):
        lat = rng.lognormvariate(math.log(p["median"] * load_factor), p["sigma"])
        if profile == "unhealthy" and rng.random() < 0.03:
            lat *= 4.5  # tail amplification from lock contention
        is_down = rng.random() < p["outage_p"]
        is_err = is_down or rng.random() < p["error_p"] * load_factor
        latencies.append(lat)
        errors += is_err
        unavailable += is_down
        slo_ok += (not is_err) and lat <= SLO_LATENCY_MS
        biz_ok += (not is_err) and rng.random() >= p["biz_fail"]
        b_lat.append(lat)
        b_err += is_err
        if (i + 1) % bucket == 0:
            s = sorted(b_lat)
            series.append({"t": len(series) * 15, "p95_latency_ms": round(_percentile(s, 95)),
                           "p99_latency_ms": round(_percentile(s, 99)),
                           "error_rate_pct": round(b_err / len(b_lat) * 100, 2)})
            b_lat, b_err = [], 0
    s = sorted(latencies)
    return {
        "requests": samples,
        "error_rate_pct": round(errors / samples * 100, 2),
        "p95_latency_ms": round(_percentile(s, 95)),
        "p99_latency_ms": round(_percentile(s, 99)),
        "cpu_utilization_pct": round(min(100, rng.gauss(p["cpu"], 2) * (1 + traffic_percent / 400)), 1),
        "memory_utilization_pct": round(min(100, rng.gauss(p["mem"], 1.5)), 1),
        "availability_pct": round((1 - unavailable / samples) * 100, 3),
        "slo_compliance_pct": round(slo_ok / samples * 100, 2),
        "business_txn_success_pct": round(biz_ok / samples * 100, 2),
        "series": series,
        "simulated": True,
        "profile": profile,
    }


def sample_logs(profile: str, version: str, seed: int, n: int = 8) -> list[dict[str, Any]]:
    rng = random.Random(f"logs-{profile}-{seed}")
    if profile != "unhealthy":
        return [{"level": "INFO", "message": f"{version} GET /customers/CUST-*/transactions 200",
                 "simulated": True} for _ in range(3)]
    messages = [
        ("ERROR", "sqlite3.OperationalError: database is locked (idx_transactions_customer_created build)"),
        ("ERROR", "upstream timeout calling ledger-service after 2000ms"),
        ("WARN", "connection pool exhausted (max=20) for transactions-db"),
        ("ERROR", "GET /customers/CUST-*/transactions 503 Service Unavailable"),
        ("WARN", "p99 latency 2.7s exceeds SLO 1.5s"),
    ]
    out = []
    for i in range(n):
        level, msg = messages[rng.randrange(len(messages))]
        out.append({"ts_offset_s": i * 7, "level": level, "message": f"[{version}] {msg}", "simulated": True})
    return out
