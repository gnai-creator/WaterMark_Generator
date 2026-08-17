"""Stable serialization and conservative watermark canonicalization."""
import hashlib
import json
import unicodedata
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def canonicalize_watermark(value: str) -> str:
    """Normalize Unicode and line endings; preserve all other distinctions."""
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


def watermark_id(value: str) -> str:
    return "sha256:" + hashlib.sha256(canonicalize_watermark(value).encode()).hexdigest()


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()
