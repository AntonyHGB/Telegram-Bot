import pytest

import bot_tele_parrot as bot


@pytest.fixture(autouse=True)
def allow_test_user(monkeypatch):
    """Default the test user as authorized; tests override it when needed."""
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
