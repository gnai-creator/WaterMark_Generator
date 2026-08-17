"""Atomic filesystem persistence and vault handling."""
import json
import os
from pathlib import Path
from typing import Any
from .canonical import canonical_json
from .crypto import decrypt_vault, encrypt_vault
from .errors import WatermarkError


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state_path = self.root / "state.json"
        self.vault_path = self.root / "private" / "vault.json"

    def _read(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise WatermarkError(f"Missing or corrupt local state: {path.name}") from exc

    def state(self) -> dict:
        return self._read(self.state_path)

    def secrets(self, passphrase: str) -> dict:
        return json.loads(decrypt_vault(self._read(self.vault_path), passphrase))

    def write_json(self, path: Path, value: Any, mode: int = 0o644) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(canonical_json(value) + b"\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)

    def save(self, state: dict, secrets: dict, passphrase: str) -> None:
        self.write_json(self.vault_path, encrypt_vault(canonical_json(secrets), passphrase), 0o600)
        self.write_json(self.state_path, state, 0o600)
