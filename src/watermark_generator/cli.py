"""Command-line interface."""
import argparse
import getpass
import os
import sys
from pathlib import Path
from .core import (PROVIDERS, export_key, generate, initialize, parse_providers,
                   revoke, rotate, verify_manifest)
from .crypto import b64, unb64
from .errors import WatermarkError
from .storage import Store


def password(confirm: bool = False) -> str:
    value = os.environ.get("WATERMARK_GENERATOR_PASSPHRASE")
    if value:
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = Store(args.root)
    try:
        if args.command == "init":
            state = initialize(store, password(True), args.prefix)
            print(f"Identity initialized.\n\nFingerprint:\n{state['identity_fingerprint']}\n\nIMPORTANT: Back up the Identity Private Key offline. Loss prevents future continuity proofs.")
        elif args.command == "rotate":
            old, new, path = rotate(store, password(), args.model, args.reason)
            print(f"{old['key_id']} -> REVOKED\n{new['key_id']} -> ACTIVE\n\nSigned rotation manifest: {path}\nIdentity continuity: SIGNED")
        elif args.command == "revoke":
            path = revoke(store, password(), args.key, args.reason, args.retire)
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
                import json
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
            path = export_key(store, password(), args.model)
            print(path.read_text(), end="")
            print(f"Export saved with mode 0600: {path}", file=sys.stderr)
        elif args.command == "verification-bundle":
            path = store.root / "public" / "identity.json"
            if not path.exists(): raise WatermarkError("Identity is not initialized.")
            print(path)
        else:
            if not args.models or bool(args.watermark) == bool(args.watermark_file):
                parser().error("generation requires --models and exactly one of --watermark/--watermark-file")
            watermark = args.watermark if args.watermark is not None else args.watermark_file.read_text()
            records, manifest = generate(store, password(), watermark, args.models, args.prefix)
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
