import pytest

from app.staging.contracts import KekRef, KekResolver, StagingIntegrityError, StagingSecurityError
from app.staging.crypto import unwrap_dek, wrap_dek


class Keys:
    def __init__(self, values):
        self.values = dict(values)

    def resolve(self, reference, version):
        return self.values[(reference, version)]


def test_kek_resolver_is_runtime_protocol_and_exact_version_round_trip():
    resolver = Keys({("kms/pilot", "v1"): b"1" * 32})
    assert isinstance(resolver, KekResolver)
    kek = KekRef("kms/pilot", "v1")
    wrapped = wrap_dek(b"d" * 32, object_id="1" * 32, kek=kek, resolver=resolver)
    assert unwrap_dek(wrapped, object_id="1" * 32, kek=kek, resolver=resolver) == b"d" * 32
    assert b"d" * 32 not in wrapped.encode("ascii")


def test_wrap_is_domain_bound_to_object_reference_and_exact_kek_version():
    resolver = Keys({("kms/pilot", "v1"): b"1" * 32, ("kms/pilot", "v2"): b"2" * 32})
    wrapped = wrap_dek(
        b"d" * 32, object_id="1" * 32, kek=KekRef("kms/pilot", "v1"), resolver=resolver,
    )
    for object_id, kek in [
        ("2" * 32, KekRef("kms/pilot", "v1")),
        ("1" * 32, KekRef("kms/pilot", "v2")),
        ("1" * 32, KekRef("kms/other", "v1")),
    ]:
        with pytest.raises(StagingIntegrityError):
            unwrap_dek(wrapped, object_id=object_id, kek=kek, resolver=resolver)


def test_key_wrap_is_nonce_free_and_deterministic_for_one_exact_binding():
    resolver = Keys({("kms/pilot", "v1"): b"1" * 32})
    kek = KekRef("kms/pilot", "v1")
    first = wrap_dek(b"d" * 32, object_id="1" * 32, kek=kek, resolver=resolver)
    second = wrap_dek(b"d" * 32, object_id="1" * 32, kek=kek, resolver=resolver)
    assert first == second
    assert len(first) == 56


def test_rotation_requires_exact_old_key_and_has_no_fallback():
    old = KekRef("kms/pilot", "old")
    wrapped = wrap_dek(
        b"d" * 32, object_id="1" * 32, kek=old,
        resolver=Keys({("kms/pilot", "old"): b"o" * 32}),
    )
    rotated = Keys({("kms/pilot", "new"): b"n" * 32})
    with pytest.raises(StagingIntegrityError, match="key_unavailable"):
        unwrap_dek(wrapped, object_id="1" * 32, kek=old, resolver=rotated)
    rotated.values[("kms/pilot", "old")] = b"o" * 32
    assert unwrap_dek(wrapped, object_id="1" * 32, kek=old, resolver=rotated) == b"d" * 32


def test_wrong_missing_and_malformed_keys_fail_without_secret_leak():
    secret = "DO-NOT-LEAK-KEY-MATERIAL"
    class Broken:
        def resolve(self, reference, version):
            raise RuntimeError(secret)

    for resolver in [Broken(), Keys({("kms/pilot", "v1"): b"short"})]:
        with pytest.raises(StagingIntegrityError) as caught:
            wrap_dek(b"d" * 32, object_id="1" * 32,
                     kek=KekRef("kms/pilot", "v1"), resolver=resolver)
        assert secret not in repr(caught.value)


@pytest.mark.parametrize("reference,version", [
    ("", "v1"), ("../key", "v1"), ("kms/pilot", ""), ("kms/pilot", "v 1"),
    ("x" * 129, "v1"), ("kms/pilot", "v" * 65),
])
def test_kek_reference_is_canonical(reference, version):
    with pytest.raises(StagingSecurityError):
        wrap_dek(b"d" * 32, object_id="1" * 32, kek=KekRef(reference, version),
                 resolver=Keys({}))


def test_tampered_wrapped_key_is_rejected():
    resolver = Keys({("kms/pilot", "v1"): b"1" * 32})
    kek = KekRef("kms/pilot", "v1")
    wrapped = wrap_dek(b"d" * 32, object_id="1" * 32, kek=kek, resolver=resolver)
    replacement = "A" if wrapped[-2] != "A" else "B"
    tampered = wrapped[:-2] + replacement + wrapped[-1]
    with pytest.raises(StagingIntegrityError, match="wrapped_key_invalid"):
        unwrap_dek(tampered, object_id="1" * 32, kek=kek, resolver=resolver)


def test_public_wrap_boundary_rejects_noncanonical_object_id_safely():
    with pytest.raises(StagingSecurityError, match="invalid_opaque_id"):
        wrap_dek(
            b"d" * 32, object_id="../secret", kek=KekRef("kms/pilot", "v1"),
            resolver=Keys({("kms/pilot", "v1"): b"1" * 32}),
        )
