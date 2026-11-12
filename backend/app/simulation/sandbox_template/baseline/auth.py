"""Existing (baseline) token verification service used by the generated endpoint."""

_TOKENS = {
    "tok-alice": "CUST-001",
    "tok-bob": "CUST-002",
    "tok-carol": "CUST-003",
}


def verify_token(token: str) -> str | None:
    """Return the customer id bound to ``token`` or ``None`` when the token is invalid."""
    return _TOKENS.get(token)
