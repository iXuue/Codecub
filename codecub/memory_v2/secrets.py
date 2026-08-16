"""Secret safety for Memory 2.0.

Memory is long-lived storage, so secret risk is higher than for ordinary traces.
This module is intentionally dependency-free (no runtime import) so deterministic
tests can exercise every stage: extraction, consolidation, persistence, retrieval
and debug views.

`filter_text` redacts secret-shaped values; `contains_secret` is the strict
rejection predicate used by the candidate filter and persistence guards.
"""

import re

REDACTED = "<redacted>"

# Conservative markers: any occurrence makes the text suspect.
SECRET_MARKER_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|password|passwd|"
    r"private[_-]?token|access[_-]?token|secret[_-]?key|client[_-]?secret|"
    r"session[_-]?token|auth[_-]?token)\b"
)

# Secret-shaped values (long random-ish tokens, JWTs, sk-/ghp-/xoxb- keys).
SECRET_SHAPED_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"sk-[A-Za-z0-9_-]{8,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|(?:[A-Za-z0-9+/]{40,}={0,2})"
    r")\b"
)

# Values that look like assignment of a secret: key=value or key: value.
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|authorization|bearer)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)

DEFAULT_SECRET_WORDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "client_secret",
        "passwd",
        "password",
        "private_token",
        "secret",
        "secret_key",
        "session_token",
        "token",
    }
)


def contains_secret(text, secret_words=DEFAULT_SECRET_WORDS):
    """Strict rejection predicate: True if text looks secret-bearing.

    Applied to candidate statements, evidence summaries and rendered views.
    A bare word like "token" is NOT enough on its own; we require a marker
    assignment, a secret-shaped value, or a marker in a secret-ish context.
    """
    value = str(text or "")
    if not value:
        return False
    if SECRET_MARKER_PATTERN.search(value):
        return True
    if SECRET_SHAPED_VALUE_PATTERN.search(value):
        return True
    if SECRET_ASSIGNMENT_PATTERN.search(value):
        return True
    lowered = value.lower()
    # "set the TOKEN env var" style instructions are still secret-adjacent:
    # reject any line that both mentions a secret word and looks like a command
    # that would reveal a value (export/echo/set + word).
    if re.search(r"(?i)\b(export|set|echo|printf)\b", value) and any(
        word in lowered for word in secret_words
    ):
        return True
    return False


def filter_text(text, redacted=REDACTED):
    """Redact secret-shaped values while keeping the rest readable.

    Used for the human-readable durable view and for debug output; the
    machine-readable stores additionally refuse to persist secret candidates.
    """
    value = str(text or "")
    if not value:
        return value
    # Assignment first: it needs the secret word as its anchor.
    value = SECRET_ASSIGNMENT_PATTERN.sub(lambda match: match.group(1) + redacted, value)
    value = SECRET_MARKER_PATTERN.sub(redacted, value)
    value = SECRET_SHAPED_VALUE_PATTERN.sub(redacted, value)
    return value
