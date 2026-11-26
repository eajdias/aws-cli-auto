"""Parse a pasted AWS credential block without ever exposing its values."""

from __future__ import annotations

import re
from dataclasses import dataclass

_REQUIRED = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "AWS_DEFAULT_OUTPUT",
)
_OPTIONAL = "AWS_SESSION_TOKEN"
_ALLOWED = frozenset({*_REQUIRED, _OPTIONAL})
_OUTPUTS = frozenset({"json", "text", "table", "yaml", "yaml-stream"})
_REGION = re.compile(r"[a-z]{2}(-gov)?-[a-z]+-\d+")


class CredentialBlockError(ValueError):
    """Raised with key names only; never carries credential values."""


@dataclass(repr=False)
class EnvCredentials:
    access_key_id: str
    secret_access_key: str
    region: str
    output: str
    session_token: str | None = None

    def __repr__(self) -> str:
        return f"[REDACTED] region={self.region}"

    def masked_summary(self) -> str:
        return f"regiao={self.region}; credencial preenchida"

    def as_child_env(self) -> dict[str, str]:
        env = {
            "AWS_ACCESS_KEY_ID": self.access_key_id,
            "AWS_SECRET_ACCESS_KEY": self.secret_access_key,
            "AWS_DEFAULT_REGION": self.region,
            "AWS_DEFAULT_OUTPUT": self.output,
        }
        if self.session_token:
            env["AWS_SESSION_TOKEN"] = self.session_token
        return env


def parse_env_block(text: str) -> EnvCredentials:
    values: dict[str, str] = {}
    unknown: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        positions = [pos for pos in (line.find(":"), line.find("=")) if pos > 0]
        if not positions:
            raise CredentialBlockError("linha sem separador : ou =")
        index = min(positions)
        key = line[:index].strip()
        value = line[index + 1 :].strip()
        if key not in _ALLOWED:
            unknown.append(key)
            continue
        if key in values:
            raise CredentialBlockError(f"chave duplicada: {key}")
        values[key] = value

    for key in _REQUIRED:
        if not values.get(key):
            raise CredentialBlockError(f"{key} ausente")
    if unknown:
        raise CredentialBlockError(f"chave desconhecida: {unknown[0]}")

    access = values["AWS_ACCESS_KEY_ID"]
    secret = values["AWS_SECRET_ACCESS_KEY"]
    region = values["AWS_DEFAULT_REGION"]
    output = values["AWS_DEFAULT_OUTPUT"].lower()
    if len(access) < 20:
        raise CredentialBlockError("AWS_ACCESS_KEY_ID invalido")
    if len(secret) < 40:
        raise CredentialBlockError("AWS_SECRET_ACCESS_KEY invalido")
    if _REGION.fullmatch(region) is None:
        raise CredentialBlockError("AWS_DEFAULT_REGION invalido")
    if output not in _OUTPUTS:
        raise CredentialBlockError("AWS_DEFAULT_OUTPUT invalido")

    return EnvCredentials(
        access_key_id=access,
        secret_access_key=secret,
        region=region,
        output=output,
        session_token=values.get(_OPTIONAL) or None,
    )
