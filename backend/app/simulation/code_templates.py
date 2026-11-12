"""Deterministic code templates used by the simulated Coding / Fix Agent.

The generated module is *real* Python that is executed by the Functional Test
Evaluator against the governance-owned acceptance suite, linted by Ruff and scanned
by the security rules. Defects are injected as code variants so that every score
the engine reports is computed from real tool output, never from a lookup table.

Defect catalogue (ids are used only by the simulated agent, never by evaluators):

  D1  missing authentication / ownership checks
  D2  pagination off-by-one and missing bounds checks
  D3  no response / record validation
  D4  SQL built with string interpolation (injection)
  D5  hardcoded live credential
  D6  lint problems (unused imports, == None, camelCase helper)
  D7  endpoint documentation missing
  D8  unrelated file modified
  D9  regression: default page size changed
  D10 agent modified a protected governance file
  D11 vulnerable dependency pinned
"""

from __future__ import annotations

ALL_DEFECTS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11"]

DEFECT_DESCRIPTIONS = {
    "D1": "Authentication and ownership checks",
    "D2": "Pagination offset and bounds",
    "D3": "Response and record validation",
    "D4": "Parameterised SQL",
    "D5": "Credential handling",
    "D6": "Lint hygiene",
    "D7": "Endpoint documentation",
    "D8": "Scope of change",
    "D9": "Default page size regression",
    "D10": "Protected governance file",
    "D11": "Dependency version",
}


HEADER = '''"""Customer transaction history API handler.

GET /customers/{customer_id}/transactions

Implements TICKET-104: bearer authentication, ownership authorization,
pagination and response validation.
"""

from __future__ import annotations

'''

IMPORTS_CLEAN = "import math\n"
IMPORTS_LINT = "import json\nimport math\nimport os\n"
IMPORT_AUTH = "\nfrom baseline.auth import verify_token\n"

CONSTANTS = '''
DEFAULT_PAGE_SIZE = __DEFAULT_PAGE_SIZE__
MAX_PAGE_SIZE = 100
COLUMNS = ("id", "amount_cents", "currency", "created_at", "description")
ITEM_SCHEMA = {"id": str, "amount_cents": int, "currency": str, "created_at": str, "description": str}
'''

# Fake demo credential, assembled at runtime so the repository itself never contains a
# key-shaped literal (and secret scanners on the repo are not triggered).
SECRET = 'LEDGER_API_KEY = "' + "sk_" + "live_" + "51HxQe2KZvTnq8YwB7mLp0Rr4" + '"\n'

ERROR_FN = '''

def _error(status: int, message: str) -> dict:
    return {"status": status, "body": {"error": message}}
'''

AUTH_FN = '''

def authenticate(headers: dict) -> str | None:
    """Return the customer id bound to the bearer token, or None if it is missing or invalid."""
    auth_header = headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return verify_token(auth_header[len("Bearer "):])
'''

PAGINATION_GOOD = '''

def parse_pagination(query: dict) -> tuple[int, int] | None:
    """Return (page, page_size) or None when the parameters are invalid."""
    try:
        page = int(query.get("page", 1))
        page_size = int(query.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return None
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        return None
    return page, page_size
'''

PAGINATION_BAD = '''

def parse_pagination(query: dict) -> tuple[int, int] | None:
    """Return (page, page_size)."""
    try:
        page = int(query.get("page", 1))
        page_size = int(query.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return None
    return page, page_size
'''

FETCH_PARAM = '''

PAGE_SQL = (
    "SELECT id, amount_cents, currency, created_at, description FROM transactions "
    "WHERE customer_id = ?__VALID__ ORDER BY created_at DESC LIMIT ? OFFSET ?"
)
COUNT_SQL = "SELECT COUNT(*) FROM transactions WHERE customer_id = ?__VALID__"


def fetch_page(conn, customer_id: str, page: int, page_size: int) -> tuple[list[dict], int]:
    """Fetch one page of transactions using parameterised queries."""
    offset = __OFFSET__
    rows = conn.execute(PAGE_SQL, (customer_id, page_size, offset)).fetchall()
    total = conn.execute(COUNT_SQL, (customer_id,)).fetchone()[0]__NONECHECK__
    return [dict(zip(COLUMNS, row, strict=True)) for row in rows], total
'''

