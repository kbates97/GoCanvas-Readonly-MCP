"""GoCanvas read-only MCP server.

Exposes read-only (GET) endpoints of the GoCanvas API v3 as MCP tools, scoped to
Forms, Submissions, Reports, and Reference Data.

Authentication is provided via environment variables, in priority order:
  * GOCANVAS_CLIENT_ID + GOCANVAS_CLIENT_SECRET -> OAuth 2.0 client-credentials.
        A short-lived bearer token is fetched from /oauth/token on startup,
        cached until it expires, and re-fetched automatically (or on a 401).
  * GOCANVAS_API_TOKEN                          -> static Bearer token.
  * GOCANVAS_USERNAME + GOCANVAS_PASSWORD       -> HTTP Basic auth (fallback).

Optional environment variables:
  * GOCANVAS_BASE_URL   (default: https://api.gocanvas.com/api/v3)
  * GOCANVAS_OAUTH_SCOPE (default: unset)         scope requested for the token
  * GOCANVAS_PDF_DIR    (default: ./pdf_output)  directory for downloaded PDFs
  * GOCANVAS_TIMEOUT    (default: 30)            HTTP timeout in seconds
"""

from __future__ import annotations

import base64
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("GOCANVAS_BASE_URL", "https://api.gocanvas.com/api/v3").rstrip("/")
API_TOKEN = os.environ.get("GOCANVAS_API_TOKEN")
CLIENT_ID = os.environ.get("GOCANVAS_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOCANVAS_CLIENT_SECRET")
OAUTH_SCOPE = os.environ.get("GOCANVAS_OAUTH_SCOPE")
USERNAME = os.environ.get("GOCANVAS_USERNAME")
PASSWORD = os.environ.get("GOCANVAS_PASSWORD")
PDF_DIR = os.environ.get("GOCANVAS_PDF_DIR", "./pdf_output")
TIMEOUT = float(os.environ.get("GOCANVAS_TIMEOUT", "30"))

# Refresh an OAuth token this many seconds before its stated expiry.
_TOKEN_EXPIRY_SKEW = 60.0

# Pagination-related response headers surfaced back to the caller.
_PAGINATION_HEADERS = (
    "link",
    "current-page",
    "page-items",
    "total-count",
    "total-pages",
)

# Rate-limit retry configuration.
_MAX_RETRIES = 5
_DEFAULT_BACKOFF = 60.0  # seconds, per GoCanvas best-practice guidance
_MAX_BACKOFF = 300.0

mcp = FastMCP("gocanvas")

# In-memory OAuth token cache: {"access_token", "token_type", "expires_at",
# "scope", "created_at"}. Populated lazily / on demand via /oauth/token.
_token_state: dict[str, Any] = {}


