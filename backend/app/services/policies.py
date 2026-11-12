"""Versioned policy store."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.governance.default_policy import DEFAULT_POLICY
from app.models import PolicyVersion


def active_policy(db: Session) -> dict[str, Any]:
    pv = db.execute(select(PolicyVersion).where(PolicyVersion.active.is_(True))).scalar_one_or_none()
    if pv is None:
        pv = PolicyVersion(version=DEFAULT_POLICY["version"], policy=copy.deepcopy(DEFAULT_POLICY), active=True,
                           created_by="system-governance", change_summary="Initial policy")
        db.add(pv)
        db.flush()
    policy = copy.deepcopy(pv.policy)
    for key, value in DEFAULT_POLICY.items():  # sections added after this version was stored
        policy.setdefault(key, copy.deepcopy(value))
    return policy


def get_path(policy: dict[str, Any], path: str) -> Any:
    node: Any = policy
    for key in path.split("."):
        node = node[key]
    return node


def set_path(policy: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    node = policy
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value


def bump_version(version: str) -> str:
    major, minor = (version.split(".") + ["0"])[:2]
    return f"{major}.{int(minor) + 1}"


def create_version(db: Session, *, changes: list[dict[str, Any]], created_by: str, summary: str,
                   proposal_id: str | None = None) -> PolicyVersion:
    current = db.execute(select(PolicyVersion).where(PolicyVersion.active.is_(True))).scalar_one()
    new_policy = copy.deepcopy(current.policy)
    for ch in changes:
        set_path(new_policy, ch["path"], ch["new_value"])
    new_version = bump_version(current.version)
    new_policy["version"] = new_version
    current.active = False
    pv = PolicyVersion(version=new_version, policy=new_policy, active=True, created_by=created_by,
                       change_summary=summary, changes=changes, proposal_id=proposal_id)
    db.add(pv)
    db.flush()
    return pv
