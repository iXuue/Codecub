"""Atomic, corruption-safe persistence for Memory 2.0 stores.

JSONL records are appended line by line (stream-friendly); index.json and any
whole-file rewrite go through temp-file + atomic replace so a crash never leaves
a half-written store. Corrupt files degrade safely: the store loads empty and
flags the corruption instead of raising into the agent loop.
"""

import json
import os
import tempfile
from pathlib import Path

from . import secrets as secretlib


class StoreCorruption(Exception):
    """Raised internally when a store file fails to parse; callers degrade."""


def write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(temp_name).replace(path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_json_atomic(path, payload):
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json_safe(path, default=None):
    """Return parsed JSON or `default`; never raise on missing/corrupt files."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def append_jsonl(path, record):
    """Append one record (dict) to a JSONL file, flushed immediately."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()


def read_jsonl(path):
    """Read all records from a JSONL file.

    Returns (records, corrupt_line_count). Invalid lines are skipped and
    counted, never raised — a single corrupted line must not kill the agent.
    """
    path = Path(path)
    if not path.exists():
        return [], 0
    records = []
    corrupt = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], 1
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            corrupt += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            corrupt += 1
    return records, corrupt


def rewrite_jsonl(path, records):
    """Rewrite a JSONL file atomically from a list of dicts."""
    text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    write_text_atomic(path, text)


def redact_records(records):
    """Defense-in-depth: scrub secret-shaped values from any persisted record."""
    cleaned = []
    for record in records:
        item = dict(record)
        for key, value in list(item.items()):
            if isinstance(value, str) and secretlib.contains_secret(value):
                item[key] = secretlib.filter_text(value)
        cleaned.append(item)
    return cleaned
