import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.core.management.base import BaseCommand


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Command(BaseCommand):
    help = "Print a new key pair for push notifications. Run it once, locally: a new pair cuts off every device already subscribed."

    def handle(self, *args, **opts):
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_numbers().private_value.to_bytes(32, "big")
        public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        self.stdout.write(f"VAPID_PUBLIC_KEY={b64url(public)}\nVAPID_PRIVATE_KEY={b64url(private)}")
