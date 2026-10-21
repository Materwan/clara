"""Secrets, encrypted in the database with AES-GCM.

The key never lives in the data folder. It comes, in this order, from `CLARA_SECRET_KEY_FILE`, the systemd credential
`clara-secret-key` (`LoadCredential=` / `LoadCredentialEncrypted=`), or `CLARA_SECRET_KEY` (development only). It is
either

* a **raw key**: exactly 32 random bytes, written as 64 hex characters or as base64; used as it is; or
* a **passphrase**: anything else (at least 16 characters), stretched with scrypt (N = 2^17, r = 8, p = 1) and a
  random 16-byte salt kept in `secrets_meta`. A stolen database alone is then slow to attack offline.

`secrets_meta` also keeps a check value (a fixed text encrypted with the key), so a wrong key is refused at start
instead of failing on the first secret read. Each secret's name is its associated data: a ciphertext cannot be moved
to another name. `rotate()` re-encrypts everything under a new key in one transaction.

The API never returns a secret: only whether it is set, and when it was last changed.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..db import Database
from ..timeutil import now_iso

SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
NONCE_BYTES = 12
MIN_PASSPHRASE = 16
CHECK_TEXT = b"clara secrets store v1"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# What the store holds, and what each one is for (the console and the Admin page show this list)
KNOWN_SECRETS: dict[str, str] = {
    "ollama_api_key": "Ollama cloud API key (provider `cloud`)",
    "gemini_api_key": "Google Gemini API key (provider `gemini`)",
    "deepseek_api_key": "DeepSeek API key (provider `deepseek`)",
    "mistral_api_key": "Mistral API key (provider `mistral`)",
    "opencode_go_api_key": "OpenCode Go API key (provider `opencode-go`)",
    "github_token": "GitHub fine-grained personal access token",
    "google_client_secret": "Google OAuth client secret (Drive and Gmail)",
    "google_refresh_token": "Google refresh token (set by the Drive connection, not by hand)",
    "google_picker_api_key": "Google API key for the Drive Picker",
    "gmail_refresh_token": "Gmail refresh token (set by the Gmail connection, not by hand)",
    "microsoft_client_secret": "Microsoft (Azure) app client secret (Outlook)",
    "outlook_refresh_token": "Outlook refresh token (set by the Outlook connection, not by hand)",
    "vault_git_token": "Token to push the Obsidian vault over HTTPS",
    "restic_password": "Password of the restic repository for remote backups",
    "release_token": "Optional GitHub token for release downloads (public repositories need none)",
}

# Set by a connection flow after you confirm the account, never by hand (an unconfirmed account must not be attachable)
CONNECTION_TOKENS = frozenset({"google_refresh_token", "gmail_refresh_token", "outlook_refresh_token"})


class SecretError(Exception):
    """The store cannot be used: no key, a key that is too weak, or an unknown secret name."""


class WrongKey(SecretError):
    """The key does not open this store."""


@dataclass(frozen=True)
class KeyMaterial:
    """What the key source gave: 32 raw bytes, or a passphrase to stretch."""

    raw: bytes | None = field(default=None, repr=False)
    passphrase: str | None = field(default=None, repr=False)
    origin: str = ""

    @classmethod
    def parse(cls, text: str, origin: str) -> KeyMaterial:
        text = text.strip()
        if re.fullmatch(r"[0-9a-fA-F]{64}", text):
            return cls(raw=bytes.fromhex(text), origin=origin)
        try:
            decoded = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            decoded = b""
        if len(decoded) == 32:
            return cls(raw=decoded, origin=origin)
        if len(text) < MIN_PASSPHRASE:
            raise SecretError(
                f"The secret key from {origin} is too short: give 32 random bytes (64 hex characters) or a passphrase "
                f"of at least {MIN_PASSPHRASE} characters."
            )
        return cls(passphrase=text, origin=origin)


def load_key_material(key_file: Path | None, key_text: str | None) -> KeyMaterial:
    if key_file is not None:
        try:
            return KeyMaterial.parse(key_file.read_text(encoding="utf-8"), str(key_file))
        except OSError as error:
            raise SecretError(f"Cannot read the secret key file {key_file}: {error.strerror}") from None
    if key_text:
        return KeyMaterial.parse(key_text, "CLARA_SECRET_KEY")
    raise SecretError(
        "No secret key: set the systemd credential clara-secret-key, CLARA_SECRET_KEY_FILE, or (development only) "
        "CLARA_SECRET_KEY. `clara secrets new-key` prints a fresh one."
    )


def _derive(material: KeyMaterial, salt: bytes | None, n: int, r: int, p: int) -> bytes:
    if material.raw is not None:
        return material.raw
    assert material.passphrase is not None and salt is not None
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(material.passphrase.encode("utf-8"))


def new_raw_key() -> str:
    """A fresh raw key, as 64 hex characters."""
    return os.urandom(32).hex()


class SecretsStore:
    def __init__(self, db: Database, material: KeyMaterial):
        self.db = db
        self._key = self._open(material)

    # ------------------------------------------------------------------
    # The key
    # ------------------------------------------------------------------
    def _open(self, material: KeyMaterial) -> bytes:
        meta = self.db.one("SELECT * FROM secrets_meta WHERE id = 1")
        if meta is None:
            return self._initialise(material)
        if (meta["kdf"] == "raw") != (material.raw is not None):
            kinds = {"raw": "a raw 32-byte key", "scrypt": "a passphrase"}
            raise WrongKey(f"This store was made with {kinds[meta['kdf']]}, but {material.origin} gives the other kind.")
        key = _derive(material, meta["salt"], meta["n"] or SCRYPT_N, meta["r"] or SCRYPT_R, meta["p"] or SCRYPT_P)
        try:
            AESGCM(key).decrypt(meta["check_nonce"], meta["check_value"], b"check")
        except InvalidTag:
            raise WrongKey(f"The secret key from {material.origin} does not open this secrets store.") from None
        return key

    def _initialise(self, material: KeyMaterial) -> bytes:
        salt = None if material.raw is not None else os.urandom(SALT_BYTES)
        key = _derive(material, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
        nonce = os.urandom(NONCE_BYTES)
        check = AESGCM(key).encrypt(nonce, CHECK_TEXT, b"check")
        kdf = "raw" if material.raw is not None else "scrypt"
        self.db.execute(
            "INSERT INTO secrets_meta (id, kdf, salt, n, r, p, check_nonce, check_value, created_at)"
            " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kdf, salt, SCRYPT_N if salt else None, SCRYPT_R if salt else None, SCRYPT_P if salt else None,
             nonce, check, now_iso()),
        )
        return key

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------
    @staticmethod
    def _check_name(name: str) -> None:
        if not NAME.match(name):
            raise SecretError(f"{name!r} is not a secret name (lower case letters, digits and _).")

    def set(self, name: str, value: str) -> None:
        self._check_name(name)
        if not value:
            raise SecretError("An empty secret: use unset to remove one.")
        nonce = os.urandom(NONCE_BYTES)
        sealed = AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), name.encode())
        now = now_iso()
        self.db.execute(
            "INSERT INTO secrets (name, nonce, ciphertext, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (name) DO UPDATE SET nonce = excluded.nonce, ciphertext = excluded.ciphertext,"
            " updated_at = excluded.updated_at",
            (name, nonce, sealed, now, now),
        )

    def get(self, name: str) -> str | None:
        row = self.db.one("SELECT nonce, ciphertext FROM secrets WHERE name = ?", (name,))
        if row is None:
            return None
        try:
            return AESGCM(self._key).decrypt(row["nonce"], row["ciphertext"], name.encode()).decode("utf-8")
        except InvalidTag:
            raise WrongKey(f"The secret {name} cannot be opened with this key.") from None

    def has(self, name: str) -> bool:
        return self.db.one("SELECT 1 FROM secrets WHERE name = ?", (name,)) is not None

    def unset(self, name: str) -> bool:
        return self.db.execute("DELETE FROM secrets WHERE name = ?", (name,)).rowcount > 0

    def status(self) -> list[dict]:
        """Every known secret and every stored one: set or not, and when it changed. Never the value."""
        stored = {row["name"]: row["updated_at"] for row in self.db.all("SELECT name, updated_at FROM secrets")}
        names = list(KNOWN_SECRETS) + sorted(set(stored) - set(KNOWN_SECRETS))
        return [
            {"name": name, "purpose": KNOWN_SECRETS.get(name, ""), "set": name in stored, "updated_at": stored.get(name)}
            for name in names
        ]

    def rotate(self, material: KeyMaterial) -> int:
        """Re-encrypt every secret under a new key. Returns how many were re-encrypted."""
        values = {row["name"]: self.get(row["name"]) for row in self.db.all("SELECT name FROM secrets")}
        salt = None if material.raw is not None else os.urandom(SALT_BYTES)
        key = _derive(material, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
        if key == self._key:
            raise SecretError("The new key is the same as the current one.")
        nonce = os.urandom(NONCE_BYTES)
        check = AESGCM(key).encrypt(nonce, CHECK_TEXT, b"check")
        now = now_iso()
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE secrets_meta SET kdf = ?, salt = ?, n = ?, r = ?, p = ?, check_nonce = ?, check_value = ?,"
                " rotated_at = ? WHERE id = 1",
                ("raw" if salt is None else "scrypt", salt, SCRYPT_N if salt else None, SCRYPT_R if salt else None,
                 SCRYPT_P if salt else None, nonce, check, now),
            )
            for name, value in values.items():
                assert value is not None
                item_nonce = os.urandom(NONCE_BYTES)
                sealed = AESGCM(key).encrypt(item_nonce, value.encode("utf-8"), name.encode())
                tx.execute(
                    "UPDATE secrets SET nonce = ?, ciphertext = ?, updated_at = ? WHERE name = ?",
                    (item_nonce, sealed, now, name),
                )
        self._key = key
        return len(values)
