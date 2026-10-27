"""Content-addressed immutable bytes and retrieval receipts; cache reads never use HTTP."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from phases.phase_01_foundation.http import ServiceError
from phases.phase_01_foundation.sources import digest


def immutable(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("Immutable artifact mismatch")


class Snapshots:
    def __init__(self, root):
        self.root = Path(root)

    def fetch(self, client, source, url, *, params=None, max_bytes=1_000_000):
        if client is None:
            raise ValueError("Live fetch requires an explicit client")
        try:
            with client.stream("GET", url, params=params) as response:
                if response.status_code >= 400:
                    raise ServiceError(f"HTTP_{response.status_code}", blocked=True)
                if response.status_code != 200:
                    raise ServiceError("SOURCE_REDIRECT_REFUSED")
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > max_bytes:
                        raise ServiceError("RESPONSE_TOO_LARGE")
                return self.save(
                    source, str(response.url), bytes(body), response.headers.get("content-type", "")
                )
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("SOURCE_TRANSPORT_FAILED", blocked=True) from None

    def save(self, source, url, body, content_type, *, observed_at=None):
        if source not in ("comtrade", "fda-shortages", "fda-recalls", "curated-document"):
            raise ValueError("Unknown source")
        sha = hashlib.sha256(body).hexdigest()
        receipt = dict(
            source=source,
            url=url,
            body_sha256=sha,
            content_type=content_type,
            observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
        )
        key = digest(receipt)
        immutable(self.root / "raw" / f"{sha}.bin", body)
        immutable(self.root / "receipts" / f"{key}.json", json.dumps(receipt, sort_keys=True).encode())
        return key, receipt, body

    def read(self, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid snapshot id")
        receipt = json.loads((self.root / "receipts" / f"{key}.json").read_text())
        if digest(receipt) != key:
            raise ValueError("Receipt hash mismatch")
        body = (self.root / "raw" / f"{receipt['body_sha256']}.bin").read_bytes()
        if hashlib.sha256(body).hexdigest() != receipt["body_sha256"]:
            raise ValueError("Snapshot hash mismatch")
        return key, receipt, body
