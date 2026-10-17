"""Ed25519 signing behind one interface: LocalSigner (dev/tests) or KmsSigner (Cloud KMS)."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from vl_core.settings import Settings, get_settings


class Signer(Protocol):
    async def sign(self, key_id: str, data: bytes) -> bytes: ...

    async def public_key(self, key_id: str) -> bytes:
        """Raw 32-byte Ed25519 public key for `key_id`, creating the key if it is new."""
        ...

    async def promote(self, from_key_id: str, to_key_id: str) -> None:
        """Make the key under `from_key_id` the key for `to_key_id` (key rotation).
        Rotation always stages under `f"{key_id}#next"` and promotes onto `key_id`."""
        ...


def verify_signature(public_key: bytes, data: bytes, signature: bytes) -> bool:
    """Verify an Ed25519 signature. Works for keys from either signer backend."""
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, data)
    except (InvalidSignature, ValueError):
        return False
    return True


class LocalSigner:
    """Ed25519 keys held in memory, optionally persisted under `key_dir` to survive restarts."""

    def __init__(self, key_dir: Path | None = None) -> None:
        self._keys: dict[str, Ed25519PrivateKey] = {}
        self._key_dir = key_dir

    def _key_path(self, key_id: str) -> Path | None:
        if self._key_dir is None:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key_id)
        return self._key_dir / f"{safe}.key"

    def _key(self, key_id: str) -> Ed25519PrivateKey:
        key = self._keys.get(key_id)
        if key is not None:
            return key
        path = self._key_path(key_id)
        if path is not None and path.exists():
            key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
        else:
            key = Ed25519PrivateKey.generate()
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(
                    key.private_bytes(
                        serialization.Encoding.Raw,
                        serialization.PrivateFormat.Raw,
                        serialization.NoEncryption(),
                    )
                )
                path.chmod(0o600)
        self._keys[key_id] = key
        return key

    async def sign(self, key_id: str, data: bytes) -> bytes:
        return self._key(key_id).sign(data)

    async def public_key(self, key_id: str) -> bytes:
        return (
            self._key(key_id)
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )

    async def promote(self, from_key_id: str, to_key_id: str) -> None:
        key = self._key(from_key_id)
        self._keys[to_key_id] = key
        self._keys.pop(from_key_id, None)
        src, dst = self._key_path(from_key_id), self._key_path(to_key_id)
        if src is not None and dst is not None and src.exists():
            src.replace(dst)


STAGING = "#next"
_KMS_ID = re.compile(r"[^A-Za-z0-9_-]")


def kms_key_id(namespace: str, key_id: str) -> str:
    """A valid, collision-free KMS crypto key id ([A-Za-z0-9_-]{1,63}) for one of our key ids."""
    readable = _KMS_ID.sub("_", f"{namespace}-{key_id}")
    if readable == f"{namespace}-{key_id}" and len(readable) <= 63:
        return readable
    return f"{readable[:52]}-{hashlib.sha256(key_id.encode()).hexdigest()[:10]}"


def _version_no(name: str) -> int:
    return int(name.rsplit("/", 1)[1])


class KmsSigner:
    """Cloud KMS EC_SIGN_ED25519 keys, one crypto key per key id; private keys never leave KMS.

    Rotation: `public_key("x#next")` adds a new version to x's crypto key; `promote` disables
    the older versions. The active version is the *oldest* enabled one, so a restart between the
    two steps keeps signing with the old key until the gateway has accepted the new one.
    """

    def __init__(self, settings: Settings, namespace: str, client: Any = None) -> None:
        ring = settings.kms_keyring
        if not ring:
            raise ValueError("VL_SIGNER=kms needs KMS_KEYRING")
        if not ring.startswith("projects/"):
            ring = (
                f"projects/{settings.google_cloud_project}/locations/"
                f"{settings.google_cloud_location or 'global'}/keyRings/{ring}"
            )
        self.keyring = ring
        self.namespace = namespace
        if client is None:
            from google.cloud import kms

            client = kms.KeyManagementServiceAsyncClient()
        self._client = client
        self._public: dict[str, bytes] = {}  # version name → raw public key

    def _key_name(self, key_id: str) -> str:
        return f"{self.keyring}/cryptoKeys/{kms_key_id(self.namespace, key_id)}"

    async def _enabled_versions(self, key_name: str) -> list[str]:
        pager = await self._client.list_crypto_key_versions(
            request={"parent": key_name, "filter": "state=ENABLED"}
        )
        return sorted([v.name async for v in pager], key=_version_no)

    async def _wait_enabled(self, version: str) -> None:
        for _ in range(60):  # Ed25519 generation takes a few seconds at most
            got = await self._client.get_crypto_key_version(request={"name": version})
            if _state(got) == "ENABLED":
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(f"KMS key version {version} did not become ENABLED")

    async def _ensure_key(self, key_name: str) -> list[str]:
        versions = await self._enabled_versions(key_name)
        if versions:
            return versions
        try:
            await self._client.create_crypto_key(
                request={
                    "parent": self.keyring,
                    "crypto_key_id": key_name.rsplit("/", 1)[1],
                    "crypto_key": {
                        "purpose": "ASYMMETRIC_SIGN",
                        "version_template": {"algorithm": "EC_SIGN_ED25519"},
                    },
                }
            )
            first = f"{key_name}/cryptoKeyVersions/1"
        except Exception as exc:  # AlreadyExists with every version disabled: add one
            if "AlreadyExists" not in type(exc).__name__ and "already exists" not in str(exc):
                raise
            created = await self._client.create_crypto_key_version(
                request={"parent": key_name, "crypto_key_version": {}}
            )
            first = created.name
        await self._wait_enabled(first)
        return [first]

    async def _version(self, key_id: str) -> str:
        """The version that signs for `key_id` (or the staged one for `key_id#next`)."""
        base, staged = key_id.removesuffix(STAGING), key_id.endswith(STAGING)
        key_name = self._key_name(base)
        versions = await self._ensure_key(key_name)
        if not staged:
            return versions[0]
        if len(versions) > 1:
            return versions[-1]
        created = await self._client.create_crypto_key_version(
            request={"parent": key_name, "crypto_key_version": {}}
        )
        await self._wait_enabled(created.name)
        return str(created.name)

    async def sign(self, key_id: str, data: bytes) -> bytes:
        version = await self._version(key_id)
        # Ed25519 in KMS signs the message itself (no pre-hashed digest).
        response = await self._client.asymmetric_sign(request={"name": version, "data": data})
        return bytes(response.signature)

    async def public_key(self, key_id: str) -> bytes:
        version = await self._version(key_id)
        if version not in self._public:
            response = await self._client.get_public_key(request={"name": version})
            key = serialization.load_pem_public_key(response.pem.encode())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError(f"{version} is not an Ed25519 key")
            self._public[version] = key.public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        return self._public[version]

    async def promote(self, from_key_id: str, to_key_id: str) -> None:
        if from_key_id != f"{to_key_id}{STAGING}":
            raise ValueError("KMS rotation promotes only <key_id>#next onto <key_id>")
        versions = await self._enabled_versions(self._key_name(to_key_id))
        for old in versions[:-1]:
            await self._client.update_crypto_key_version(
                request={
                    "crypto_key_version": {"name": old, "state": "DISABLED"},
                    "update_mask": {"paths": ["state"]},
                }
            )


def _state(version: Any) -> str:
    state = version.state
    return str(getattr(state, "name", state))


def get_signer(namespace: str, settings: Settings | None = None) -> Signer:
    """Signer for one tenant (`vendor`, `retailer`, `gateway`); tenants never share key stores."""
    settings = settings or get_settings()
    if settings.vl_signer == "kms":
        return KmsSigner(settings, namespace)
    return LocalSigner(settings.vl_data_dir / "keys" / namespace)
