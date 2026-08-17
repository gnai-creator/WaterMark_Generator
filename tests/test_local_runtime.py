import secrets

import pytest

from watermark_generator.adapter import WatermarkAdapter, WatermarkConfig, constant_test_intensity
from watermark_generator.crypto import b64
from watermark_generator.errors import WatermarkError
from watermark_generator.local_runtime import GenerationConfig, generate_local, rewrite_local


class FakeRuntime:
    vocabulary_size = 64
    eos_token_id = None

    def encode(self, text):
        return [ord(char) % self.vocabulary_size for char in text] or [1]

    def decode(self, token_ids):
        return ",".join(str(token) for token in token_ids)

    def next_token_logits(self, token_ids):
        return [0.0] * self.vocabulary_size


class CapturingRuntime(FakeRuntime):
    def __init__(self):
        self.encoded_text = None

    def encode(self, text):
        self.encoded_text = text
        return super().encode(text)


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


def test_rewrite_local_regenerates_source_with_preservation_instructions():
    runtime, instance = CapturingRuntime(), disposable_adapter(minimum_tokens=2)
    result = rewrite_local(runtime, instance, "Original text 42 https://example.test",
                           "rewrite-doc", "time",
                           GenerationConfig(max_new_tokens=4, temperature=0.0))
    assert "Original text 42 https://example.test" in runtime.encoded_text
    assert "Preserve meaning, facts, names, numbers" in runtime.encoded_text
    assert result.status == "APPLIED" and result.applied_steps == 4


def test_rewrite_local_rejects_empty_source():
    with pytest.raises(WatermarkError, match="must not be empty"):
        rewrite_local(FakeRuntime(), disposable_adapter(), "  ", "doc", "time")
