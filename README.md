# Watermark Generator

Local tool for creating and protecting watermark keys, maintaining signed
manifests, and using provenance markers with LLMs.

There are two distinct modes:

- **Local LLM:** real statistical application to logits before every token;
- **Online LLM:** visible provenance marker added through a session instruction.

A marker indicates declared provenance. By itself, it does not prove legal
authorship, consent, approval, or endorsement.

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Edit `.env` and define a strong passphrase:

```dotenv
WATERMARK_GENERATOR_PASSPHRASE='a-strong-and-unique-passphrase'
```

`.env` is ignored by Git. Do not share or commit it.

## Create identity and keys

```bash
run init --prefix FMM

run \
  --models all \
  --prefix FMM \
  --watermark-file private/watermark.txt
```

The formula remains in `private/watermark.txt`, which is ignored by Git. The
system stores only its hash in manifests.

## Online LLM: visible session marker

ChatGPT, Codex, Claude, Gemini, and other hosted LLMs do not expose their
internal logits to this tool. Use a visible marker with these services.

Paste the text below at the beginning of the conversation and replace the
placeholders:

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

The repository can also assemble this text from `private/watermark.txt`:

```bash
run session-prompt
```

This command neither uses nor displays the Watermark Key. It displays only the
watermark you deliberately chose to copy into the conversation.

## Local LLM: real statistical watermark

For local Transformers-compatible models, install:

```bash
pip install -e '.[local]'
```

Configure `.env`:

```dotenv
WATERMARK_LOCAL_MODEL='/path/to/local-model'
WATERMARK_INTENSITY_TABLE='private/intensity.json'
WATERMARK_SESSION_PROVIDER='openai'
WATERMARK_PERIOD=64
WATERMARK_CONTEXT_WIDTH=4
WATERMARK_GAMMA=0.5
WATERMARK_STRENGTH=1.0
WATERMARK_MINIMUM_TOKENS=100
```

`private/intensity.json` must contain exactly `WATERMARK_PERIOD` finite,
non-negative numbers calculated from your private function.

Start the session:

```bash
run session-local
```

Type `/exit` to finish. During the session, the program:

1. derives the active key directly from the vault;
2. obtains the local model's actual logits;
3. applies the bias before sampling each token;
4. keeps the secret only in the process;
5. calculates the statistical score of the generated text.

The `FMM: APPLIED` status is used only when logits were actually modified. It
does not constitute confirmation of authorship or endorsement.

## File security

| Path | Contents | Share? |
| --- | --- | --- |
| `.env` | Passphrase and local configuration | **Never** |
| `private/vault.json` | Encrypted private identity and master secret | **Never** |
| `private/watermark.txt` | Your specification | Only if you decide to disclose it |
| `exports/<provider>/key.env` | Operational key | **Never in chats or Git** |
| `public/identity.json` | Public key and fingerprint | May be deliberately disclosed |
| `public/manifests/*.json` | Signed public history | May be deliberately disclosed |

`private/`, `exports/`, `public/`, `.env`, and `state.json` are ignored by Git.
Keep secure offline backups of the vault, state, public identity, and manifests.

## Lifecycle and verification

```bash
run status
run rotate --model openai --reason compromised
run revoke --key FMM-OPENAI-01 --reason compromised
run export --model openai
run verify public/manifests/<manifest>.json --identity public/identity.json
```

Never send `KEY`, `key.env`, `.env`, a passphrase, the vault, `master_secret`,
or `identity_private_key` to a conversation, issue, log, or repository.

## Tests

```bash
pytest
```

Technical details are available in
[`docs/watermark-interface.md`](docs/watermark-interface.md).
