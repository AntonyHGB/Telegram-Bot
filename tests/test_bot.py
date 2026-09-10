import asyncio

import httpx
import pytest

import bot_tele_parrot as bot


class FakeMessage:
    def __init__(self):
        self.texts = []

    async def reply_text(self, text):
        self.texts.append(text)


class FakeUser:
    def __init__(self, user_id=42):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id=42):
        self.message = FakeMessage()
        self.effective_user = FakeUser(user_id)


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def history(tmp_path, monkeypatch):
    store = bot.History(str(tmp_path / "history.db"))
    monkeypatch.setattr(bot, "HISTORY", store)
    return store


def test_parse_allowed_ids():
    assert bot.parse_allowed_ids("1, 2 ,x,3") == {1, 2, 3}
    assert bot.parse_allowed_ids("") == set()
    assert bot.parse_allowed_ids(None) == set()


def test_format_number():
    assert bot.format_number("5.1") == "5,10"
    assert bot.format_number(1234567.5) == "1.234.567,50"
    assert bot.format_number(None) == "None"


def test_format_coin():
    data = {
        "USDBRL": {
            "name": "Dólar Americano/Real Brasileiro",
            "bid": "5.1",
            "pctChange": "-0.094034",
        }
    }
    text = bot.format_coin(data, ["USD-BRL"])
    assert "Dólar Americano/Real Brasileiro" in text
    assert "R$ 5,10" in text
    assert "(-0,09%)" in text


def test_format_coin_skips_missing_pairs():
    assert bot.format_coin({}, ["USD-BRL"]) == ""


def test_parse_pairs_defaults():
    assert bot.parse_pairs([]) == ["USD-BRL", "EUR-BRL", "BTC-BRL"]


def test_parse_pairs_accepts_variants():
    assert bot.parse_pairs(["usd"]) == ["USD-BRL"]
    assert bot.parse_pairs(["USDBRL"]) == ["USD-BRL"]
    assert bot.parse_pairs(["usd/brl", "btc"]) == ["USD-BRL", "BTC-BRL"]


def test_parse_pairs_rejects_invalid():
    assert bot.parse_pairs(["nonsense"]) == []


def test_format_cep():
    data = {
        "cep": "01310100",
        "address": "Avenida Paulista",
        "state": "SP",
        "district": "Bela Vista",
        "city": "São Paulo",
        "ddd": "11",
    }
    text = bot.format_cep(data)
    assert "01310100" in text
    assert "Avenida Paulista" in text


def test_format_insult():
    assert "foo" in bot.format_insult({"insult": "foo"})


def test_normalize_cep():
    assert bot.normalize_cep("01310-100") == "01310100"
    assert bot.normalize_cep("123") is None
    assert bot.normalize_cep(None) is None


def test_truncate():
    assert bot.truncate("abc", limit=10) == "abc"
    assert bot.truncate("abcdef", limit=5) == "ab..."


def test_fetch_json_with_mock_transport():
    def handler(request):
        assert request.url.path == "/json"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    data = run(bot.fetch_json("https://example.test/json", transport=transport))
    assert data == {"ok": True}


def test_fetch_json_raises_on_http_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        run(bot.fetch_json("https://example.test/fail", transport=transport))


def test_cep_not_found_returns_specific_message(monkeypatch):
    async def fake_fetch(url, timeout=bot.HTTP_TIMEOUT, transport=None):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "not found", request=request, response=httpx.Response(404, request=request)
        )

    monkeypatch.setattr(bot, "fetch_json", fake_fetch)
    update, context = FakeUpdate(), FakeContext(["00000000"])
    run(bot.cep(update, context))
    assert "not found" in update.message.texts[0].lower()


def test_cep_success(monkeypatch):
    async def fake_fetch(url, timeout=bot.HTTP_TIMEOUT, transport=None):
        return {"cep": "01310100", "address": "Avenida Paulista", "state": "SP",
                "district": "Bela Vista", "city": "São Paulo", "ddd": "11"}

    monkeypatch.setattr(bot, "fetch_json", fake_fetch)
    update, context = FakeUpdate(), FakeContext(["01310-100"])
    run(bot.cep(update, context))
    assert "Avenida Paulista" in update.message.texts[0]


