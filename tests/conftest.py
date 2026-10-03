import pytest

import bot_tele_parrot as bot
import price_history


@pytest.fixture(autouse=True)
def allow_test_user(monkeypatch):
    """Default the test user as authorized; tests override it when needed."""
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})


@pytest.fixture(autouse=True)
def isolate_price_history(tmp_path, monkeypatch):
    """Aponta o histórico de preços para um banco temporário em todos os testes."""
    monkeypatch.setattr(
        bot, "PRICES", price_history.PriceHistory(str(tmp_path / "prices.db"))
    )

