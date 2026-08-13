"""ULID identifiers with a type prefix (SPEC §18 conventions: `ntc_`, `org_`, `tsk_`).

Why ULIDs rather than UUIDv4: they sort by creation time, so a primary-key index stays
locality-friendly as notices arrive, and `ORDER BY id` is a usable tiebreaker in cursor
pagination. The prefix makes an id self-describing in a log line or an error report, which
matters when debugging a pipeline that moves ids between four services.

Implemented here rather than pulled from a library so the TypeScript and Python sides agree
on the format without depending on two packages staying compatible.
"""

from __future__ import annotations

import os
import time

# Crockford's base32: no I, L, O or U, so an id cannot be misread aloud or in a ticket.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENCODED_TIME_LENGTH = 10
_ENCODED_RANDOM_LENGTH = 16


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def ulid(now_ms: int | None = None) -> str:
    """A 26-character ULID: 48 bits of millisecond timestamp, 80 bits of randomness."""
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(timestamp, _ENCODED_TIME_LENGTH) + _encode(randomness, _ENCODED_RANDOM_LENGTH)


def new_id(prefix: str) -> str:
    """A prefixed ULID, e.g. `ntc_01J8Z...`."""
    return f"{prefix}_{ulid()}"


#: Type prefixes used across the codebase. Kept in one place so they cannot diverge.
PREFIX_NOTICE = "ntc"
PREFIX_RAW_NOTICE = "raw"
PREFIX_CLUSTER = "cls"
PREFIX_MATCH = "mch"
PREFIX_JOB = "job"
PREFIX_EVENT = "evt"
PREFIX_DOCUMENT = "doc"
PREFIX_VERSION = "nvr"
