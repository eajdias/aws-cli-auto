"""Static checks for the single-file UI: no network, no frameworks, accessible markup."""

from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).parents[1] / "src" / "s3_glacier_restore" / "ui" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _constants(name: str) -> list[str]:
    match = re.search(rf"const {name} = \[(.*?)\];", _html(), re.DOTALL)
    assert match is not None, f"{name} ausente"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_no_external_resources() -> None:
    html = _html()
    for forbidden in ("http://", "https://", "cdn", "<script src=", "<link "):
        assert forbidden not in html


def test_admin_and_client_constants() -> None:
    assert _constants("ADMIN_COMMANDS") == [
        "perms",
        "plan",
        "restore",
        "status",
        "copy",
        "verify",
        "parity",
        "auto",
        "batch",
    ]
    assert _constants("CLIENT_STEPS") == [
        "credentials",
        "selection",
        "destination",
        "progress",
        "result",
    ]


def test_sections_for_every_screen_exist() -> None:
    html = _html()
    for section in (
        "mode",
        "admin-login",
        "admin-panel",
        "client-credentials",
        "client-selection",
        "client-destination",
        "progress",
        "result",
    ):
        assert re.search(rf'<section[^>]+id="{section}"', html) is not None, section


def test_inputs_have_associated_labels() -> None:
    html = _html()
    tags = re.findall(r"<(?:input|textarea|select)\b[^>]*>", html)
    assert tags
    for tag in tags:
        id_match = re.search(r'\bid="([^"]+)"', tag)
        assert id_match is not None, tag
        control_id = id_match.group(1)
        assert re.search(rf'<label[^>]+for="{re.escape(control_id)}"', html) is not None, control_id


def test_buttons_have_accessible_text() -> None:
    html = _html()
    for tag in re.findall(r"<button\b[^>]*>.*?</button>", html, re.DOTALL):
        text = re.sub(r"<[^>]+>", "", tag).strip()
        label = re.search(r'aria-label="([^"]+)"', tag)
        assert text or (label is not None and label.group(1).strip()), tag


def test_admin_password_change_form_exists() -> None:
    html = _html()
    for element_id in (
        "admin-password-form",
        "admin-new-password",
        "admin-confirm-password",
        "admin-change-password",
        "admin-password-message",
    ):
        assert f'id="{element_id}"' in html, element_id
    assert '"/api/admin/password"' in html


def test_progressbar_has_aria_value() -> None:
    html = _html()
    match = re.search(r'<[^>]+role="progressbar"[^>]*>', html)
    assert match is not None
    assert 'aria-valuenow="' in match.group(0)


def test_credentials_are_cleared_after_download_response() -> None:
    html = _html()
    index = html.index('"/api/download"')
    window = html[index : index + 3000]
    assert re.search(r"credentialInput\.value\s*=\s*\"\"", window) is not None


def test_admin_and_client_use_s3_uri_fields() -> None:
    html = _html()
    for element_id in ("admin-src", "admin-dst", "admin-label", "client-dst", "client-label"):
        assert f'id="{element_id}"' in html, element_id
    for legacy_id in (
        "admin-serial",
        "admin-src-bucket",
        "admin-dst-bucket",
        "client-bucket",
        "client-serial",
    ):
        assert f'id="{legacy_id}"' not in html, legacy_id
    assert "s3://" in html
    assert '"/api/download"' in html
    download = html[html.index('"/api/download"') :]
    assert "dst: state.dst" in download
    assert "label: state.label" in download


def test_theme_toggle_is_local_and_persistent() -> None:
    html = _html()
    assert 'id="theme-toggle"' in html
    assert 'aria-pressed="false"' in html
    assert 'data-theme="dark"' in html
    assert "s3-restore-theme" in html
    assert "prefers-color-scheme: dark" in html
    assert "color-scheme: dark" in html


def _dark_tokens() -> dict[str, str]:
    block = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\n  \}', _html(), re.DOTALL)
    assert block is not None, "bloco do tema escuro ausente"
    tokens = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block.group(1)))
    assert tokens
    return tokens


def _luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_dark_theme_text_meets_wcag_aa() -> None:
    tokens = _dark_tokens()
    surface = tokens["surface"]
    for name in ("ink", "ink-soft", "ink-muted", "ink-faint", "accent", "ok", "danger"):
        assert _contrast(tokens[name], surface) >= 4.5, name
