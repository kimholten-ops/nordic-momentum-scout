"""Lager et VAPID-nøkkelpar for web push. Kjør LOKALT (ikke i Actions — da
havner den private nøkkelen i en logg):

    python -m scripts.lag_vapid

- WEBPUSH_VAPID_PRIVATE  -> GitHub secret
- WEBPUSH_VAPID_PUBLIC   -> GitHub variable (ikke hemmelig; dashboardet trenger den)
"""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def main() -> None:
    nokkel = ec.generate_private_key(ec.SECP256R1())
    privat = nokkel.private_numbers().private_value.to_bytes(32, "big")
    offentlig = nokkel.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    print("Secret   WEBPUSH_VAPID_PRIVATE =", _b64(privat))
    print("Variable WEBPUSH_VAPID_PUBLIC  =", _b64(offentlig))


if __name__ == "__main__":
    main()
