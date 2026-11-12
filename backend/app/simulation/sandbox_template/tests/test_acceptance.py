"""Governance-owned acceptance suite for TICKET-104.

This suite is owned by the evaluation engine, not by the coding agent. The agent
cannot modify it; the evaluator copies it into an isolated sandbox for each run.
Test names carry the acceptance criterion id (ac1..ac4, reg) for coverage mapping.
"""

import pytest

from baseline.db import make_db
from service.transaction_history import get_transaction_history


@pytest.fixture()
def conn():
    return make_db()


def call(conn, customer_id="CUST-001", token="tok-alice", **query):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return get_transaction_history({"headers": headers, "path": {"customer_id": customer_id}, "query": query}, conn)


# AC-1 authentication -----------------------------------------------------------
def test_ac1_missing_token_returns_401(conn):
    assert call(conn, token=None)["status"] == 401


def test_ac1_invalid_token_returns_401(conn):
    assert call(conn, token="tok-forged")["status"] == 401


def test_ac1_valid_token_returns_200(conn):
    assert call(conn)["status"] == 200


# AC-2 authorization ------------------------------------------------------------
def test_ac2_other_customer_returns_403(conn):
    assert call(conn, customer_id="CUST-002", token="tok-alice")["status"] == 403


# AC-3 pagination ---------------------------------------------------------------
def test_ac3_first_page_returns_newest(conn):
    body = call(conn, page=1, page_size=5)["body"]
    assert [i["id"] for i in body["items"]] == ["TX-A023", "TX-A022", "TX-A021", "TX-A020", "TX-A019"]


def test_ac3_second_page_offset(conn):
    body = call(conn, page=2, page_size=5)["body"]
    assert body["items"][0]["id"] == "TX-A018"


def test_ac3_total_pages(conn):
    body = call(conn, page=1, page_size=10)["body"]
    assert body["total"] == 23 and body["total_pages"] == 3


def test_ac3_page_size_limit_enforced(conn):
    assert call(conn, page=1, page_size=500)["status"] == 400


def test_ac3_invalid_page_rejected(conn):
    assert call(conn, page=0)["status"] == 400


# AC-4 response validation ------------------------------------------------------
def test_ac4_items_match_schema(conn):
    body = call(conn, page=1, page_size=100)["body"]
    schema = {"id": str, "amount_cents": int, "currency": str, "created_at": str, "description": str}
    assert body["items"], "expected items"
    for item in body["items"]:
        for field, typ in schema.items():
            assert isinstance(item.get(field), typ), f"{item.get('id')}.{field}"


def test_ac4_invalid_customer_id_returns_400(conn):
    assert call(conn, customer_id="1 OR 1=1", token="tok-alice")["status"] == 400


# REG existing behaviour --------------------------------------------------------
def test_reg_empty_history_returns_empty_list(conn):
    body = call(conn, customer_id="CUST-003", token="tok-carol")["body"]
    assert body["items"] == [] and body["total"] == 0


def test_reg_default_page_size(conn):
    body = call(conn)["body"]
    assert body["page_size"] == 20 and len(body["items"]) == 20
