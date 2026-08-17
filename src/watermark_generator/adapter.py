"""Provider-agnostic, local logits watermark application and detection.

Production credentials are accepted only from the process environment by
``WatermarkAdapter.from_environment``. They are never included in result
objects, error messages, representations, logs, or persistent state.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence

from .crypto import unb64
from .errors import WatermarkError

DOCUMENT_DOMAIN = b"watermark-generator/v1/document-seed"
PARTITION_DOMAIN = b"watermark-generator/v1/token-partition"
UINT64_LIMIT = 2**64


class IntensityFunction(Protocol):
    """Return a deterministic, non-negative intensity for one position."""

    def __call__(self, position: int, period: int) -> float: ...


class LogitsRuntime(Protocol):
    """Interface a concrete local model runtime must implement."""

    @property
    def vocabulary_size(self) -> int: ...
    def encode(self, text: str) -> list[int]: ...
    def decode(self, token_ids: list[int]) -> str: ...
    def next_token_logits(self, token_ids: list[int]) -> list[float]: ...


@dataclass(frozen=True)
class WatermarkConfig:
    protocol_version: str = "FMM-0.1"
    period: int = 64
    context_width: int = 4
    gamma: float = 0.5
    strength: float = 1.0
    minimum_tokens: int = 100

    def __post_init__(self) -> None:
        if not self.protocol_version:
            raise WatermarkError("Invalid watermark configuration.")
        if self.period <= 0 or self.context_width <= 0 or self.minimum_tokens <= 0:
            raise WatermarkError("Invalid watermark configuration.")
        if not math.isfinite(self.gamma) or not 0.0 < self.gamma < 1.0:
            raise WatermarkError("Invalid watermark configuration.")
        if not math.isfinite(self.strength) or self.strength < 0.0:
            raise WatermarkError("Invalid watermark configuration.")


@dataclass(frozen=True)
class ApplicationResult:
    logits: tuple[float, ...]
    position: int
    favored_token_count: int
    key_id: str
    applied: bool


@dataclass(frozen=True)
class DetectionResult:
    z_score: float
    token_count: int
    favored_token_count: int
    weighted_score: float
    sufficient_sample: bool
    key_id: str


@dataclass(frozen=True)
class TableIntensity:
    """Validated periodic intensity table suitable for private local profiles."""

    values: tuple[float, ...]

    def __post_init__(self) -> None:
        try:
            normalized = tuple(float(value) for value in self.values)
        except (TypeError, ValueError) as exc:
            raise WatermarkError("Invalid intensity table.") from exc
        if not normalized or any(not math.isfinite(v) or v < 0.0 for v in normalized):
            raise WatermarkError("Invalid intensity table.")
        object.__setattr__(self, "values", normalized)

    def __call__(self, position: int, period: int) -> float:
        if len(self.values) != period:
            raise WatermarkError("Intensity table length must equal the configured period.")
        return self.values[position % period]


def constant_test_intensity(position: int, period: int) -> float:
    """Disposable test helper; not a production watermark intensity function."""
    del position, period
    return 1.0


def _u64(value: int) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < UINT64_LIMIT:
        raise WatermarkError("Token identifiers and positions must be unsigned 64-bit integers.")
    return value.to_bytes(8, "big")


def _field(value: str) -> bytes:
    raw = value.encode("utf-8")
    return _u64(len(raw)) + raw


def _validate_intensity(value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise WatermarkError("Intensity function returned an invalid value.") from exc
    if not math.isfinite(result) or result < 0.0:
        raise WatermarkError("Intensity function returned an invalid value.")
    return result


@dataclass(frozen=True)
class WatermarkAdapter:
    """Stateless applicator/detector holding only an ephemeral process secret."""

    key_id: str
    intensity: IntensityFunction
    config: WatermarkConfig = field(default_factory=WatermarkConfig)
    _key: bytes = field(repr=False, compare=False, default=b"")

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{1,127}", self.key_id) or not self._key:
            raise WatermarkError("Watermark runtime credentials are unavailable.")

    @classmethod
    def from_environment(
        cls, intensity: IntensityFunction, config: WatermarkConfig | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "WatermarkAdapter":
        source = os.environ if environ is None else environ
        encoded_key, key_id = source.get("KEY"), source.get("KEY_ID")
        if not encoded_key or not key_id:
            raise WatermarkError("Watermark runtime credentials are unavailable.")
        try:
            key = unb64(encoded_key)
        except (ValueError, TypeError) as exc:
            raise WatermarkError("Watermark runtime credentials are invalid.") from exc
        if len(key) < 32:
            raise WatermarkError("Watermark runtime credentials are invalid.")
        return cls(key_id=key_id, intensity=intensity, config=config or WatermarkConfig(), _key=key)

    def document_seed(self, document_id: str, timestamp: str) -> bytes:
        if not document_id or not timestamp:
            raise WatermarkError("Document ID and timestamp are required.")
        payload = (DOCUMENT_DOMAIN + _field(self.config.protocol_version) +
                   _field(self.key_id) + _field(document_id) + _field(timestamp))
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def _context_digest(self, seed: bytes, previous_tokens: Sequence[int], position: int) -> bytes:
        context = previous_tokens[-self.config.context_width:]
        payload = PARTITION_DOMAIN + _u64(len(context))
        payload += b"".join(_u64(token) for token in context) + _u64(position)
        return hmac.new(seed, payload, hashlib.sha256).digest()

    def is_favored(self, seed: bytes, previous_tokens: Sequence[int], position: int, candidate: int) -> bool:
        context_digest = self._context_digest(seed, previous_tokens, position)
        candidate_digest = hmac.new(context_digest, _u64(candidate), hashlib.sha256).digest()
        normalized = int.from_bytes(candidate_digest[:8], "big") / UINT64_LIMIT
        return normalized < self.config.gamma

    def apply(self, logits: Sequence[float], previous_tokens: Sequence[int], document_id: str,
              timestamp: str, *, enabled: bool = True) -> ApplicationResult:
        try:
            original = tuple(float(value) for value in logits)
        except (TypeError, ValueError) as exc:
            raise WatermarkError("Logits must be a non-empty sequence of finite numbers.") from exc
        if not original or any(not math.isfinite(value) for value in original):
            raise WatermarkError("Logits must be a non-empty sequence of finite numbers.")
        for token in previous_tokens:
            _u64(token)
        position = len(previous_tokens)
        if not enabled:
            return ApplicationResult(original, position, 0, self.key_id, False)
        seed = self.document_seed(document_id, timestamp)
        intensity = _validate_intensity(self.intensity(position, self.config.period))
        bias = self.config.strength * intensity
        modified = list(original)
        favored = 0
        for candidate in range(len(modified)):
            if self.is_favored(seed, previous_tokens, position, candidate):
                modified[candidate] += bias
                favored += 1
        return ApplicationResult(tuple(modified), position, favored, self.key_id, True)

    def detect(self, tokens: Sequence[int], document_id: str, timestamp: str,
               prefix_tokens: Sequence[int] = ()) -> DetectionResult:
        seed = self.document_seed(document_id, timestamp)
        numerator = sum_squares = 0.0
        favored_count = 0
        history = list(prefix_tokens)
        for token in history:
            _u64(token)
        for offset, token in enumerate(tokens):
            position = len(prefix_tokens) + offset
            _u64(token)
            intensity = _validate_intensity(self.intensity(position, self.config.period))
            favored = self.is_favored(seed, history, position, token)
            favored_count += int(favored)
            numerator += intensity * (int(favored) - self.config.gamma)
            sum_squares += intensity * intensity
            history.append(token)
        denominator = math.sqrt(self.config.gamma * (1.0 - self.config.gamma) * sum_squares)
        z_score = numerator / denominator if denominator else 0.0
        return DetectionResult(z_score, len(tokens), favored_count, numerator,
                               len(tokens) >= self.config.minimum_tokens, self.key_id)
