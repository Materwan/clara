"""Release signing keys (Ed25519), as the updater checks them.

    python scripts/release_signing.py new                       print a new private key and its public key
    python scripts/release_signing.py sign SHA256SUMS           write SHA256SUMS.sig (key in CLARA_RELEASE_SIGNING_KEY)
    python scripts/release_signing.py verify SHA256SUMS PUBKEY  check SHA256SUMS.sig with the updater's own code

The private key (base64 of 32 raw bytes) belongs in the repository's Actions secret CLARA_RELEASE_SIGNING_KEY and
nowhere else; the public key goes in the machine's CLARA_RELEASE_PUBKEY. With it set, the updater refuses any
release whose SHA256SUMS is not signed by that key.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def new() -> None:
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes_raw()).decode()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    print(f"private (Actions secret CLARA_RELEASE_SIGNING_KEY): {private}")
    print(f"public  (CLARA_RELEASE_PUBKEY on the machine):      {public}")


def sign(path: Path) -> None:
    secret = os.environ.get("CLARA_RELEASE_SIGNING_KEY", "").strip()
    if not secret:
        sys.exit("CLARA_RELEASE_SIGNING_KEY is not set.")
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(secret))
    signature = base64.b64encode(key.sign(path.read_bytes())).decode()
    path.with_name(path.name + ".sig").write_text(signature + "\n", encoding="ascii")
    print(f"signed {path.name}")


def verify(path: Path, public_key: str) -> None:
    from clara.ops.updater import UpdateError, check_signature

    try:
        check_signature(path.read_bytes(), path.with_name(path.name + ".sig").read_bytes(), public_key)
    except UpdateError as error:
        sys.exit(str(error))
    print(f"{path.name}.sig matches the public key")


def main(argv: list[str]) -> None:
    if argv[:1] == ["new"]:
        new()
    elif len(argv) == 2 and argv[0] == "sign":
        sign(Path(argv[1]))
    elif len(argv) == 3 and argv[0] == "verify":
        verify(Path(argv[1]), argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
