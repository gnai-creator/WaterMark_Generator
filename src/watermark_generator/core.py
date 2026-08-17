"""Domain operations and lifecycle invariants."""
from __future__ import annotations
import datetime as dt
import json
import os
import re
from pathlib import Path
from .canonical import canonical_json, digest, watermark_id
from .crypto import b64, derive_provider_key, fingerprint, new_identity, sign, unb64, verify
from .errors import WatermarkError
from .storage import Store

PROVIDERS = ("openai", "anthropic", "google", "xai")
REASONS = {"compromised", "suspected_compromise", "scheduled_rotation", "provider_change", "manual", "other"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def signed_manifest(body: dict, private: bytes) -> dict:
    result = dict(body)
    result["signature"] = sign(private, canonical_json(body))
    return result


def manifest_hash(manifest: dict) -> str:
    return digest(canonical_json(manifest))


def initialize(store: Store, passphrase: str, prefix: str = "MTW") -> dict:
    if store.state_path.exists() or store.vault_path.exists():
        raise WatermarkError("Repository is already initialized.")
    private, public = new_identity()
    created = now()
    body = {"schema": "watermark-genesis/v1", "sequence": 0, "previous_manifest_hash": None,
            "created_at": created, "identity_public_key": b64(public),
            "identity_fingerprint": fingerprint(public), "signature_algorithm": "Ed25519"}
    genesis = signed_manifest(body, private)
    path = store.root / "public" / "manifests" / "genesis_manifest.json"
    store.write_json(path, genesis)
    store.write_json(store.root / "public" / "identity.json", {
        "schema": "watermark-identity/v1", "identity_public_key": b64(public), "algorithm": "Ed25519",
        "created_at": created, "identity_fingerprint": fingerprint(public)})
    state = {"schema": "watermark-generator-state/v1", "default_prefix": prefix, "current_watermark_id": None,
             "identity_public_key": b64(public), "identity_fingerprint": fingerprint(public),
             "manifest_sequence": 0, "last_manifest_hash": manifest_hash(genesis), "watermarks": {}}
    secrets = {"identity_private_key": b64(private), "master_secret": b64(os.urandom(32))}
    store.save(state, secrets, passphrase)
    return state


def parse_providers(value: str) -> list[str]:
    values = list(PROVIDERS) if value.lower() == "all" else [x.strip().lower() for x in value.split(",")]
    if not values or any(x not in PROVIDERS for x in values):
        raise WatermarkError("Models must be 'all' or a comma-separated subset of: " + ", ".join(PROVIDERS))
    return list(dict.fromkeys(values))


def _validate_prefix(prefix: str) -> str:
    prefix = prefix.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", prefix):
        raise WatermarkError("Prefix must contain 2-16 uppercase letters or digits and start with a letter.")
    return prefix


def _next_manifest(state: dict, body: dict, private: bytes) -> dict:
    body.update(sequence=state["manifest_sequence"] + 1, previous_manifest_hash=state["last_manifest_hash"],
                created_at=now(), identity_public_key=state["identity_public_key"], signature_algorithm="Ed25519")
    return signed_manifest(body, private)


def _record_manifest(store: Store, state: dict, manifest: dict, name: str) -> Path:
    path = store.root / "public" / "manifests" / name
    store.write_json(path, manifest)
    state["manifest_sequence"] = manifest["sequence"]
    state["last_manifest_hash"] = manifest_hash(manifest)
    return path


def generate(store: Store, passphrase: str, watermark: str, models: str, prefix: str | None = None) -> tuple[list[dict], Path | None]:
    state, sec = store.state(), store.secrets(passphrase)
    wid = watermark_id(watermark)
    prefix = _validate_prefix(prefix or state["default_prefix"])
    wm = state["watermarks"].setdefault(wid, {"prefix": prefix, "providers": {}})
    if wm["prefix"] != prefix:
        raise WatermarkError("This watermark was already registered with a different prefix.")
    created, manifest_path = [], None
    for provider in parse_providers(models):
        history = wm["providers"].setdefault(provider, [])
        active = next((r for r in history if r["status"] == "ACTIVE"), None)
        if active:
            created.append({**active, "created": False, "secret": derive_provider_key(unb64(sec["master_secret"]), wid, prefix, provider, active["generation"])})
            continue
        generation = max((r["generation"] for r in history), default=0) + 1
        key_id = f"{prefix}-{provider.upper()}-{generation:02d}"
        record = {"key_id": key_id, "generation": generation, "status": "ACTIVE", "created_at": now()}
        history.append(record)
        manifest = _next_manifest(state, {"schema": "watermark-key-creation/v1", "watermark_id": wid,
            "prefix": prefix, "provider": provider, "key_id": key_id, "generation": generation, "status": "ACTIVE"}, unb64(sec["identity_private_key"]))
        manifest_path = _record_manifest(store, state, manifest, f"{manifest['sequence']:06d}-create-{key_id}.json")
        created.append({**record, "created": True, "secret": derive_provider_key(unb64(sec["master_secret"]), wid, prefix, provider, generation)})
    state["current_watermark_id"] = wid
    store.save(state, sec, passphrase)
    return created, manifest_path


def _current(state: dict) -> tuple[str, dict]:
    wid = state.get("current_watermark_id")
    if not wid or wid not in state["watermarks"]:
        raise WatermarkError("No current watermark. Generate keys first.")
    return wid, state["watermarks"][wid]


def rotate(store: Store, passphrase: str, provider: str, reason: str) -> tuple[dict, dict, Path]:
    if provider not in PROVIDERS or reason not in REASONS:
        raise WatermarkError("Invalid provider or rotation reason.")
    state, sec = store.state(), store.secrets(passphrase)
    wid, wm = _current(state)
    history = wm["providers"].get(provider, [])
    old = next((r for r in history if r["status"] == "ACTIVE"), None)
    if not old:
        raise WatermarkError(f"No active key for {provider}.")
    old["status"] = "REVOKED"
    generation = max(r["generation"] for r in history) + 1
    new = {"key_id": f"{wm['prefix']}-{provider.upper()}-{generation:02d}", "generation": generation, "status": "ACTIVE", "created_at": now()}
    history.append(new)
    manifest = _next_manifest(state, {"schema": "watermark-key-rotation/v1", "watermark_id": wid, "prefix": wm["prefix"],
        "provider": provider, "previous_key_id": old["key_id"], "new_key_id": new["key_id"],
        "previous_status": "REVOKED", "new_status": "ACTIVE", "reason": reason, "generation": generation}, unb64(sec["identity_private_key"]))
    path = _record_manifest(store, state, manifest, f"{manifest['sequence']:06d}-rotate-{new['key_id']}.json")
    store.save(state, sec, passphrase)
    write_rotation_export(store, state, manifest)
    return old, new, path


def revoke(store: Store, passphrase: str, key_id: str, reason: str, retired: bool = False) -> Path:
    if reason not in REASONS:
        raise WatermarkError("Invalid revocation reason.")
    state, sec = store.state(), store.secrets(passphrase)
    wid, wm = _current(state)
    found = next((r for h in wm["providers"].values() for r in h if r["key_id"] == key_id), None)
    if not found or found["status"] != "ACTIVE":
        raise WatermarkError("Key is missing or no longer ACTIVE; terminal states cannot become ACTIVE.")
    found["status"] = "RETIRED" if retired else "REVOKED"
    provider = key_id.split("-")[-2].lower()
    manifest = _next_manifest(state, {"schema": "watermark-key-revocation/v1", "watermark_id": wid, "prefix": wm["prefix"],
        "provider": provider, "key_id": key_id, "new_status": found["status"], "reason": reason,
        "generation": found["generation"]}, unb64(sec["identity_private_key"]))
    path = _record_manifest(store, state, manifest, f"{manifest['sequence']:06d}-{found['status'].lower()}-{key_id}.json")
    store.save(state, sec, passphrase)
    return path


def verify_manifest(path: Path, trusted_public: bytes | None = None) -> tuple[bool, dict]:
    try:
        manifest = json.loads(path.read_text())
        signature = manifest.pop("signature")
        embedded = unb64(manifest["identity_public_key"])
        valid = verify(trusted_public or embedded, canonical_json(manifest), signature)
        manifest["signature"] = signature
        return valid, manifest
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise WatermarkError("Manifest is malformed or unreadable.") from exc


def export_key(store: Store, passphrase: str, provider: str) -> Path:
    state, sec = store.state(), store.secrets(passphrase)
    wid, wm = _current(state)
    active = next((r for r in wm["providers"].get(provider, []) if r["status"] == "ACTIVE"), None)
    if not active:
        raise WatermarkError(f"No active key for {provider}.")
    secret = derive_provider_key(unb64(sec["master_secret"]), wid, wm["prefix"], provider, active["generation"])
    path = store.root / "exports" / provider / "key.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"KEY_ID={active['key_id']}\nKEY={b64(secret)}\nWATERMARK_ID={wid}\nIDENTITY_PUBLIC_KEY={state['identity_public_key']}\n")
    os.chmod(path, 0o600)
    return path


def write_rotation_export(store: Store, state: dict, manifest: dict) -> Path:
    directory = store.root / "exports" / manifest["provider"]
    directory.mkdir(parents=True, exist_ok=True)
    store.write_json(directory / "rotation_manifest.json", manifest, 0o600)
    text = f"""WATERMARK KEY ROTATION

Identity fingerprint: {state['identity_fingerprint']}
Previous key: {manifest['previous_key_id']}
Previous status: REVOKED
New key: {manifest['new_key_id']}
New status: ACTIVE
Reason: {manifest['reason']}

A signed rotation manifest accompanies this update. Verify its Ed25519 signature
against the previously pinned Identity Public Key. If verification fails, reject
the rotation and report IDENTITY VERIFICATION FAILED.
"""
    path = directory / "rotation_prompt.txt"
    path.write_text(text)
    os.chmod(path, 0o600)
    return path
