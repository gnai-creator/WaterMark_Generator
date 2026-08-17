import secrets

import pytest

from watermark_generator.adapter import WatermarkAdapter, WatermarkConfig, constant_test_intensity
from watermark_generator.crypto import b64
from watermark_generator.errors import WatermarkError
from watermark_generator.local_runtime import GenerationConfig, generate_local


class FakeRuntime:
    vocabulary_size = 64
    eos_token_id = None

    def encode(self, text):
        return [ord(char) % self.vocabulary_size for char in text] or [1]

    def decode(self, token_ids):
        return ",".join(str(token) for token in token_ids)

    def next_token_logits(self, token_ids):
        return [0.0] * self.vocabulary_size


def disposable_adapter(minimum_tokens=10):
    environment = {"KEY": b64(secrets.token_bytes(32)), "KEY_ID": "TEST-LOCAL-01"}
    config = WatermarkConfig(strength=20.0, minimum_tokens=minimum_tokens)
    return WatermarkAdapter.from_environment(constant_test_intensity, config, environment)


def test_end_to_end_generation_applies_before_each_greedy_sample():
    runtime, adapter = FakeRuntime(), disposable_adapter()
    result = generate_local(runtime, adapter, "prompt", "doc", "time",
                            GenerationConfig(max_new_tokens=32, temperature=0.0))
    assert result.status == "APPLIED"
    assert result.applied_steps == 32 == len(result.generated_token_ids)
    detected = adapter.detect(result.generated_token_ids, "doc", "time", result.prompt_token_ids)
    assert detected.favored_token_count == 32
    assert detected.z_score > 4.0 and detected.sufficient_sample


def test_generation_is_reproducible_with_disposable_seeded_sampler():
    runtime, adapter = FakeRuntime(), disposable_adapter()
    config = GenerationConfig(max_new_tokens=12, temperature=0.8, top_k=12,
                              top_p=0.9, random_seed=42)
    first = generate_local(runtime, adapter, "prompt", "doc", "time", config)
    second = generate_local(runtime, adapter, "prompt", "doc", "time", config)
    assert first.generated_token_ids == second.generated_token_ids


@pytest.mark.parametrize("kwargs", [
    {"max_new_tokens": 0}, {"temperature": -1.0}, {"top_k": -1},
    {"top_p": 0.0}, {"top_p": 1.1},
])
def test_invalid_generation_configuration_fails_closed(kwargs):
    with pytest.raises(WatermarkError, match="Invalid generation configuration"):
        GenerationConfig(**kwargs)


def test_runtime_vocabulary_mismatch_fails_closed():
    class BrokenRuntime(FakeRuntime):
        def next_token_logits(self, token_ids):
            return [0.0]

    with pytest.raises(WatermarkError, match="vocabulary size"):
        generate_local(BrokenRuntime(), disposable_adapter(), "prompt", "doc", "time",
                       GenerationConfig(max_new_tokens=1))
