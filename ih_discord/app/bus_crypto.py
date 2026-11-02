"""AES-GCM protection for per-module private Discord bus payloads."""
import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _key(signing_key, module):
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ih-discord-mqtt-content-v1",
        info=("ih-discord-module:" + str(module)).encode("utf-8"),
    ).derive(str(signing_key).strip().encode("utf-8"))


def _aad(module, topic, body, field):
    return json.dumps(
        ["ih-discord-mqtt-content-v1", module, topic, field,
         body.get("channel_key"), body.get("id"), body.get("msg_id"), body.get("op_id")],
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def encrypt_field(signing_key, module, topic, body, field, value):
    nonce = os.urandom(12)
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    cipher = AESGCM(_key(signing_key, module)).encrypt(nonce, raw, _aad(module, topic, body, field))
    return {
        "v": 1,
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "ciphertext": base64.urlsafe_b64encode(cipher).decode("ascii"),
    }


def decrypt_field(signing_key, module, topic, body, field, envelope):
    if not isinstance(envelope, dict) or envelope.get("v") != 1:
        raise ValueError("bad_envelope")
    try:
        nonce = base64.b64decode(envelope["nonce"], altchars=b"-_", validate=True)
        cipher = base64.b64decode(envelope["ciphertext"], altchars=b"-_", validate=True)
        if len(nonce) != 12 or len(cipher) < 16:
            raise ValueError("bad_envelope")
        raw = AESGCM(_key(signing_key, module)).decrypt(nonce, cipher, _aad(module, topic, body, field))
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("decrypt_failed") from exc


def encrypt_private_event(signing_key, module, topic, body):
    out = dict(body)
    names = ("author_id", "author_name", "content", "msg_ts", "age_s", "reply_to", "attachments")
    private = {name: out.pop(name) for name in names if name in out}
    out["private_encrypted"] = encrypt_field(signing_key, module, topic, out, "private", private)
    return out


def decrypt_private_event(signing_key, module, topic, body):
    out = dict(body)
    if "private_encrypted" not in out:
        raise ValueError("missing_envelope")
    private = decrypt_field(signing_key, module, topic, out, "private", out.pop("private_encrypted"))
    if not isinstance(private, dict) or set(private) - {"author_id", "author_name", "content", "msg_ts", "age_s", "reply_to", "attachments"}:
        raise ValueError("bad_payload")
    out.update(private)
    return out