def test_cep_without_argument_returns_usage():
    update, context = FakeUpdate(), FakeContext()
    run(bot.cep(update, context))
    assert "Usage" in update.message.texts[0]


def test_cep_with_invalid_argument_returns_usage():
    update, context = FakeUpdate(), FakeContext(["123"])
    run(bot.cep(update, context))
    assert "Usage" in update.message.texts[0]


def test_simple_commands_reply():
    for handler, expected in [
        (bot.start, "Hello World!"),
        (bot.help_command, "Nobody will help"),
        (bot.end, "Bye Bye!"),
    ]:
        update, context = FakeUpdate(), FakeContext()
        run(handler(update, context))
        assert expected in update.message.texts[0]


def test_commands_lists_ai_commands():
    update, context = FakeUpdate(), FakeContext()
    run(bot.commands(update, context))
    assert "/cep <codigo>" in update.message.texts[0]
    assert "/ask <question>" in update.message.texts[0]
    assert "/nota <question>" in update.message.texts[0]


def test_ask_without_question_returns_usage(history):
    update, context = FakeUpdate(), FakeContext()
    run(bot.ask(update, context))
    assert "Usage" in update.message.texts[0]


def test_ask_saves_history(history, monkeypatch):
    captured = {}

    async def fake_chat(messages, base_url, timeout=bot.LLM_TIMEOUT):
        captured["messages"] = messages
        captured["base_url"] = base_url
        return "A resposta é 42."

    monkeypatch.setattr(bot, "chat", fake_chat)
    update, context = FakeUpdate(), FakeContext(["Qual", "é", "a", "resposta?"])
    run(bot.ask(update, context))
    assert "42" in update.message.texts[0]
    assert captured["base_url"] == bot.OLLAMA_URL
    stored = history.recent(42)
    assert [item["role"] for item in stored] == ["user", "assistant"]
    assert stored[0]["content"] == "Qual é a resposta?"


def test_ask_failure_does_not_save_history(history, monkeypatch):
    async def fake_chat(messages, base_url, timeout=bot.LLM_TIMEOUT):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(bot, "chat", fake_chat)
    update, context = FakeUpdate(), FakeContext(["oi"])
    run(bot.ask(update, context))
    assert "Could not reach" in update.message.texts[0]
    assert history.recent(42) == []


def test_nota_disabled_without_allowed_ids(history, monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", set())
    update, context = FakeUpdate(), FakeContext(["pergunta"])
    run(bot.nota(update, context))
    assert "not configured" in update.message.texts[0]


def test_nota_rejects_other_user(history, monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {1})
    update, context = FakeUpdate(user_id=42), FakeContext(["pergunta"])
    run(bot.nota(update, context))
    assert "not allowed" in update.message.texts[0]


def test_nota_uses_vault_proxy(history, monkeypatch):
    captured = {}

    async def fake_chat(messages, base_url, timeout=bot.LLM_TIMEOUT):
        captured["base_url"] = base_url
        return "Segundo o vault..."

    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    monkeypatch.setattr(bot, "chat", fake_chat)
    update, context = FakeUpdate(user_id=42), FakeContext(["o", "que", "tenho", "anotado?"])
    run(bot.nota(update, context))
    assert captured["base_url"] == bot.VAULT_PROXY_URL
    assert "vault" in update.message.texts[0]


def test_reset_clears_history(history):
    history.add(42, "user", "oi")
    update, context = FakeUpdate(), FakeContext()
    run(bot.reset(update, context))
    assert history.recent(42) == []
    assert "cleared" in update.message.texts[0]


def test_build_application_registers_handlers():
    application = bot.build_application("123456:TEST-TOKEN")
    assert application.handlers
    commands = {
        command
        for handler in application.handlers[0]
        if isinstance(handler, bot.CommandHandler)
        for command in handler.commands
    }
    assert {"ask", "nota", "reset", "coin", "cep"} <= commands
