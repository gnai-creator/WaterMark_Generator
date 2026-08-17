"""End-to-end local causal-LM generation with pre-sampling watermark bias."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .adapter import DetectionResult, WatermarkAdapter
from .errors import WatermarkError


class CausalRuntime(Protocol):
    @property
    def vocabulary_size(self) -> int: ...
    @property
    def eos_token_id(self) -> int | None: ...
    def encode(self, text: str) -> list[int]: ...
    def decode(self, token_ids: Sequence[int]) -> str: ...
    def next_token_logits(self, token_ids: Sequence[int]) -> list[float]: ...


@dataclass(frozen=True)
class GenerationConfig:
    max_new_tokens: int = 128
    temperature: float = 0.8
    top_k: int = 0
    top_p: float = 1.0
    random_seed: int | None = None

    def __post_init__(self) -> None:
        if self.max_new_tokens <= 0 or self.top_k < 0:
            raise WatermarkError("Invalid generation configuration.")
        if not math.isfinite(self.temperature) or self.temperature < 0.0:
            raise WatermarkError("Invalid generation configuration.")
        if not math.isfinite(self.top_p) or not 0.0 < self.top_p <= 1.0:
            raise WatermarkError("Invalid generation configuration.")


@dataclass(frozen=True)
class GenerationResult:
    text: str
    prompt_token_ids: tuple[int, ...]
    generated_token_ids: tuple[int, ...]
    applied_steps: int
    key_id: str
    status: str
    detection: DetectionResult


def _sample(logits: Sequence[float], config: GenerationConfig, rng: random.Random) -> int:
    if not logits or any(not math.isfinite(value) for value in logits):
        raise WatermarkError("Runtime returned invalid logits.")
    if config.temperature == 0.0:
        return max(range(len(logits)), key=logits.__getitem__)
    scaled = [value / config.temperature for value in logits]
    candidates = sorted(range(len(scaled)), key=scaled.__getitem__, reverse=True)
    if config.top_k:
        candidates = candidates[:min(config.top_k, len(candidates))]
    maximum = max(scaled[index] for index in candidates)
    weights = [math.exp(scaled[index] - maximum) for index in candidates]
    if config.top_p < 1.0:
        total = sum(weights)
        kept_candidates, kept_weights, cumulative = [], [], 0.0
        for index, weight in zip(candidates, weights):
            kept_candidates.append(index); kept_weights.append(weight)
            cumulative += weight / total
            if cumulative >= config.top_p:
                break
        candidates, weights = kept_candidates, kept_weights
    return rng.choices(candidates, weights=weights, k=1)[0]


def generate_local(runtime: CausalRuntime, adapter: WatermarkAdapter, prompt: str,
                   document_id: str, timestamp: str,
                   generation: GenerationConfig | None = None) -> GenerationResult:
    """Generate tokens while applying watermark bias before every sample."""
    config = generation or GenerationConfig()
    prompt_tokens = runtime.encode(prompt)
    if not prompt_tokens:
        raise WatermarkError("The tokenizer produced an empty prompt.")
    if runtime.vocabulary_size <= 0:
        raise WatermarkError("Runtime vocabulary is invalid.")
    all_tokens = list(prompt_tokens)
    generated: list[int] = []
    rng = random.Random(config.random_seed)
    for _ in range(config.max_new_tokens):
        logits = runtime.next_token_logits(all_tokens)
        if len(logits) != runtime.vocabulary_size:
            raise WatermarkError("Runtime logits do not match its vocabulary size.")
        application = adapter.apply(logits, all_tokens, document_id, timestamp)
        token = _sample(application.logits, config, rng)
        generated.append(token); all_tokens.append(token)
        if runtime.eos_token_id is not None and token == runtime.eos_token_id:
            break
    detection = adapter.detect(generated, document_id, timestamp, prompt_tokens)
    return GenerationResult(runtime.decode(generated), tuple(prompt_tokens), tuple(generated),
                            len(generated), adapter.key_id, "APPLIED", detection)


class TransformersRuntime:
    """Lazy optional adapter for a Hugging Face causal language model."""

    def __init__(self, model_path: str | Path, *, device: str = "auto",
                 allow_download: bool = False) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise WatermarkError("Local runtime dependencies are missing; install the 'local' extra.") from exc
        path = str(model_path)
        if not allow_download and not Path(path).exists():
            raise WatermarkError("Model path must exist locally unless downloads are explicitly enabled.")
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                path, local_files_only=not allow_download, trust_remote_code=False)
            model_kwargs = {"local_files_only": not allow_download, "trust_remote_code": False}
            if device == "auto":
                model_kwargs["device_map"] = "auto"
            self._model = AutoModelForCausalLM.from_pretrained(path, **model_kwargs)
            if device != "auto":
                self._model.to(device)
            self._model.eval()
            self._torch = torch
            self._device = next(self._model.parameters()).device
        except Exception as exc:
            raise WatermarkError("Unable to load the requested local model.") from exc

    @property
    def vocabulary_size(self) -> int:
        return int(self._model.config.vocab_size)

    @property
    def eos_token_id(self) -> int | None:
        value = self._tokenizer.eos_token_id
        return int(value) if value is not None else None

    def encode(self, text: str) -> list[int]:
        return list(self._tokenizer.encode(text, add_special_tokens=True))

    def decode(self, token_ids: Sequence[int]) -> str:
        return self._tokenizer.decode(list(token_ids), skip_special_tokens=True)

    def next_token_logits(self, token_ids: Sequence[int]) -> list[float]:
        tensor = self._torch.tensor([list(token_ids)], dtype=self._torch.long, device=self._device)
        with self._torch.inference_mode():
            output = self._model(input_ids=tensor, use_cache=False)
        return output.logits[0, -1].float().cpu().tolist()
