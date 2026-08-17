"""Command-line interface."""
import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path
from .core import (PROVIDERS, active_key_material, export_key, generate, initialize, now, parse_providers,
                   revoke, rotate, verify_manifest)
from .crypto import b64, unb64
from .errors import WatermarkError
from .storage import Store
from .adapter import TableIntensity, WatermarkAdapter, WatermarkConfig


_DOTENV_KEYS = {
    "WATERMARK_GENERATOR_PASSPHRASE", "WATERMARK_LOCAL_MODEL",
    "WATERMARK_INTENSITY_TABLE", "WATERMARK_SESSION_PROVIDER",
    "WATERMARK_PERIOD", "WATERMARK_CONTEXT_WIDTH", "WATERMARK_GAMMA",
    "WATERMARK_STRENGTH", "WATERMARK_MINIMUM_TOKENS",
}


def _dotenv_values(root: Path) -> dict[str, str]:
    """Read only explicitly supported settings from a local .env file."""
    path = root / ".env"
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key in _DOTENV_KEYS:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                result[key] = value
    return result


def password(root: Path, confirm: bool = False) -> str:
    value = os.environ.get("WATERMARK_GENERATOR_PASSPHRASE")
    if not value:
        value = _dotenv_values(root).get("WATERMARK_GENERATOR_PASSPHRASE")
    if value:
        if len(value) < 12:
            raise WatermarkError("Passphrase from environment or .env must contain at least 12 characters.")
        return value
    value = getpass.getpass("Vault passphrase: ")
    if confirm and value != getpass.getpass("Confirm passphrase: "):
        raise WatermarkError("Passphrases do not match.")
    if len(value) < 12:
        raise WatermarkError("Passphrase must contain at least 12 characters.")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run", description="Identity-backed watermark key lifecycle manager")
    p.add_argument("--root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    p.add_argument("--models", help="all or comma-separated providers")
    p.add_argument("--watermark")
    p.add_argument("--watermark-file", type=Path)
    p.add_argument("--prefix")
    p.add_argument("--show-secrets", action="store_true")
    sub = p.add_subparsers(dest="command")
    init = sub.add_parser("init"); init.add_argument("--prefix", default="MTW")
    rot = sub.add_parser("rotate"); rot.add_argument("--model", required=True, choices=PROVIDERS); rot.add_argument("--reason", default="manual")
    rev = sub.add_parser("revoke"); rev.add_argument("--key", required=True); rev.add_argument("--reason", required=True); rev.add_argument("--retire", action="store_true")
    sub.add_parser("status")
    ver = sub.add_parser("verify"); ver.add_argument("manifest", type=Path); ver.add_argument("--identity", type=Path)
    exp = sub.add_parser("export"); exp.add_argument("--model", required=True, choices=PROVIDERS)
    sub.add_parser("verification-bundle")
    apply = sub.add_parser("apply", help="apply a local bias to a logits vector")
    _adapter_arguments(apply, include_logits=True)
    apply.add_argument("--disabled", action="store_true")
    detect = sub.add_parser("detect", help="score a token sequence locally")
    _adapter_arguments(detect, include_logits=False)
    detect.add_argument("--prefix-tokens", type=Path)
    local = sub.add_parser("generate-local", help="generate with a local Transformers causal LM")
    local.add_argument("--model", required=True)
    prompt = local.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    local.add_argument("--document-id", required=True)
    local.add_argument("--timestamp", required=True)
    local.add_argument("--intensity-table", required=True, type=Path)
    local.add_argument("--protocol-version", default="FMM-0.1")
    local.add_argument("--period", type=int, default=64)
    local.add_argument("--context-width", type=int, default=4)
    local.add_argument("--gamma", type=float, default=0.5)
    local.add_argument("--strength", type=float, default=1.0)
    local.add_argument("--minimum-tokens", type=int, default=100)
    local.add_argument("--max-new-tokens", type=int, default=128)
    local.add_argument("--temperature", type=float, default=0.8)
    local.add_argument("--top-k", type=int, default=0)
    local.add_argument("--top-p", type=float, default=1.0)
    local.add_argument("--random-seed", type=int)
    local.add_argument("--device", default="auto")
    local.add_argument("--allow-download", action="store_true")
    visible = sub.add_parser("session-prompt", help="create a visible per-session provenance prompt")
    visible.add_argument("--watermark-file", type=Path)
    session = sub.add_parser("session-local", help="start a watermarked local-model session")
    session.add_argument("--model")
    session.add_argument("--provider", choices=PROVIDERS)
    session.add_argument("--intensity-table", type=Path)
    session.add_argument("--prompt")
    session.add_argument("--max-new-tokens", type=int, default=200)
    session.add_argument("--temperature", type=float, default=0.8)
    session.add_argument("--top-k", type=int, default=0)
    session.add_argument("--top-p", type=float, default=1.0)
    session.add_argument("--random-seed", type=int)
    session.add_argument("--device", default="auto")
    session.add_argument("--allow-download", action="store_true")
    rewrite = sub.add_parser("rewrite-local", help="rewrite online-LLM text with a local statistical watermark")
    rewrite.add_argument("--input", required=True, type=Path)
    rewrite.add_argument("--output", type=Path)
    rewrite.add_argument("--model")
    rewrite.add_argument("--provider", choices=PROVIDERS)
    rewrite.add_argument("--intensity-table", type=Path)
    rewrite.add_argument("--document-id")
    rewrite.add_argument("--timestamp")
    rewrite.add_argument("--max-new-tokens", type=int, default=512)
    rewrite.add_argument("--temperature", type=float, default=0.5)
    rewrite.add_argument("--top-k", type=int, default=0)
    rewrite.add_argument("--top-p", type=float, default=0.95)
    rewrite.add_argument("--random-seed", type=int)
    rewrite.add_argument("--device", default="auto")
    rewrite.add_argument("--allow-download", action="store_true")
    return p


def _adapter_arguments(command: argparse.ArgumentParser, *, include_logits: bool) -> None:
    command.add_argument("--document-id", required=True)
    command.add_argument("--timestamp", required=True)
    command.add_argument("--tokens", required=True, type=Path)
    if include_logits:
        command.add_argument("--logits", required=True, type=Path)
    command.add_argument("--intensity-table", required=True, type=Path)
    command.add_argument("--protocol-version", default="FMM-0.1")
    command.add_argument("--period", type=int, default=64)
    command.add_argument("--context-width", type=int, default=4)
    command.add_argument("--gamma", type=float, default=0.5)
    command.add_argument("--strength", type=float, default=1.0)
    command.add_argument("--minimum-tokens", type=int, default=100)


def _json_array(path: Path, label: str) -> list:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WatermarkError(f"{label} file is unreadable or invalid JSON.") from exc
    if not isinstance(value, list):
        raise WatermarkError(f"{label} must be a JSON array.")
    return value


def _adapter(args: argparse.Namespace) -> WatermarkAdapter:
    config = WatermarkConfig(args.protocol_version, args.period, args.context_width,
                             args.gamma, args.strength, args.minimum_tokens)
    intensity = TableIntensity(tuple(_json_array(args.intensity_table, "Intensity table")))
    return WatermarkAdapter.from_environment(intensity, config)


def _setting(root: Path, name: str, explicit=None, default=None):
    if explicit is not None:
        return explicit
    return os.environ.get(name) or _dotenv_values(root).get(name) or default


def _session_config(root: Path) -> WatermarkConfig:
    try:
        return WatermarkConfig(
            period=int(_setting(root, "WATERMARK_PERIOD", default="64")),
            context_width=int(_setting(root, "WATERMARK_CONTEXT_WIDTH", default="4")),
            gamma=float(_setting(root, "WATERMARK_GAMMA", default="0.5")),
            strength=float(_setting(root, "WATERMARK_STRENGTH", default="1.0")),
            minimum_tokens=int(_setting(root, "WATERMARK_MINIMUM_TOKENS", default="100")),
        )
    except (TypeError, ValueError) as exc:
        raise WatermarkError("Invalid local session settings.") from exc


def _visible_session_prompt(specification: str | None) -> str:
    marker = specification.strip() if specification else "[INSIRA_AQUI_SUA_MARCA_VISIVEL_AUTORIZADA]"
    return f"""VISIBLE PROVENANCE SESSION

For this session, append the following visible provenance marker to each
substantive natural-language response, without changing code, quotations,
equations, hashes, URLs, identifiers, or structured data:

{marker}

This is an explicit visible marker only. Do not claim that a hidden statistical
watermark was applied. Provenance does not imply consent, approval, authorship,
or endorsement. Report the status as: FMM: VISIBLE MARK ONLY
"""


def _adapter_from_vault(store: Store, provider: str, intensity: TableIntensity,
                        config: WatermarkConfig) -> WatermarkAdapter:
    """Derive an active key and expose it to the adapter only during construction."""
    key_id, key = active_key_material(store, password(store.root), provider)
    previous_key, previous_id = os.environ.get("KEY"), os.environ.get("KEY_ID")
    try:
        os.environ["KEY"], os.environ["KEY_ID"] = b64(key), key_id
        return WatermarkAdapter.from_environment(intensity, config)
    finally:
        if previous_key is None: os.environ.pop("KEY", None)
        else: os.environ["KEY"] = previous_key
        if previous_id is None: os.environ.pop("KEY_ID", None)
        else: os.environ["KEY_ID"] = previous_id


def _local_settings(store: Store, args: argparse.Namespace) -> tuple[str, str, TableIntensity, WatermarkConfig]:
    model = _setting(store.root, "WATERMARK_LOCAL_MODEL", args.model)
    provider = _setting(store.root, "WATERMARK_SESSION_PROVIDER", args.provider)
    intensity_path = _setting(store.root, "WATERMARK_INTENSITY_TABLE", args.intensity_table)
    if not model or not provider or not intensity_path:
        raise WatermarkError("Configure local model, session provider, and intensity table in .env or arguments.")
    table_path = Path(intensity_path)
    if not table_path.is_absolute(): table_path = store.root / table_path
    model_path = Path(model)
    if not model_path.is_absolute() and not args.allow_download:
        model = str(store.root / model_path)
    config = _session_config(store.root)
    intensity = TableIntensity(tuple(_json_array(table_path, "Intensity table")))
    return model, provider, intensity, config


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = Store(args.root)
    try:
        if args.command == "init":
            state = initialize(store, password(store.root, True), args.prefix)
            print(f"Identity initialized.\n\nFingerprint:\n{state['identity_fingerprint']}\n\nIMPORTANT: Back up the Identity Private Key offline. Loss prevents future continuity proofs.")
        elif args.command == "rotate":
            old, new, path = rotate(store, password(store.root), args.model, args.reason)
            print(f"{old['key_id']} -> REVOKED\n{new['key_id']} -> ACTIVE\n\nSigned rotation manifest: {path}\nIdentity continuity: SIGNED")
        elif args.command == "revoke":
            path = revoke(store, password(store.root), args.key, args.reason, args.retire)
            print(f"Key transitioned to {'RETIRED' if args.retire else 'REVOKED'}.\nSigned manifest: {path}")
        elif args.command == "status":
            state = store.state(); wid = state.get("current_watermark_id")
            print(f"Protocol: {state['default_prefix']}\nWatermark: {wid or 'none'}\nIdentity root: {state['identity_fingerprint']}")
            if wid:
                for provider, history in state["watermarks"][wid]["providers"].items():
                    print(f"\n{provider.upper()}")
                    for record in history: print(f"{record['key_id']:<24} {record['status']}")
        elif args.command == "verify":
            trusted = None
            if args.identity:
                trusted = unb64(json.loads(args.identity.read_text())["identity_public_key"])
            valid, manifest = verify_manifest(args.manifest, trusted)
            rollback = False
            if valid and store.state_path.exists() and manifest.get("identity_public_key") == store.state().get("identity_public_key"):
                rollback = manifest.get("sequence", -1) < store.state()["manifest_sequence"]
            if rollback:
                print("ROLLBACK REJECTED\nSignature: VALID\nContinuity: REJECTED (older sequence)")
                return 3
            print(f"SIGNATURE: {'VALID' if valid else 'INVALID'}\nIdentity: {manifest.get('identity_public_key','unknown')}\nWatermark: {manifest.get('watermark_id','genesis')}\nProvider: {manifest.get('provider','n/a')}\nContinuity: {'VERIFIED' if valid else 'FAILED'}")
            return 0 if valid else 2
        elif args.command == "export":
            path = export_key(store, password(store.root), args.model)
            print(path.read_text(), end="")
            print(f"Export saved with mode 0600: {path}", file=sys.stderr)
        elif args.command == "verification-bundle":
            path = store.root / "public" / "identity.json"
            if not path.exists(): raise WatermarkError("Identity is not initialized.")
            print(path)
        elif args.command == "apply":
            adapter = _adapter(args)
            result = adapter.apply(_json_array(args.logits, "Logits"),
                                   _json_array(args.tokens, "Tokens"),
                                   args.document_id, args.timestamp,
                                   enabled=not args.disabled)
            print(json.dumps({"status": "APPLIED" if result.applied else "DISABLED",
                              "logits": result.logits, "position": result.position,
                              "favored_token_count": result.favored_token_count,
                              "key_id": result.key_id}, separators=(",", ":")))
        elif args.command == "detect":
            adapter = _adapter(args)
            result = adapter.detect(_json_array(args.tokens, "Tokens"),
                                    args.document_id, args.timestamp,
                                    _json_array(args.prefix_tokens, "Prefix tokens") if args.prefix_tokens else ())
            print(json.dumps({"status": "SCORE_COMPUTED" if result.sufficient_sample else "INSUFFICIENT_SAMPLE",
                              "z_score": result.z_score, "token_count": result.token_count,
                              "favored_token_count": result.favored_token_count,
                              "weighted_score": result.weighted_score,
                              "sufficient_sample": result.sufficient_sample,
                              "key_id": result.key_id}, separators=(",", ":")))
        elif args.command == "generate-local":
            from .local_runtime import GenerationConfig, TransformersRuntime, generate_local
            adapter = _adapter(args)
            prompt_text = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
            runtime = TransformersRuntime(args.model, device=args.device, allow_download=args.allow_download)
            generation = GenerationConfig(args.max_new_tokens, args.temperature, args.top_k,
                                          args.top_p, args.random_seed)
            result = generate_local(runtime, adapter, prompt_text, args.document_id,
                                    args.timestamp, generation)
            print(json.dumps({"status": result.status, "text": result.text,
                              "prompt_token_ids": result.prompt_token_ids,
                              "generated_token_ids": result.generated_token_ids,
                              "applied_steps": result.applied_steps,
                              "key_id": result.key_id,
                              "detection": {"z_score": result.detection.z_score,
                                  "token_count": result.detection.token_count,
                                  "favored_token_count": result.detection.favored_token_count,
                                  "weighted_score": result.detection.weighted_score,
                                  "sufficient_sample": result.detection.sufficient_sample}},
                             ensure_ascii=False, separators=(",", ":")))
        elif args.command == "session-prompt":
            specification = None
            source = args.watermark_file
            if source is None:
                default_source = store.root / "private" / "watermark.txt"
                source = default_source if default_source.is_file() else None
            if source is not None:
                specification = source.read_text(encoding="utf-8")
            print(_visible_session_prompt(specification))
        elif args.command == "session-local":
            from .local_runtime import GenerationConfig, TransformersRuntime, generate_local
            model, provider, intensity, config = _local_settings(store, args)
            adapter = _adapter_from_vault(store, provider, intensity, config)
            runtime = TransformersRuntime(model, device=args.device, allow_download=args.allow_download)
            generation = GenerationConfig(args.max_new_tokens, args.temperature, args.top_k,
                                          args.top_p, args.random_seed)
            session_id, turn, history = secrets.token_hex(12), 0, ""
            print("Local watermarked session started. Type /exit to finish.", file=sys.stderr)
            pending = args.prompt
            while True:
                if pending is None:
                    try: pending = input("you> ")
                    except EOFError: break
                if pending.strip().lower() in {"/exit", "/quit"}: break
                turn += 1
                model_prompt = history + f"User: {pending}\nAssistant:"
                result = generate_local(runtime, adapter, model_prompt,
                                        f"{session_id}-{turn}", now(), generation)
                print(result.text)
                sample = "sufficient" if result.detection.sufficient_sample else "insufficient"
                print(f"FMM: APPLIED | z={result.detection.z_score:.4f} | sample={sample}", file=sys.stderr)
                history = model_prompt + result.text + "\n"
                pending = None
        elif args.command == "rewrite-local":
            from .local_runtime import GenerationConfig, TransformersRuntime, rewrite_local
            model, provider, intensity, config = _local_settings(store, args)
            adapter = _adapter_from_vault(store, provider, intensity, config)
            try:
                source_text = args.input.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise WatermarkError("Input text is unreadable.") from exc
            runtime = TransformersRuntime(model, device=args.device, allow_download=args.allow_download)
            generation = GenerationConfig(args.max_new_tokens, args.temperature, args.top_k,
                                          args.top_p, args.random_seed)
            result = rewrite_local(runtime, adapter, source_text,
                                   args.document_id or f"rewrite-{secrets.token_hex(12)}",
                                   args.timestamp or now(), generation)
            if args.output:
                output_path = args.output if args.output.is_absolute() else store.root / args.output
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(result.text, encoding="utf-8")
                print(output_path)
            else:
                print(result.text)
            sample = "sufficient" if result.detection.sufficient_sample else "insufficient"
            print(f"FMM: APPLIED | z={result.detection.z_score:.4f} | sample={sample}", file=sys.stderr)
        else:
            if not args.models or bool(args.watermark) == bool(args.watermark_file):
                parser().error("generation requires --models and exactly one of --watermark/--watermark-file")
            watermark = args.watermark if args.watermark is not None else args.watermark_file.read_text()
            records, manifest = generate(store, password(store.root), watermark, args.models, args.prefix)
            state = store.state()
            print(f"Watermark Generator\n\nWatermark ID:\n{state['current_watermark_id']}\n\nIdentity:\n{state['identity_fingerprint']}\n\nGenerated:")
            for record in records:
                print(record["key_id"] + (" (existing)" if not record["created"] else ""))
                if args.show_secrets:
                    print("WARNING: WATERMARK SECRET EXPOSED BY EXPLICIT REQUEST")
                    print("KEY=" + b64(record["secret"]))
            print(f"\nSecrets saved securely.\nManifest: {manifest or 'no new manifest'}\nSignature: VALID")
        return 0
    except WatermarkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: operation failed safely ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
