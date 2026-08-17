import json
import math
import secrets

import pytest

from watermark_generator.adapter import (
    TableIntensity, WatermarkAdapter, WatermarkConfig,
    constant_test_intensity,
)
from watermark_generator.cli import main
from watermark_generator.crypto import b64
from watermark_generator.errors import WatermarkError


def disposable_env(key_id="TEST-LOCAL-01", key=None):
    return {"KEY": b64(key or secrets.token_bytes(32)), "KEY_ID": key_id}


def adapter(env=None, config=None, intensity=constant_test_intensity):
    return WatermarkAdapter.from_environment(intensity, config, env or disposable_env())


def test_missing_credentials_fail_without_secret_disclosure():
    with pytest.raises(WatermarkError, match="credentials are unavailable") as error:
        WatermarkAdapter.from_environment(constant_test_intensity, environ={})
    assert "KEY" not in str(error.value)
    with pytest.raises(WatermarkError, match="credentials are unavailable"):
        WatermarkAdapter.from_environment(constant_test_intensity, environ={"KEY": b64(secrets.token_bytes(32))})


def test_secret_is_not_in_repr_or_results():
    raw = secrets.token_bytes(32)
    instance = adapter(disposable_env(key=raw))
    result = instance.apply([0.0] * 32, [], "doc", "time")
    assert b64(raw) not in repr(instance)
    assert b64(raw) not in repr(result)


def test_partitions_change_with_key_document_and_context():
    key_a, key_b = secrets.token_bytes(32), secrets.token_bytes(32)
    a = adapter(disposable_env(key=key_a)); b = adapter(disposable_env(key=key_b))
    seed_a = a.document_seed("doc-a", "time")
    seed_b = b.document_seed("doc-a", "time")
    assert seed_a != seed_b
    assert seed_a != a.document_seed("doc-b", "time")
    p1 = [a.is_favored(seed_a, [1, 2], 2, token) for token in range(128)]
    p2 = [a.is_favored(seed_a, [1, 3], 2, token) for token in range(128)]
    p3 = [b.is_favored(seed_b, [1, 2], 2, token) for token in range(128)]
    assert p1 != p2 and p1 != p3


def test_application_is_deterministic_selective_and_non_mutating():
    instance = adapter()
    logits = [float(index) for index in range(128)]
    untouched = logits.copy()
    first = instance.apply(logits, [1, 2], "doc", "time")
    second = instance.apply(logits, [1, 2], "doc", "time")
    assert first == second
    assert logits == untouched
    differences = [new - old for new, old in zip(first.logits, logits)]
    assert set(differences) <= {0.0, 1.0}
    assert differences.count(1.0) == first.favored_token_count
    assert 0 < first.favored_token_count < len(logits)


def test_disabled_application_does_not_claim_success():
    result = adapter().apply([0.0, 1.0], [], "doc", "time", enabled=False)
    assert not result.applied and result.logits == (0.0, 1.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_logits_and_intensity_are_rejected(bad):
    with pytest.raises(WatermarkError):
        adapter().apply([0.0, bad], [], "doc", "time")
    with pytest.raises(WatermarkError):
        adapter(intensity=lambda _p, _l: bad).apply([0.0, 1.0], [], "doc", "time")


@pytest.mark.parametrize("kwargs", [
    {"gamma": 0.0}, {"gamma": 1.0}, {"period": 0},
    {"context_width": 0}, {"minimum_tokens": 0}, {"strength": float("nan")},
])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(WatermarkError, match="Invalid watermark configuration"):
        WatermarkConfig(**kwargs)


def test_detection_reconstructs_partition_and_flags_short_samples():
    config = WatermarkConfig(minimum_tokens=10)
    instance = adapter(config=config)
    seed = instance.document_seed("doc", "time")
    history = []
    for position in range(64):
        token = next(candidate for candidate in range(512)
                     if instance.is_favored(seed, history, position, candidate))
        history.append(token)
    detected = instance.detect(history, "doc", "time")
    short = instance.detect(history[:4], "doc", "time")
    assert detected.favored_token_count == len(history)
    assert detected.z_score > 4.0 and detected.sufficient_sample
    assert not short.sufficient_sample


def test_intensity_table_must_match_period():
    instance = adapter(config=WatermarkConfig(period=3), intensity=TableIntensity((1.0, 2.0)))
    with pytest.raises(WatermarkError, match="length"):
        instance.apply([0.0, 1.0], [], "doc", "time")


def test_cli_reads_process_environment_and_never_prints_secret(tmp_path, monkeypatch, capsys):
    secret = secrets.token_bytes(32)
    monkeypatch.setenv("KEY", b64(secret)); monkeypatch.setenv("KEY_ID", "TEST-LOCAL-01")
    (tmp_path / "tokens.json").write_text("[]")
    (tmp_path / "logits.json").write_text("[0,0,0,0]")
    (tmp_path / "intensity.json").write_text(json.dumps([1.0] * 4))
    code = main(["apply", "--document-id", "doc", "--timestamp", "time",
                 "--tokens", str(tmp_path / "tokens.json"),
                 "--logits", str(tmp_path / "logits.json"),
                 "--intensity-table", str(tmp_path / "intensity.json"), "--period", "4"])
    captured = capsys.readouterr()
    assert code == 0 and json.loads(captured.out)["status"] == "APPLIED"
    assert b64(secret) not in captured.out + captured.err


def test_cli_does_not_load_key_from_dotenv(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("KEY", raising=False); monkeypatch.delenv("KEY_ID", raising=False)
    (tmp_path / ".env").write_text(f"KEY={b64(secrets.token_bytes(32))}\nKEY_ID=TEST-LOCAL-01\n")
    for name, value in (("tokens.json", "[]"), ("logits.json", "[0]"), ("intensity.json", "[1]")):
        (tmp_path / name).write_text(value)
    code = main(["apply", "--document-id", "doc", "--timestamp", "time",
                 "--tokens", str(tmp_path / "tokens.json"), "--logits", str(tmp_path / "logits.json"),
                 "--intensity-table", str(tmp_path / "intensity.json"), "--period", "1"])
    assert code == 1
    assert "credentials are unavailable" in capsys.readouterr().err
