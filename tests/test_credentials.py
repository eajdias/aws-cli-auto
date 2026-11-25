"""Unit tests for pasted credential blocks; all values are fake and runtime-built."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from s3_glacier_restore.credentials import (
    CredentialBlockError,
    parse_env_block,
)


def _access() -> str:
    return "AKIA" + "0" * 16


def _secret() -> str:
    return "aBc+/" * 8


def _block(
    access: str,
    secret: str,
    region: str = "sa-east-1",
    output: str = "json",
    token: str | None = None,
    sep: str = ":",
) -> str:
    lines = [
        f"AWS_ACCESS_KEY_ID{sep} {access}",
        f"AWS_SECRET_ACCESS_KEY{sep} {secret}",
        f"AWS_DEFAULT_REGION{sep} {region}",
        f"AWS_DEFAULT_OUTPUT{sep} {output}",
    ]
    if token is not None:
        lines.append(f"AWS_SESSION_TOKEN{sep} {token}")
    return "\n".join(lines)


def test_parse_env_block_preserves_secret_characters() -> None:
    access, secret = _access(), _secret()

    cred = parse_env_block(_block(access, secret))

    assert cred.access_key_id == access
    assert cred.secret_access_key == secret
    assert cred.region == "sa-east-1"
    assert cred.output == "json"
    assert cred.session_token is None


def test_parse_env_block_accepts_crlf_blank_lines_and_equals() -> None:
    access = _access()
    secret = ("aBc+/=" * 7)[:38] + "=="
    block = "\r\n\r\n".join(
        [
            f"AWS_ACCESS_KEY_ID={access}",
            f"AWS_SECRET_ACCESS_KEY={secret}",
            "AWS_DEFAULT_REGION=sa-east-1",
            "AWS_DEFAULT_OUTPUT=JSON",
            "",
        ]
    )

    cred = parse_env_block(block)

    assert cred.secret_access_key == secret
    assert cred.output == "json"


def test_as_child_env_includes_session_token_without_profile() -> None:
    access, secret, token = _access(), _secret(), "token-" + "1" * 20

    cred = parse_env_block(_block(access, secret, token=token, output="text"))

    assert cred.as_child_env() == {
        "AWS_ACCESS_KEY_ID": access,
        "AWS_SECRET_ACCESS_KEY": secret,
        "AWS_DEFAULT_REGION": "sa-east-1",
        "AWS_DEFAULT_OUTPUT": "text",
        "AWS_SESSION_TOKEN": token,
    }


def test_as_child_env_omits_stale_session_token() -> None:
    access, secret = _access(), _secret()

    cred = parse_env_block(_block(access, secret))

    assert "AWS_SESSION_TOKEN" not in cred.as_child_env()
    assert "AWS_PROFILE" not in cred.as_child_env()


@pytest.mark.parametrize(
    ("mutate", "expected_key"),
    [
        (lambda b: b.replace("AWS_DEFAULT_OUTPUT", "AWS_OTHER_OUTPUT"), "AWS_DEFAULT_OUTPUT"),
        (lambda b: b.replace("AWS_DEFAULT_REGION", "AWS_OTHER_REGION"), "AWS_DEFAULT_REGION"),
        (lambda b: b.replace("sa-east-1", "xx"), "AWS_DEFAULT_REGION"),
        (
            lambda b: b.replace("AWS_DEFAULT_OUTPUT: json", "AWS_DEFAULT_OUTPUT: xml"),
            "AWS_DEFAULT_OUTPUT",
        ),
        (lambda b: b.replace(_access(), "SHORT"), "AWS_ACCESS_KEY_ID"),
    ],
)
def test_parse_env_block_rejects_invalid_blocks(
    mutate: Callable[[str], str], expected_key: str
) -> None:
    block = mutate(_block(_access(), _secret()))

    with pytest.raises(CredentialBlockError, match=expected_key):
        parse_env_block(block)


def test_errors_and_representations_are_redacted() -> None:
    access, secret, token = _access(), _secret(), "token-" + "1" * 20
    cred = parse_env_block(_block(access, secret, token=token))
    texts = [repr(cred), str(cred), cred.masked_summary()]

    with pytest.raises(CredentialBlockError) as invalid:
        parse_env_block(_block(access, secret, region="xx", token=token))
    texts.append(str(invalid.value))

    for text in texts:
        assert access not in text
        assert secret not in text
        assert token not in text
    assert repr(cred) == "[REDACTED] region=sa-east-1"
    assert cred.masked_summary() == "regiao=sa-east-1; credencial preenchida"