FETCH_CONCAT = '''

def fetch_page(conn, customer_id: str, page: int, page_size: int) -> tuple[list[dict], int]:
    """Fetch one page of transactions."""
    offset = __OFFSET__
    query = "SELECT id, amount_cents, currency, created_at, description FROM transactions WHERE customer_id = '" + customer_id + "'__VALID__ ORDER BY created_at DESC LIMIT " + str(page_size) + " OFFSET " + str(offset)
    rows = conn.execute(query).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM transactions WHERE customer_id = '" + customer_id + "'__VALID__").fetchone()[0]__NONECHECK__
    return [dict(zip(COLUMNS, row, strict=True)) for row in rows], total
'''

NONECHECK_LINT = '''
    if total == None:
        total = 0'''

LINT_HELPER = '''

def formatAmount(cents):
    return "%.2f" % (cents / 100)
'''

VALIDATE_FN = '''

def validate_item(item: dict) -> bool:
    """Check a response item against ITEM_SCHEMA before it leaves the service."""
    return all(isinstance(item.get(field), typ) for field, typ in ITEM_SCHEMA.items())
'''

HANDLER_TEMPLATE = '''

def get_transaction_history(request: dict, conn) -> dict:
    """Handle GET /customers/{customer_id}/transactions."""
__AUTH_BLOCK____CUSTOMER_BLOCK____OWNER_BLOCK__    pagination = parse_pagination(request.get("query", {}))
    if pagination is None:
        return _error(400, "invalid pagination parameters")
    page, page_size = pagination
    items, total = fetch_page(conn, customer_id, page, page_size)
__VALIDATE_BLOCK__    return {
        "status": 200,
        "body": {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if total else 0,
        },
    }
'''

AUTH_BLOCK = '''    caller = authenticate(request.get("headers", {}))
    if caller is None:
        return _error(401, "authentication required")
'''

CUSTOMER_BLOCK_GOOD = '''    customer_id = request.get("path", {}).get("customer_id", "")
    if not (isinstance(customer_id, str) and customer_id.startswith("CUST-") and customer_id[5:].isdigit()):
        return _error(400, "invalid customer_id")
'''

CUSTOMER_BLOCK_BAD = '''    customer_id = request.get("path", {}).get("customer_id", "")
'''

OWNER_BLOCK = '''    if caller != customer_id:
        return _error(403, "forbidden")
'''

VALIDATE_BLOCK = '''    items = [item for item in items if validate_item(item)]
'''


def render_handler_module(defects: set[str], review_notes: list[str] | None = None) -> str:
    imports = IMPORTS_LINT if "D6" in defects else IMPORTS_CLEAN
    if "D1" not in defects:
        imports += IMPORT_AUTH
    constants = CONSTANTS.replace("__DEFAULT_PAGE_SIZE__", "10" if "D9" in defects else "20")
    if "D5" in defects:
        constants += SECRET
    parts = [HEADER, imports, constants, ERROR_FN]
    if "D1" not in defects:
        parts.append(AUTH_FN)
    parts.append(PAGINATION_BAD if "D2" in defects else PAGINATION_GOOD)

    fetch = FETCH_CONCAT if "D4" in defects else FETCH_PARAM
    fetch = fetch.replace("__OFFSET__", "page * page_size" if "D2" in defects else "(page - 1) * page_size")
    fetch = fetch.replace("__VALID__", "" if "D3" in defects else " AND amount_cents IS NOT NULL")
    fetch = fetch.replace("__NONECHECK__", NONECHECK_LINT if "D6" in defects else "")
    parts.append(fetch)
    if "D6" in defects:
        parts.append(LINT_HELPER)
    if "D3" not in defects:
        parts.append(VALIDATE_FN)

    handler = HANDLER_TEMPLATE
    handler = handler.replace("__AUTH_BLOCK__", "" if "D1" in defects else AUTH_BLOCK)
    handler = handler.replace("__CUSTOMER_BLOCK__", CUSTOMER_BLOCK_BAD if "D3" in defects else CUSTOMER_BLOCK_GOOD)
    handler = handler.replace("__OWNER_BLOCK__", "" if "D1" in defects else OWNER_BLOCK)
    handler = handler.replace("__VALIDATE_BLOCK__", "" if "D3" in defects else VALIDATE_BLOCK)
    parts.append(handler)
    module = "".join(parts)
    if review_notes:
        notes = "".join(f"# Review follow-up: {n}\n" for n in review_notes)
        module = module.rstrip("\n") + "\n\n\n" + notes
    return module


