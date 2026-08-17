"""Cryptographic operations, exclusively through mature primitives."""
import base64
import hashlib
import os
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from .errors import WatermarkError


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def new_identity() -> tuple[bytes, bytes]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private, public


def sign(private: bytes, payload: bytes) -> str:
    return b64(Ed25519PrivateKey.from_private_bytes(private).sign(payload))


def verify(public: bytes, payload: bytes, signature: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(unb64(signature), payload)
        return True
    except (InvalidSignature, ValueError):
        return False


def fingerprint(public: bytes) -> str:
    raw = hashlib.sha256(public).hexdigest().upper()[:32]
    return "WG:ED25519:" + "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def derive_provider_key(master: bytes, wid: str, prefix: str, provider: str, generation: int) -> bytes:
    info = f"watermark-generator/v1/provider-key\0{prefix}\0{provider}\0{generation}".encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=wid.encode(), info=info).derive(master)


def encrypt_vault(data: bytes, passphrase: str) -> dict[str, str | int]:
    salt, nonce = os.urandom(16), os.urandom(12)
    key = hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    ciphertext = AESGCM(key).encrypt(nonce, data, b"watermark-generator/v1/vault")
    return {"schema": "watermark-vault/v1", "kdf": "scrypt-n32768-r8-p1", "cipher": "AES-256-GCM", "salt": b64(salt), "nonce": b64(nonce), "ciphertext": b64(ciphertext)}


def decrypt_vault(vault: dict, passphrase: str) -> bytes:
    try:
        key = hashlib.scrypt(passphrase.encode(), salt=unb64(vault["salt"]), n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
        return AESGCM(key).decrypt(unb64(vault["nonce"]), unb64(vault["ciphertext"]), b"watermark-generator/v1/vault")
    except Exception as exc:
        raise WatermarkError("Unable to unlock vault (wrong passphrase or corrupted vault).") from exc
