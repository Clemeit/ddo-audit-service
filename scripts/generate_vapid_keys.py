"""Generate a VAPID key pair for Web Push.

    python scripts/generate_vapid_keys.py

Prints values for VAPID_PUBLIC_KEY (safe to publish; browsers subscribe with it) and VAPID_PRIVATE_KEY
(secret: put it in the .env file / the VAPID_PRIVATE_KEY GitHub secret, never in the repo).

Use one key pair per environment and keep it: changing the keys invalidates every existing browser
subscription, so users would have to turn push notifications on again.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    print(f"VAPID_PUBLIC_KEY={b64url(public_raw)}")
    print(f"VAPID_PRIVATE_KEY={b64url(private_raw)}")
    print("VAPID_SUBJECT=mailto:<an address the push services can contact about problems>")


if __name__ == "__main__":
    main()
