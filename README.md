# Watermark Generator

Local key lifecycle and provenance tooling for LLM watermarks.

It supports two distinct modes:

- **Local LLMs:** real statistical logits modulation before every token sample.
- **Hosted LLMs:** an explicit visible provenance marker requested per session.

Provenance does not by itself prove legal authorship, consent, approval, or
endorsement.

## Install and initialize

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
run init --prefix FMM
run --models all --prefix FMM --watermark-file private/watermark.txt
```

Set a strong `WATERMARK_GENERATOR_PASSPHRASE` in `.env`. Never commit or share
that file.

## Hosted LLMs: visible session marker

Paste the following at the beginning of a hosted LLM conversation:

```text
VISIBLE PROVENANCE SESSION

For this session, append the following visible provenance marker to each
substantive natural-language response, without changing code, quotations,
equations, hashes, URLs, identifiers, or structured data:

<YOUR WATERMARK>

This is an explicit visible marker only. Do not claim that a hidden statistical
watermark was applied. Provenance does not imply consent, approval, authorship,
or endorsement. Report the status as: <YOUR PREFIX>: VISIBLE MARK ONLY
```

Or generate it from the gitignored `private/watermark.txt`:

```bash
run session-prompt
```

No key is used or displayed. Hosted conversations do not expose the internal,
stateful per-token logits control required for statistical application.

## Local LLMs: statistical application

```bash
pip install -e '.[local]'
```

Configure `.env`:

```dotenv
WATERMARK_LOCAL_MODEL='/path/to/local-model'
WATERMARK_INTENSITY_TABLE='private/intensity.json'
WATERMARK_SESSION_PROVIDER='openai'
WATERMARK_PERIOD=64
```

Then start one local session:

```bash
run session-local
```

The command derives the active key in memory, obtains actual next-token logits,
applies the keyed bias before sampling, and reports a statistical score. Type
`/exit` to finish. `APPLIED` is reported only after real logits modification.

## Security boundaries

Never share `.env`, `private/vault.json`, `exports/*/key.env`, a passphrase,
`KEY`, `master_secret`, or `identity_private_key`. Public identity and signed
manifests may be disclosed deliberately, but `public/` is gitignored by default.

Lifecycle commands:

```bash
run status
run rotate --model openai --reason compromised
run revoke --key FMM-OPENAI-01 --reason compromised
run export --model openai
run verify public/manifests/<manifest>.json --identity public/identity.json
```

## Tests

```bash
pytest
```

See [`docs/watermark-interface.md`](docs/watermark-interface.md) for the complete
runtime contract and security invariants.
