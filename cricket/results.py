"""Shared tool envelopes: preserve domain errors and keep exception details private."""

import re

ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def error_result(code: str, message: str) -> dict:
    """Build a safe domain failure using a stable code and user-facing explanation."""
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
        "provenance": [],
        "coverage": {},
    }


def normalize_envelope(value: dict) -> dict:
    """Validate an already JSON-safe envelope without losing failure data or metadata."""
    ok = value["ok"]
    provenance, coverage = value.get("provenance", []), value.get("coverage", {})
    if not isinstance(ok, bool) or not isinstance(provenance, (dict, list)):
        raise TypeError("Invalid tool result envelope.")
    if not isinstance(coverage, dict):
        raise TypeError("Invalid tool coverage.")
    error = value.get("error")
    if ok:
        if error is not None:
            raise ValueError("A successful tool result cannot contain an error.")
    else:
        if not isinstance(error, dict):
            raise TypeError("A failed tool result requires a structured domain error.")
        code, message = error.get("code"), error.get("message")
        if not isinstance(code, str) or not ERROR_CODE.fullmatch(code):
            raise ValueError("Invalid domain error code.")
        if not isinstance(message, str) or not message.strip() or len(message) > 2000:
            raise ValueError("Invalid domain error message.")
        # Only the documented fields are exposed; diagnostic extras are discarded.
        error = {"code": code, "message": message}
    return {
        "ok": ok,
        "data": value.get("data"),
        "error": error,
        "provenance": provenance,
        "coverage": coverage,
    }
