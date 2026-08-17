# Watermark Generator

A local, watermark-agnostic CLI for generating, rotating, revoking, exporting,
and verifying LLM watermark keys. Watermark secrets perform a protocol; an
independent Ed25519 **Identity Root** authorizes their lifecycle. Compromise of
a provider key therefore does not grant authority to forge its successor.

The bundled MTW example is a provenance convention, not proof of legal
authorship, consent, approval, endorsement, or publication authorization.
Authenticity remains subject to confirmation by the identity holder.

The owner's MTW formula is intentionally **not distributed in this repository**.
Examples use placeholders only. Supply your own watermark specification, or
obtain the applicable holder's authorization before using a third-party one.

## Security architecture

- The Identity Private Key and random 256-bit master secret live only in an
  AES-256-GCM encrypted local vault. A passphrase key is derived with scrypt
  (`N=32768, r=8, p=1`). Neither secret enters manifests or exports.
- Provider keys are independent HKDF-SHA256 derivations over the full Watermark
  ID, prefix, provider, and monotonic generation, with domain separation.
- `WID = SHA256(NFC(strip(normalize_line_endings(input))))`. The tool deliberately
  makes no attempt to infer mathematical equivalence.
- Ed25519 signs UTF-8 JSON with sorted keys and compact separators, excluding
  only the `signature` field. Every manifest carries the SHA-256 hash of its
  signed predecessor.
- State transitions are one-way: `ACTIVE -> REVOKED|RETIRED`. Generation is
  idempotent while an active key exists.

Exports contain secrets and are mode `0600` and gitignored. Environment-based
passphrases are for automation; interactive entry is preferred. Keep offline
backups of the Identity Private Key and encrypted vault.

## Install and use

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
run init
run --models all --prefix DEMO --watermark 'YOUR_OWN_WATERMARK_SPECIFICATION'
run --models openai,anthropic --watermark-file examples/mtw.txt
run rotate --model openai --reason compromised
run verify public/manifests/000005-rotate-MTW-OPENAI-02.json --identity public/identity.json
run export --model openai
run status
```

Use `--show-secrets` only when terminal disclosure is intentional. `export`
emits only the selected active watermark key and public metadata; it never
exports the master secret or Identity Private Key.

`verify` validates signature integrity. For anti-rollback in a consuming
system, pin `public/identity.json`, retain the greatest accepted `sequence`,
require the expected `previous_manifest_hash`, and reject any lower sequence.
A fresh LLM session has no implicit continuity: provide the pinned public key
and signed manifest chain.

## Development

```bash
pytest
```

The project uses `cryptography`; no cryptographic primitive is implemented here.
