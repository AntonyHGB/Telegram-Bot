import asyncio

import bot_tele_parrot as bot


class FakeMessage:
    def __init__(self):
        self.texts = []

    async def reply_text(self, text):
        self.texts.append(text)


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []


def run(coro):
    return asyncio.run(coro)


def test_format_coin():
    data = {"BTCBRL": {"name": "Bitcoin/Real Brasileiro", "high": "350000"}}
    assert "350000" in bot.format_coin(data)


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


def test_commands_lists_cep_with_argument():
    update, context = FakeUpdate(), FakeContext()
    run(bot.commands(update, context))
    assert "/cep <codigo>" in update.message.texts[0]


def test_build_application_registers_handlers():
    application = bot.build_application("123456:TEST-TOKEN")
    handler_types = [type(h) for h in application.handlers[0]]
    assert application.handlers
    assert handler_types
