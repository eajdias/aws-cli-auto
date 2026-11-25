"""boto3 session/client factory with sane retries. No credential handling here."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

S3Client = Any
STSClient = Any

_RETRY_CONFIG = Config(
    retries={"max_attempts": 5, "mode": "standard"},
    connect_timeout=10,
    read_timeout=60,
    user_agent_extra="s3-glacier-restore/0.1.0",
)


def build_session(profile: str, region: str) -> boto3.Session:
    """Empty/env profile means credentials come only from the child environment."""
    profile_name = None if profile in ("", "env") else profile
    return boto3.Session(profile_name=profile_name, region_name=region)


def s3_client(session: boto3.Session, region: str) -> S3Client:
    return session.client("s3", region_name=region, config=_RETRY_CONFIG)


def sts_client(session: boto3.Session, region: str) -> STSClient:
    return session.client("sts", region_name=region, config=_RETRY_CONFIG)


def split_aws_error(exc: BaseException) -> tuple[str, str]:
    """Return (code, message) for a boto3 ClientError, else (TypeName, str)."""
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", "ClientError"))
        msg = str(exc.response.get("Error", {}).get("Message", str(exc)))
        return code, msg
    return type(exc).__name__, str(exc)
