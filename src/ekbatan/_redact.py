"""Credentials, hidden wherever this package shows a value to a person.

A password must never reach a log line, an error message or the printed form of an object -
``log.info(result)``, a traceback, a test failure, an exception tracker. Each of those ends up
somewhere the database itself would never be: a log index, a ticket, a CI artefact. So the
redaction is done where a value is turned into text, not left to whoever prints it.

``***`` is what SQLAlchemy prints for the password of a url or an engine, so a reader sees the
same mark from both.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: What a secret is shown as.
HIDDEN = "***"

#: A key whose value is a secret, wherever it turns up - a url's query string, ``connect_args``,
#: the keyword arguments a driver is given. Matched case-insensitively against any part of the
#: key, so ``sslpassword``, ``api_token`` and ``client_secret`` count. Wide on purpose: hiding a
#: value that was not secret costs a reader a little, showing one that was costs a credential.
_SECRET_KEY = re.compile(r"pass|pwd|secret|token|credential|auth|key", re.IGNORECASE)

#: Credentials inside a string: ``password=x`` in a connection string, ``user:x@`` in a url.
_IN_TEXT = (
    (re.compile(r"(?i)\b(password|passwd|pwd)(\s*=\s*)('[^']*'|\"[^\"]*\"|[^\s;&]+)"), r"\1\2***"),
    (re.compile(r"(://[^:/@\s]*:)[^@\s]*@"), r"\1***@"),
)


def is_secret(key: object) -> bool:
    return bool(_SECRET_KEY.search(str(key)))


def redact_url(url: str) -> str:
    """``url`` with every credential in it shown as ``***``: the password, and any query parameter
    that names one (``?password=``, ``?sslpassword=``).

    A url SQLAlchemy cannot read is not shown at all: there is no telling where in it the password
    is, and a malformed url is exactly the one a person is most likely to paste whole.
    """
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(url)
    except Exception:
        return "<a url that could not be read, so it is not shown>"
    # A placeholder SQLAlchemy does not escape, swapped for `***` afterwards: it writes `***` in
    # a query string as `%2A%2A%2A`.
    marker = "EKBATANHIDDENVALUE"
    if parsed.query:
        parsed = parsed.set(
            query={
                key: (marker if is_secret(key) else value) for key, value in parsed.query.items()
            }
        )
    return parsed.render_as_string(hide_password=True).replace(marker, HIDDEN)


def redact(value: Any, key: object = None) -> Any:
    """``value`` with secrets hidden: under a secret-looking key, inside nested mappings and
    sequences, and as ``password=`` or ``user:password@`` inside any string."""
    if key is not None and is_secret(key):
        return HIDDEN
    if isinstance(value, Mapping):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    if isinstance(value, str):
        for pattern, replacement in _IN_TEXT:
            value = pattern.sub(replacement, value)
    return value
