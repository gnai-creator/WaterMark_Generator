import json
from pathlib import Path
import pytest
from watermark_generator.canonical import canonical_json, watermark_id
from watermark_generator.core import generate, manifest_hash, revoke, rotate, signed_manifest, verify_manifest
from watermark_generator.crypto import derive_provider_key, new_identity, sign, unb64, verify
from watermark_generator.errors import WatermarkError


def test_provider_watermark_and_generation_domain_separation():
    master = b"m" * 32
    a = derive_provider_key(master, "sha256:a", "MTW", "openai", 1)
    assert a != derive_provider_key(master, "sha256:a", "MTW", "google", 1)
    assert a != derive_provider_key(master, "sha256:b", "MTW", "openai", 1)
    assert a != derive_provider_key(master, "sha256:a", "MTW", "openai", 2)


def test_generation_is_idempotent_and_secrets_are_distinct(repo, passphrase):
    first, _ = generate(repo, passphrase, "formula", "openai,google")
    again, manifest = generate(repo, passphrase, "formula", "openai", "MTW")
    assert first[0]["secret"] != first[1]["secret"]
    assert again[0]["created"] is False
    assert manifest is None


def test_rotation_monotonic_and_terminal(repo, passphrase):
    generate(repo, passphrase, "formula", "openai")
    old, new, _ = rotate(repo, passphrase, "openai", "compromised")
    assert (old["status"], new["generation"], new["status"]) == ("REVOKED", 2, "ACTIVE")
    with pytest.raises(WatermarkError):
        revoke(repo, passphrase, old["key_id"], "manual")


def test_manifest_tamper_invalidates_signature(repo, passphrase):
    _, path = generate(repo, passphrase, "formula", "openai")
    valid, _ = verify_manifest(path)
    assert valid
    data = json.loads(path.read_text()); data["provider"] = "xai"
    path.write_text(json.dumps(data))
    valid, _ = verify_manifest(path)
    assert not valid


def test_watermark_key_cannot_sign_and_other_identity_rejected(repo, passphrase):
    records, path = generate(repo, passphrase, "formula", "openai")
    manifest = json.loads(path.read_text()); signature = manifest.pop("signature")
    public = unb64(manifest["identity_public_key"])
    assert verify(public, canonical_json(manifest), signature)
    attacker_private, attacker_public = new_identity()
    assert not verify(public, canonical_json(manifest), sign(attacker_private, canonical_json(manifest)))
    assert not verify(attacker_public, canonical_json(manifest), signature)
    # A 32-byte watermark key may be syntactically accepted as Ed25519 seed, but
    # its derived public identity cannot verify against the pinned Identity Root.
    assert not verify(public, canonical_json(manifest), sign(records[0]["secret"], canonical_json(manifest)))


def test_manifest_chain_sequence(repo, passphrase):
    _, first = generate(repo, passphrase, "formula", "openai")
    _, _, second = rotate(repo, passphrase, "openai", "manual")
    a, b = json.loads(first.read_text()), json.loads(second.read_text())
    assert b["sequence"] == a["sequence"] + 1
    assert b["previous_manifest_hash"] == manifest_hash(a)


def test_corrupt_state_fails_closed(repo, passphrase):
    repo.state_path.write_text("not json")
    with pytest.raises(WatermarkError):
        generate(repo, passphrase, "formula", "openai")