DOCS = """# Transaction history API

`GET /customers/{customer_id}/transactions`

| Parameter | Location | Notes |
|---|---|---|
| `Authorization` | header | `Bearer <token>`; required (401 otherwise) |
| `customer_id` | path | `CUST-<digits>`; must match the token owner (403 otherwise) |
| `page` | query | >= 1, default 1 |
| `page_size` | query | 1-100, default 20 |

Response items are validated against the transaction schema
(`id`, `amount_cents`, `currency`, `created_at`, `description`). Results are ordered newest first.
"""

MIGRATION = """-- 0042: support newest-first pagination for the transaction history endpoint
CREATE INDEX IF NOT EXISTS idx_transactions_customer_created
    ON transactions (customer_id, created_at DESC);
"""

ROUTES_BASE = '''"""HTTP route registry."""

ROUTES = {
    "GET /customers/{customer_id}": "service.customers.get_customer",
}
'''

ROUTES_NEW = '''"""HTTP route registry."""

ROUTES = {
    "GET /customers/{customer_id}": "service.customers.get_customer",
    "GET /customers/{customer_id}/transactions": "service.transaction_history.get_transaction_history",
}
'''

SCOPES_BASE = '''"""OAuth scopes understood by the API gateway."""

SCOPES = {"customers:read": "Read customer profile"}
'''

SCOPES_NEW = '''"""OAuth scopes understood by the API gateway."""

SCOPES = {
    "customers:read": "Read customer profile",
    "transactions:read": "Read the caller's own transaction history",
}
'''

TOKEN_VALIDATOR_BASE = '''"""Token validation for the API gateway."""

ACCEPTED_ISSUERS = ("https://idp.example.internal",)
CLOCK_SKEW_SECONDS = 30


def is_issuer_trusted(issuer: str) -> bool:
    return issuer in ACCEPTED_ISSUERS
'''

TOKEN_VALIDATOR_NEW = '''"""Token validation for the API gateway."""

ACCEPTED_ISSUERS = ("https://idp.example.internal", "https://idp-payments.example.internal")
CLOCK_SKEW_SECONDS = 60


def is_issuer_trusted(issuer: str) -> bool:
    return issuer in ACCEPTED_ISSUERS
'''

LEDGER_BASE = '''"""Client for the payments ledger service."""

LEDGER_TIMEOUT_SECONDS = 2.0


def ledger_endpoint(region: str) -> str:
    return f"https://ledger.{region}.example.internal/v1"
'''

LEDGER_NEW = '''"""Client for the payments ledger service."""

LEDGER_TIMEOUT_SECONDS = 2.0
LEDGER_HISTORY_PAGE_LIMIT = 100


def ledger_endpoint(region: str) -> str:
    return f"https://ledger.{region}.example.internal/v1"


def ledger_history_endpoint(region: str) -> str:
    return ledger_endpoint(region) + "/transactions"
'''

LEGACY_FORMATTER_BASE = '''"""Legacy formatting helpers (owned by the statements team)."""


def format_statement_line(amount: int, currency: str) -> str:
    return f"{currency} {amount / 100:.2f}"
'''

LEGACY_FORMATTER_CHANGED = '''"""Legacy formatting helpers (owned by the statements team)."""


def format_statement_line(amount: int, currency: str) -> str:
    return f"{currency} {amount / 100:,.2f}"
'''

REQUIREMENTS_BASE = "fastapi==0.115.0\npydantic==2.9.2\n"

POLICY_OVERRIDE = """# Added by coding agent to unblock its own pipeline
risk:
  weights:
    security_exposure: 0.0
"""

FEATURE_FLAGS_BASE = """flags:
  ff_customer_profile_v2: {owner: accounts, default: true}
"""

FEATURE_FLAGS = """flags:
  ff_customer_profile_v2: {owner: accounts, default: true}
  ff_txn_history_api: {owner: payments-platform, default: false, rollout: canary}
"""

HELM_VALUES_BASE = """egress:
  allow:
    - transactions-db
"""

HELM_VALUES = """egress:
  allow:
    - transactions-db
    - ledger-service
"""

BASE_REPO = {
    "config/feature_flags.yaml": FEATURE_FLAGS_BASE,
    "helm/txn-history-api/values.yaml": HELM_VALUES_BASE,
    "service/api/routes.py": ROUTES_BASE,
    "service/auth/scopes.py": SCOPES_BASE,
    "service/auth/token_validator.py": TOKEN_VALIDATOR_BASE,
    "service/payments/ledger_client.py": LEDGER_BASE,
    "service/utils/legacy_formatter.py": LEGACY_FORMATTER_BASE,
    "requirements.txt": REQUIREMENTS_BASE,
}
