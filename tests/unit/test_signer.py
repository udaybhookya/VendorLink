from pathlib import Path

from vl_core.a2a import sign_request, verify_request
from vl_core.crypto import LocalSigner, b64e, verify_signature


async def test_sign_and_verify() -> None:
    signer = LocalSigner()
    sig = await signer.sign("agent:a", b"hello")
    pub = await signer.public_key("agent:a")
    assert verify_signature(pub, b"hello", sig)
    assert not verify_signature(pub, b"hellO", sig)


async def test_other_key_does_not_verify() -> None:
    signer = LocalSigner()
    sig = await signer.sign("agent:a", b"hello")
    assert not verify_signature(await signer.public_key("agent:b"), b"hello", sig)


async def test_keys_persist_in_key_dir(tmp_path: Path) -> None:
    first = await LocalSigner(tmp_path).public_key("vendor-agent:maya")
    second = await LocalSigner(tmp_path).public_key("vendor-agent:maya")
    assert first == second


async def test_request_signature_binds_method_path_body_jti() -> None:
    signer = LocalSigner()
    pub = b64e(await signer.public_key("agent:a"))
    args = ("POST", "/relay/vendor-agent:maya/order.propose", b'{"x":1}', "jti-1")
    sig = await sign_request(signer, "agent:a", *args)
    assert verify_request(pub, *args, sig)
    assert not verify_request(pub, "GET", *args[1:], sig)
    assert not verify_request(pub, args[0], "/relay/other/order.propose", *args[2:], sig)
    assert not verify_request(pub, *args[:2], b'{"x":2}', args[3], sig)
    assert not verify_request(pub, *args[:3], "jti-2", sig)
    assert not verify_request(pub, *args, "not-base64!!")
