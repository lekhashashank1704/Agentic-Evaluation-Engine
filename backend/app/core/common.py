"""Hashing, id sequences, audit trail (hash chained) and notifications."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEntry, Counter, Notification
from app.schemas import utcnow

GENESIS_HASH = "0" * 64


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def artifact_hash(files: dict[str, str], manifest: dict[str, Any]) -> str:
    """Deterministic SHA-256 over the artifact content (files + change manifest)."""
    canonical = json.dumps({"files": files, "manifest": manifest}, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def next_id(db: Session, prefix: str, width: int = 4) -> str:
    counter = db.get(Counter, prefix)
    if counter is None:
        counter = Counter(name=prefix, value=0)
        db.add(counter)
    counter.value += 1
    db.flush()
    return f"{prefix}-{counter.value:0{width}d}"


def audit(
    db: Session,
    *,
    actor_id: str,
    actor_kind: str,
    actor_role: str,
    action: str,
    entity_type: str,
    entity_id: str,
    outcome: str = "ALLOWED",
    artifact_hash: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEntry:
    """Append a tamper-evident audit entry. Each entry hashes the previous entry's hash."""
    last = db.execute(select(AuditEntry).order_by(AuditEntry.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.entry_hash if last else GENESIS_HASH
    ts = utcnow()
    details = details or {}
    payload = json.dumps(
        {
            "prev": prev,
            "ts": ts.isoformat(),
            "actor": actor_id,
            "action": action,
            "entity": f"{entity_type}:{entity_id}",
            "outcome": outcome,
            "hash": artifact_hash,
            "details": details,
        },
        sort_keys=True,
        default=str,
    )
    entry = AuditEntry(
        ts=ts,
        actor_id=actor_id,
        actor_kind=actor_kind,
        actor_role=actor_role,
        action=action,
        outcome=outcome,
        entity_type=entity_type,
        entity_id=entity_id,
        artifact_hash=artifact_hash,
        details=details,
        prev_hash=prev,
        entry_hash=sha256_text(payload),
    )
    db.add(entry)
    db.flush()
    return entry


def verify_audit_chain(db: Session) -> dict[str, Any]:
    entries = db.execute(select(AuditEntry).order_by(AuditEntry.id)).scalars().all()
    prev = GENESIS_HASH
    for e in entries:
        if e.prev_hash != prev:
            return {"valid": False, "broken_at": e.id, "entries": len(entries)}
        prev = e.entry_hash
    return {"valid": True, "entries": len(entries), "head": prev}


def notify(
    db: Session, *, recipient_role: str, subject: str, body: str, entity_type: str, entity_id: str,
    recipient_id: str | None = None, channel: str = "simulated-pager",
) -> Notification:
    n = Notification(
        recipient_role=recipient_role, recipient_id=recipient_id, channel=channel, subject=subject,
        body=body, entity_type=entity_type, entity_id=entity_id,
    )
    db.add(n)
    db.flush()
    return n
