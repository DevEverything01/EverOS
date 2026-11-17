"""Tests for the [auth] settings section."""

from __future__ import annotations

import pytest

from everos.config import AuthSettings


def test_bootstrap_env_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVEROS_BOOTSTRAP_TOKEN is honoured when the nested var is unset."""
    monkeypatch.setenv("EVEROS_BOOTSTRAP_TOKEN", "short-form-token")

    settings = AuthSettings()

    assert settings.bootstrap_token is not None
    assert settings.bootstrap_token.get_secret_value() == "short-form-token"


def test_explicit_bootstrap_token_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit value is not overwritten by the alias."""
    monkeypatch.setenv("EVEROS_BOOTSTRAP_TOKEN", "short-form-token")

    settings = AuthSettings(bootstrap_token="explicit-token")

    assert settings.bootstrap_token is not None
    assert settings.bootstrap_token.get_secret_value() == "explicit-token"


def test_auth_required_respects_legacy_open() -> None:
    assert AuthSettings().auth_required is True
    assert AuthSettings(legacy_open=True).auth_required is False
    assert AuthSettings(enabled=False).auth_required is False