def _oauth_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _fetch_oauth_token() -> dict[str, Any]:
    """Request a fresh bearer token from /oauth/token (client-credentials grant).

    Updates the in-memory cache and returns the raw token payload.
    """
    if not _oauth_configured():
        raise RuntimeError(
            "OAuth is not configured. Set GOCANVAS_CLIENT_ID and GOCANVAS_CLIENT_SECRET."
        )
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    if OAUTH_SCOPE:
        data["scope"] = OAUTH_SCOPE

    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(
            f"{BASE_URL}/oauth/token",
            data=data,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()

    expires_in = payload.get("expires_in")
    now = time.time()
    _token_state.clear()
    _token_state.update(
        {
            "access_token": payload.get("access_token"),
            "token_type": payload.get("token_type", "Bearer"),
            "scope": payload.get("scope"),
            "created_at": payload.get("created_at"),
            "expires_at": now + float(expires_in) if expires_in else None,
        }
    )
    return payload


def _ensure_oauth_token(force: bool = False) -> str:
    """Return a valid cached access token, fetching a new one if needed."""
    token = _token_state.get("access_token")
    expires_at = _token_state.get("expires_at")
    valid = (
        token
        and not force
        and (expires_at is None or time.time() < expires_at - _TOKEN_EXPIRY_SKEW)
    )
    if not valid:
        _fetch_oauth_token()
        token = _token_state.get("access_token")
    if not token:
        raise RuntimeError("Failed to obtain an OAuth access token.")
    return token


def _auth_headers() -> dict[str, str]:
    """Build the Authorization header from configured credentials."""
    if _oauth_configured():
        return {"Authorization": f"Bearer {_ensure_oauth_token()}"}
    if API_TOKEN:
        return {"Authorization": f"Bearer {API_TOKEN}"}
    if USERNAME and PASSWORD:
        raw = f"{USERNAME}:{PASSWORD}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
    raise RuntimeError(
        "No GoCanvas credentials configured. Set GOCANVAS_CLIENT_ID and "
        "GOCANVAS_CLIENT_SECRET (OAuth), GOCANVAS_API_TOKEN, or both "
        "GOCANVAS_USERNAME and GOCANVAS_PASSWORD."
    )


def _clean_params(params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Drop parameters whose value is None."""
    if not params:
        return {}
    return {k: v for k, v in params.items() if v is not None}


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Compute how long to wait before retrying after a 429.

    Honors RateLimit-Reset (UTC epoch seconds) and RateLimit-Remaining headers,
    falling back to a bounded exponential backoff.
    """
    reset = response.headers.get("RateLimit-Reset") or response.headers.get("ratelimit-reset")
    remaining = response.headers.get("RateLimit-Remaining") or response.headers.get(
        "ratelimit-remaining"
    )
    if reset is not None and (remaining is None or remaining == "0"):
        try:
            wait = float(reset) - time.time()
            if wait > 0:
                return min(wait, _MAX_BACKOFF)
        except ValueError:
            pass
    # Exponential backoff starting at the recommended one-minute minimum.
    return min(_DEFAULT_BACKOFF * (2 ** attempt), _MAX_BACKOFF)


def _pagination_meta(response: httpx.Response) -> dict[str, str]:
    meta: dict[str, str] = {}
    for name in _PAGINATION_HEADERS:
        value = response.headers.get(name)
        if value is not None:
            meta[name] = value
    return meta


def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    accept: str = "application/json",
) -> httpx.Response:
    """Perform an authenticated request with rate-limit handling.

    Returns the raw httpx.Response so callers can handle JSON or binary bodies.
    Raises for non-429 error statuses.
    """
    url = f"{BASE_URL}{path}"
    query = _clean_params(params)
    with httpx.Client(timeout=TIMEOUT) as client:
        refreshed_on_401 = False
        for attempt in range(_MAX_RETRIES + 1):
            request_headers = {"Accept": accept, **_auth_headers()}
            response = client.request(method, url, params=query, headers=request_headers)
            if response.status_code == 429 and attempt < _MAX_RETRIES:
                time.sleep(_retry_delay(response, attempt))
                continue
            # On a 401 with OAuth, force-refresh the token once and retry.
            if (
                response.status_code == 401
                and _oauth_configured()
                and not refreshed_on_401
            ):
                refreshed_on_401 = True
                _ensure_oauth_token(force=True)
                continue
            response.raise_for_status()
            return response
    # Exhausted retries on repeated 429s.
    response.raise_for_status()
    return response


def _get_json(path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """GET a JSON endpoint and wrap the body with pagination metadata."""
    response = _request("GET", path, params=params, accept="application/json")
    try:
        body = response.json()
    except ValueError:
        body = response.text
    result: dict[str, Any] = {"data": body}
    meta = _pagination_meta(response)
    if meta:
        result["pagination"] = meta
    return result


def _download_pdf(path: str, filename_hint: str) -> dict[str, Any]:
    """GET a binary PDF endpoint, save it to disk, and return the saved path."""
    response = _request("GET", path, accept="application/pdf")
    out_dir = Path(PDF_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{filename_hint}-{uuid.uuid4().hex[:8]}.pdf"
    out_path = out_dir / filename
    out_path.write_bytes(response.content)
    return {
        "saved_path": str(out_path.resolve()),
        "content_type": response.headers.get("content-type", "application/pdf"),
        "size_bytes": len(response.content),
    }


# --------------------------------------------------------------------------- #
# Forms
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_forms(page: Optional[int] = None) -> dict[str, Any]:
    """List all Forms in the company.

    Args:
        page: Optional page number for pagination.
    """
    return _get_json("/forms", {"page": page})


@mcp.tool()
def get_form(form_id: int) -> dict[str, Any]:
    """Retrieve a single Form (including its full definition) by id.

    Args:
        form_id: The identifier of the Form.
    """
    return _get_json(f"/forms/{form_id}")


@mcp.tool()
def list_form_assigned_users(form_id: int) -> dict[str, Any]:
    """List the Users assigned to a Form.

    Args:
        form_id: The identifier of the Form.
    """
    return _get_json(f"/forms/{form_id}/assigned_users")


@mcp.tool()
def list_form_shared_departments(form_id: int) -> dict[str, Any]:
    """List the Departments a Form is shared with.

    Args:
        form_id: The identifier of the Form.
    """
    return _get_json(f"/forms/{form_id}/shared_departments")


# --------------------------------------------------------------------------- #
# Submissions
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_submissions(
    form_id: int,
    page: Optional[int] = None,
    department_id: Optional[int] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    hand_off: Optional[str] = None,
    custom_status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """List Submissions for a Form.

    Args:
        form_id: Required. The identifier for the Form associated with the Submissions.
        page: Optional page number for pagination.
        department_id: Filter by the Department associated with the Submission.
        user_id: Filter by the User who created the Submission.
        status: Status filter. One of: all, completed, deleted, in-progress, overdue,
            rejected, handed-off, assigned, unassigned, custom, saved-to-cloud, unfinished.
        hand_off: Workflow handoff state name (required when status is "handed-off").
        custom_status: Custom status label (required when status is "custom").
        start_date: DateTime lower bound for the Submission created_at.
        end_date: DateTime upper bound for the Submission created_at.
    """
    return _get_json(
        "/submissions",
        {
            "form_id": form_id,
            "page": page,
            "department_id": department_id,
            "user_id": user_id,
            "status": status,
            "hand_off": hand_off,
            "custom_status": custom_status,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@mcp.tool()
def get_submission(submission_id: str) -> dict[str, Any]:
    """Retrieve a single Submission (including its values) by GUID.

    Args:
        submission_id: The Submission GUID.
    """
    return _get_json(f"/submissions/{submission_id}")


@mcp.tool()
def list_submission_revisions(submission_id: int, page: Optional[int] = None) -> dict[str, Any]:
    """List the revision history of a Submission.

    Args:
        submission_id: The identifier of the Submission.
        page: Optional page number for pagination.
    """
    return _get_json(f"/submissions/{submission_id}/revisions", {"page": page})


@mcp.tool()
def get_submission_value(submission_id: str, value_id: str) -> dict[str, Any]:
    """Retrieve a single Value from a Submission (e.g. a media field).

    Args:
        submission_id: The Submission GUID.
        value_id: The identifier of the Value within the Submission.
    """
    return _get_json(f"/submissions/{submission_id}/values/{value_id}")


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_form_reports(form_id: int, page: Optional[int] = None) -> dict[str, Any]:
    """List the Report definitions associated with a Form.

    Args:
        form_id: The identifier of the Form.
        page: Optional page number for pagination.
    """
    return _get_json(f"/forms/{form_id}/reports", {"page": page})


@mcp.tool()
def get_form_report(form_id: int, report_id: int) -> dict[str, Any]:
    """Retrieve a single Report definition (including its full definition file) for a Form.

    Args:
        form_id: The identifier of the Form.
        report_id: The identifier of the Report definition.
    """
    return _get_json(f"/forms/{form_id}/reports/{report_id}")


@mcp.tool()
def get_submission_default_pdf(submission_id: int) -> dict[str, Any]:
    """Download the default Report PDF for a Submission and return the saved file path.

    Args:
        submission_id: The identifier of the Submission.
    """
    return _download_pdf(
        f"/submissions/{submission_id}/pdf", f"submission-{submission_id}-default"
    )


@mcp.tool()
def get_submission_report_pdf(submission_id: int, report_id: int) -> dict[str, Any]:
    """Generate and download a specific Report PDF for a Submission by Report id.

    Args:
        submission_id: The identifier of the Submission.
        report_id: The identifier of the Report definition to render.
    """
    return _download_pdf(
        f"/submissions/{submission_id}/reports/{report_id}",
        f"submission-{submission_id}-report-{report_id}",
    )


@mcp.tool()
def get_submission_standard_pdf(submission_id: int) -> dict[str, Any]:
    """Download the Standard Report PDF for a Submission and return the saved file path.

    Args:
        submission_id: The identifier of the Submission.
    """
    return _download_pdf(
        f"/submissions/{submission_id}/standard_pdf",
        f"submission-{submission_id}-standard",
    )


# --------------------------------------------------------------------------- #
# Reference Data
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_reference_data() -> dict[str, Any]:
    """List all Reference Data resources in the company."""
    return _get_json("/reference_data")


@mcp.tool()
def get_reference_data(reference_data_id: int) -> dict[str, Any]:
    """Retrieve a single Reference Data resource by id.

    Args:
        reference_data_id: The identifier of the Reference Data resource.
    """
    return _get_json(f"/reference_data/{reference_data_id}")


def _mask_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    if len(token) <= 8:
        return "****"
    return f"{token[:4]}...{token[-4:]}"


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
@mcp.tool()
def refresh_oauth_token() -> dict[str, Any]:
    """Fetch a fresh OAuth bearer token from /oauth/token (client-credentials grant).

    Requires GOCANVAS_CLIENT_ID and GOCANVAS_CLIENT_SECRET to be configured. The
    token is cached and used automatically for subsequent API calls; call this to
    force a refresh (e.g. after a 401). Returns token metadata with the access
    token masked for safety.
    """
    payload = _fetch_oauth_token()
    return {
        "access_token": _mask_token(payload.get("access_token")),
        "token_type": payload.get("token_type"),
        "expires_in": payload.get("expires_in"),
        "scope": payload.get("scope"),
        "created_at": payload.get("created_at"),
    }


def main() -> None:
    # Best-effort: obtain an OAuth token on startup so the first tool call is fast.
    if _oauth_configured():
        try:
            _ensure_oauth_token()
        except Exception:  # noqa: BLE001 - startup fetch is best-effort
            pass
    mcp.run()


if __name__ == "__main__":
    main()
