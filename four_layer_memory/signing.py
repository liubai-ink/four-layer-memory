# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Ledger signing.

The hash chain in ``ledger.jsonl`` makes accidental edits visible, but an
attacker who rewrites every record can recompute every hash and produce a
self-consistent forgery. Signing closes that gap: each record carries a
signature over its canonical body, and the store refuses to call the ledger
intact when a signature does not verify.

Two algorithms are supported:

* ``hmac-sha256`` (default, standard library only): the key lives in
  ``<root>/.signing_key`` with best-effort owner-only permissions. Verification
  requires the same key, so it protects against rewriting by anyone who does not
  hold the file - which is the realistic threat for a local single-user store.
* ``ed25519`` (only when the optional ``cryptography`` package is installed):
  the private key stays on the host, the public key is written next to it as
  ``.signing_key.pub`` so a third party can verify an exported bundle without
  being able to forge one.

Neither algorithm protects against a compromised host process that can read the
key file; that is stated in ``docs/THREAT-MODEL.md`` and is not claimed here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import stat

HMAC = "hmac-sha256"
ED25519 = "ed25519"


class SigningError(RuntimeError):
    pass


def canonical_bytes(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k not in ("sig", "sig_alg")}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _key_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".signing_key"


def _pub_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".signing_key.pub"


def keygen(root: str | pathlib.Path, alg: str = HMAC, force: bool = False) -> dict:
    """Create a signing key in ``root``. Returns metadata (never the secret)."""
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    kp = _key_path(root)
    if kp.exists() and not force:
        return {"created": False, "alg": _read_alg(root), "path": str(kp)}

    if alg == HMAC:
        kp.write_text(os.urandom(32).hex() + "\n", encoding="utf-8")
        _chmod_private(kp)
        return {"created": True, "alg": HMAC, "path": str(kp)}

    if alg == ED25519:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except Exception as exc:  # pragma: no cover - optional dependency
            raise SigningError("ed25519 requires the 'cryptography' package") from exc
        private = Ed25519PrivateKey.generate()
        kp.write_bytes(private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        _chmod_private(kp)
        pub = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        _pub_path(root).write_bytes(pub)
        return {"created": True, "alg": ED25519, "path": str(kp), "public": str(_pub_path(root))}

    raise SigningError(f"unknown algorithm: {alg}")


def has_key(root: str | pathlib.Path) -> bool:
    return _key_path(pathlib.Path(root)).exists()


def has_public(root: str | pathlib.Path) -> bool:
    return _pub_path(pathlib.Path(root)).exists()


def material(root: str | pathlib.Path) -> dict:
    """Describe which key material is available for verification.

    ``private`` means signatures can be re-computed here (hmac needs the secret,
    ed25519 needs the private key); ``public`` means only detached verification
    of ed25519 records is possible.  An auditor that has neither must not report
    a signed ledger as verified — that is the F05 fix in v0.7.
    """
    root = pathlib.Path(root)
    if _key_path(root).exists():
        return {"private": True, "public": _pub_path(root).exists(),
                "alg": algorithm(root)}
    if _pub_path(root).exists():
        return {"private": False, "public": True, "alg": ED25519}
    return {"private": False, "public": False, "alg": None}


def verify_with_material(root: str | pathlib.Path, record: dict,
                         mat: dict) -> tuple[bool, str]:
    """Verify one record against available material; return (ok, reason).

    Corrupt or unusable key material is a verification failure, never a pass:
    an auditor must not treat "cannot check" as "checks out" (v0.7 F05).
    """
    sig = record.get("sig")
    if not sig:
        if mat.get("private") or mat.get("public"):
            # A key is installed, so every record should be signed; an unsigned
            # record is either an old unsigned record or a stripped signature.
            return False, "signature missing"
        return True, "unsigned"
    alg = record.get("sig_alg")
    root = pathlib.Path(root)
    if mat.get("private"):
        if mat.get("alg") != alg:
            return False, f"signature algorithm {alg!r} does not match key material"
        try:
            expected = _sign_bytes(root, canonical_bytes(record))
        except SigningError:
            return False, "signing material present but unusable"
        return hmac.compare_digest(sig, expected), "private material"
    if mat.get("public"):
        if alg != ED25519:
            return False, "only public key material present; hmac records cannot be verified"
        return verify_with_public(root, record), "public material"
    return False, "signature present but no key material available"




def algorithm(root: str | pathlib.Path) -> str | None:
    root = pathlib.Path(root)
    if not _key_path(root).exists():
        return None
    if _pub_path(root).exists():
        return ED25519
    return HMAC


def sign(root: str | pathlib.Path, record: dict) -> dict:
    """Return ``record`` with ``sig`` and ``sig_alg`` added."""
    root = pathlib.Path(root)
    alg = algorithm(root)
    if alg is None:
        return dict(record)
    body = canonical_bytes(record)
    signed = dict(record)
    signed["sig_alg"] = alg
    signed["sig"] = _sign_bytes(root, body)
    return signed


def verify(root: str | pathlib.Path, record: dict) -> bool:
    """Verify a record with the private key, or with the published key if only that is present."""
    root = pathlib.Path(root)
    alg = algorithm(root)
    if alg is None and _pub_path(root).exists():
        return verify_with_public(root, record)
    sig = record.get("sig")
    if alg is None:
        return sig is None
    if not sig or record.get("sig_alg") != alg:
        return False
    try:
        expected = _sign_bytes(root, canonical_bytes(record))
    except SigningError:
        return False
    return hmac.compare_digest(sig, expected)


def verify_with_public(root: str | pathlib.Path, record: dict) -> bool:
    """Verify an Ed25519-signed record using only the public key file."""
    root = pathlib.Path(root)
    if record.get("sig_alg") != ED25519 or not record.get("sig"):
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(_pub_path(root).read_bytes())
        pub.verify(bytes.fromhex(record["sig"]), canonical_bytes(record))
        return True
    except Exception:
        return False


def public_key_hex(root: str | pathlib.Path) -> str:
    root = pathlib.Path(root)
    alg = algorithm(root)
    if alg == ED25519:
        return _pub_path(root).read_bytes().hex()
    if alg == HMAC:
        # symmetric keys are not published; expose a fingerprint instead
        secret = _key_path(root).read_text(encoding="utf-8").strip()
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]
    return ""


def _read_alg(root: pathlib.Path) -> str:
    return ED25519 if _pub_path(root).exists() else HMAC


def _sign_bytes(root: pathlib.Path, body: bytes) -> str:
    kp = _key_path(root)
    if not kp.exists():
        raise SigningError("no signing key; run `keygen` first")
    raw = kp.read_bytes()
    if _pub_path(root).exists():
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except Exception as exc:  # pragma: no cover
            raise SigningError("ed25519 key present but 'cryptography' missing") from exc
        return Ed25519PrivateKey.from_private_bytes(raw).sign(body).hex()
    secret = raw.decode("utf-8").strip().encode("utf-8")
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _chmod_private(path: pathlib.Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - ACL-managed filesystems
        pass
