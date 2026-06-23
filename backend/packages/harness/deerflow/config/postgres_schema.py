"""Shared validation for PostgreSQL schema names."""

from __future__ import annotations

import re

# Lowercase-only on purpose. The schema is created *quoted*
# (``CREATE SCHEMA IF NOT EXISTS "<schema>"``, case-preserved) but pinned via an
# *unquoted* ``search_path`` token, which PostgreSQL folds to lowercase. Allowing
# uppercase here would let the two diverge so tables silently land in ``public``.
POSTGRES_SCHEMA_PATTERN = r"^[a-z_][a-z0-9_]{0,62}$"
_POSTGRES_SCHEMA_RE = re.compile(POSTGRES_SCHEMA_PATTERN)


def validate_postgres_schema(value: str) -> str:
    """Validate the v1 plain-identifier PostgreSQL schema contract."""
    if value == "":
        return value
    if not _POSTGRES_SCHEMA_RE.match(value):
        raise ValueError(f"postgres_schema must be a plain lowercase PostgreSQL identifier matching {POSTGRES_SCHEMA_PATTERN}; got {value!r}. Mixed-case and quoted identifiers are not supported.")
    return value
