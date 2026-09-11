import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

import bot_tele_parrot as bot


class FakeChat:
    def __init__(self, chat_type="private", chat_id=100):
        self.type = chat_type
        self.id = chat_id


class FakeMessage:
    def __init__(self, chat=None):
        self.texts = []
        self.markups = []
        self.text = None
        self.chat = chat or FakeChat()

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.texts.append(text)
        self.markups.append(reply_markup)
        return self

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.texts.append(text)
        self.markups.append(reply_markup)
        return self


class FakeUser:
    def __init__(self, user_id=42):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id=42, chat_type="private", text=None):
        self.effective_user = FakeUser(user_id)
        self.effective_chat = FakeChat(chat_type)
        self.message = FakeMessage(self.effective_chat)
        self.message.text = text
        self.callback_query = None


class FakeCallbackQuery:
    def __init__(self, data, user_id=42):
        self.data = data
        self.from_user = FakeUser(user_id)
        self.answers = []
        self.edits = []
        self.markup_edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, reply_markup=None, **kwargs):
        self.markup_edits.append(reply_markup)


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when=None, data=None, name=None):
        self.jobs.append({"callback": callback, "when": when, "data": data, "name": name})


class FakeBot:
    def __init__(self):
        self.username = "testando_tudo_bot"
        self.messages = []
        self.commands = None

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))

    async def set_my_commands(self, commands):
        self.commands = commands


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []
        self.job_queue = FakeJobQueue()
        self.bot = FakeBot()
        self.job = None


class FakeJobContext:
    def __init__(self, data):
        self.job = SimpleNamespace(data=data)
        self.bot = FakeBot()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_rate_hits():
    bot.RATE_HITS.clear()
    yield
    bot.RATE_HITS.clear()


@pytest.fixture
def history(tmp_path, monkeypatch):
    store = bot.History(str(tmp_path / "history.db"))
    monkeypatch.setattr(bot, "HISTORY", store)
    return store


@pytest.fixture
def reminders(tmp_path, monkeypatch):
    store = bot.ReminderStore(str(tmp_path / "reminders.db"))
    monkeypatch.setattr(bot, "REMINDERS", store)
    return store


def fake_stream(chunks, captured=None):
    async def _stream(messages, base_url, timeout=bot.LLM_TIMEOUT):
        if captured is not None:
            captured["messages"] = messages
            captured["base_url"] = base_url
        for chunk in chunks:
            yield chunk

    return _stream


def test_parse_allowed_ids():
    assert bot.parse_allowed_ids("1, 2 ,x,3") == {1, 2, 3}
    assert bot.parse_allowed_ids("") == set()
    assert bot.parse_allowed_ids(None) == set()


def test_format_number():
    assert bot.format_number("5.1") == "5,10"
    assert bot.format_number(1234567.5) == "1.234.567,50"
    assert bot.format_number(24.31, 1) == "24,3"
    assert bot.format_number(None) == "None"


def test_format_duration():
    assert bot.format_duration(30) == "30s"
    assert bot.format_duration(600) == "10m"
    assert bot.format_duration(3600) == "1h"
    assert bot.format_duration(90000) == "1d"


def test_parse_duration():
    assert bot.parse_duration("10m") == 600
    assert bot.parse_duration("2h") == 7200
    assert bot.parse_duration("1d") == 86400
    assert bot.parse_duration("0m") is None
    assert bot.parse_duration("31d") is None
    assert bot.parse_duration("xx") is None
    assert bot.parse_duration(None) is None


def test_check_rate_limit():
    assert bot.check_rate_limit(1, limit=2) is True
    assert bot.check_rate_limit(1, limit=2) is True
    assert bot.check_rate_limit(1, limit=2) is False
    assert bot.check_rate_limit(2, limit=2) is True
    assert bot.check_rate_limit(None) is False


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


def test_format_weather():
    place = {"name": "Sao Paulo", "country": "Brasil"}
    data = {
        "current": {"temperature_2m": 24.3, "apparent_temperature": 25.1, "weather_code": 61}
    }
    text = bot.format_weather(place, data)
    assert "Clima em Sao Paulo, Brasil" in text
    assert "24,3 C" in text
    assert "Chuva leve" in text


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
        return {
            "cep": "01310100",
            "address": "Avenida Paulista",
            "state": "SP",
            "district": "Bela Vista",
            "city": "São Paulo",
            "ddd": "11",
        }

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


def test_coin_reply_has_refresh_button(monkeypatch):
    async def fake_fetch(url, timeout=bot.HTTP_TIMEOUT, transport=None):
        return {"USDBRL": {"name": "Dolar", "bid": "5.1", "pctChange": "0.1"}}

    monkeypatch.setattr(bot, "fetch_json", fake_fetch)
    update, context = FakeUpdate(), FakeContext(["usd"])
    run(bot.coin(update, context))
    assert "R$ 5,10" in update.message.texts[0]
    assert update.message.markups[0] is not None


def test_coin_callback_refreshes(monkeypatch):
    async def fake_fetch(url, timeout=bot.HTTP_TIMEOUT, transport=None):
        return {"USDBRL": {"name": "Dolar", "bid": "5.2", "pctChange": "0.2"}}

    monkeypatch.setattr(bot, "fetch_json", fake_fetch)
    query = FakeCallbackQuery("coin:USD-BRL")
    update = FakeUpdate()
    update.callback_query = query
    run(bot.coin_callback(update, FakeContext()))
    assert "R$ 5,20" in query.edits[0][0]


def test_simple_commands_reply():
    for handler, expected in [
        (bot.start, "Hello World!"),
        (bot.help_command, "O que posso fazer"),
        (bot.end, "Bye Bye!"),
    ]:
        update, context = FakeUpdate(), FakeContext()
        run(handler(update, context))
        assert expected in update.message.texts[0]


def test_commands_lists_ai_commands():
    update, context = FakeUpdate(), FakeContext()
    run(bot.commands(update, context))
    text = update.message.texts[0]
    for command in ("/cep <codigo>", "/ask <question>", "/nota <question>", "/lembrete", "/status"):
        assert command in text


def test_echo_private_replies():
    update = FakeUpdate(text="oi")
    run(bot.echo(update, FakeContext()))
    assert update.message.texts == ["oi"]


def test_echo_group_without_mention_is_ignored():
    update = FakeUpdate(chat_type="group", text="oi")
    run(bot.echo(update, FakeContext()))
    assert update.message.texts == []


def test_echo_group_with_mention_replies():
    update = FakeUpdate(chat_type="group", text="oi @testando_tudo_bot")
    run(bot.echo(update, FakeContext()))
    assert update.message.texts == ["oi @testando_tudo_bot"]


def test_clima_without_city_returns_usage():
    update = FakeUpdate()
    run(bot.clima(update, FakeContext()))
    assert "Usage" in update.message.texts[0]


def test_clima_success(monkeypatch):
    async def fake_fetch(url, timeout=bot.HTTP_TIMEOUT, transport=None):
        if "geocoding" in url:
            return {
                "results": [
                    {
                        "name": "Sao Paulo",
                        "country": "Brasil",
                        "latitude": -23.5,
                        "longitude": -46.6,
                    }
                ]
            }
        return {
            "current": {
                "temperature_2m": 24.3,
                "apparent_temperature": 25.1,
                "weather_code": 0,
            }
        }

    monkeypatch.setattr(bot, "fetch_json", fake_fetch)
    update = FakeUpdate()
    run(bot.clima(update, FakeContext(["Sao", "Paulo"])))
    assert "Clima em Sao Paulo, Brasil" in update.message.texts[0]
    assert "Ceu limpo" in update.message.texts[0]


def test_clima_city_not_found(monkeypatch):
    async def fake_fetch(url, timeout=bot.HTTP_TIMEOUT, transport=None):
        return {"results": []}

    monkeypatch.setattr(bot, "fetch_json", fake_fetch)
    update = FakeUpdate()
    run(bot.clima(update, FakeContext(["xyzabc"])))
    assert "not found" in update.message.texts[0].lower()


def test_service_status(monkeypatch):
    async def fake_fetch(url, timeout=bot.HTTP_TIMEOUT, transport=None):
        return {"models": [{"name": bot.OLLAMA_MODEL}]}

    async def fake_probe(url, timeout=bot.HTTP_TIMEOUT):
        return True

    monkeypatch.setattr(bot, "fetch_json", fake_fetch)
    monkeypatch.setattr(bot, "probe", fake_probe)
    assert run(bot.service_status()) == {"ollama": True, "model": True, "vault": True}


def test_status_reports_services(history, monkeypatch):
    async def fake_status():
        return {"ollama": True, "model": True, "vault": False}

    monkeypatch.setattr(bot, "service_status", fake_status)
    update = FakeUpdate()
    run(bot.status(update, FakeContext()))
    text = update.message.texts[0]
    assert "Ollama: online" in text
    assert "disponivel" in text
    assert "Vault: offline" in text


def test_lembrete_without_argument_returns_usage(reminders):
    update = FakeUpdate()
    run(bot.lembrete(update, FakeContext()))
    assert "Usage" in update.message.texts[0]


def test_lembrete_stores_and_schedules(reminders):
    update = FakeUpdate()
    context = FakeContext(["10m", "tomar", "agua"])
    run(bot.lembrete(update, context))
    pending = reminders.pending()
    assert len(pending) == 1
    assert pending[0]["text"] == "tomar agua"
    assert context.job_queue.jobs[0]["when"] == 600
    assert "10m" in update.message.texts[0]


def test_send_reminder(reminders):
    reminder_id = reminders.add(42, 100, "tomar agua", time.time())
    context = FakeJobContext(reminder_id)
    run(bot.send_reminder(context))
    assert context.bot.messages == [(100, "Lembrete: tomar agua")]
    assert reminders.pop(reminder_id) is None


def test_reschedule_reminders(reminders):
    reminders.add(42, 100, "atrasado", time.time() - 5)
    reminders.add(42, 100, "futuro", time.time() + 60)
    application = SimpleNamespace(job_queue=FakeJobQueue())
    bot.reschedule_reminders(application)
    jobs = application.job_queue.jobs
    assert len(jobs) == 2
    assert jobs[0]["when"] == 1.0
    assert jobs[1]["when"] > 1.0


def test_ask_without_question_returns_usage(history):
    update, context = FakeUpdate(), FakeContext()
    run(bot.ask(update, context))
    assert "Usage" in update.message.texts[0]


def test_ask_streams_and_saves_history(history, monkeypatch):
    captured = {}
    monkeypatch.setattr(bot, "stream_chat", fake_stream(["A resposta ", "e 42."], captured))
    update, context = FakeUpdate(), FakeContext(["Qual", "e", "a", "resposta?"])
    run(bot.ask(update, context))
    assert "A resposta e 42." in update.message.texts[-1]
    assert captured["base_url"] == bot.OLLAMA_URL
    stored = history.recent(42)
    assert [item["role"] for item in stored] == ["user", "assistant"]
    assert stored[0]["content"] == "Qual e a resposta?"


def test_ask_failure_does_not_save_history(history, monkeypatch):
    async def failing_stream(messages, base_url, timeout=bot.LLM_TIMEOUT):
        raise httpx.ConnectError("offline")
        yield ""

    monkeypatch.setattr(bot, "stream_chat", failing_stream)
    update, context = FakeUpdate(), FakeContext(["oi"])
    run(bot.ask(update, context))
    assert "Could not reach" in update.message.texts[-1]
    assert history.recent(42) == []


def test_ask_rate_limit(history, monkeypatch):
    monkeypatch.setattr(bot, "stream_chat", fake_stream(["nao deveria rodar"]))
    bot.RATE_HITS[42] = [time.monotonic()] * bot.RATE_LIMIT_PER_MINUTE
    update, context = FakeUpdate(), FakeContext(["oi"])
    run(bot.ask(update, context))
    assert "Rate limit" in update.message.texts[0]
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
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    monkeypatch.setattr(bot, "stream_chat", fake_stream(["Segundo o vault..."], captured))
    update, context = FakeUpdate(user_id=42), FakeContext(["o", "que", "tenho", "anotado?"])
    run(bot.nota(update, context))
    assert captured["base_url"] == bot.VAULT_PROXY_URL
    assert "vault" in update.message.texts[-1]


def test_resumo_uses_vault(history, monkeypatch):
    captured = {}
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    monkeypatch.setattr(bot, "stream_chat", fake_stream(["resumo"], captured))
    update = FakeUpdate(user_id=42)
    run(bot.resumo(update, FakeContext()))
    assert captured["base_url"] == bot.VAULT_PROXY_URL
    assert captured["messages"][-1]["content"] == bot.RESUMO_QUESTION


def test_compact_history_summarizes_and_removes(history, monkeypatch):
    for index in range(30):
        history.add(42, "user" if index % 2 == 0 else "assistant", f"mensagem {index}")

    async def fake_chat(messages, base_url, timeout=bot.LLM_TIMEOUT):
        return "resumo gerado"

    monkeypatch.setattr(bot, "chat", fake_chat)
    run(bot.compact_history(42, bot.OLLAMA_URL))
    assert history.summary(42) == "resumo gerado"
    assert history.count(42) == 30 - (bot.HISTORY_THRESHOLD - bot.HISTORY_LIMIT)


def test_compact_history_skips_when_short(history, monkeypatch):
    history.add(42, "user", "oi")

    async def fake_chat(messages, base_url, timeout=bot.LLM_TIMEOUT):
        raise AssertionError("should not be called")

    monkeypatch.setattr(bot, "chat", fake_chat)
    run(bot.compact_history(42, bot.OLLAMA_URL))
    assert history.summary(42) is None
    assert history.count(42) == 1


def test_compact_history_keeps_messages_on_failure(history, monkeypatch):
    for index in range(30):
        history.add(42, "user", f"mensagem {index}")

    async def failing_chat(messages, base_url, timeout=bot.LLM_TIMEOUT):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(bot, "chat", failing_chat)
    run(bot.compact_history(42, bot.OLLAMA_URL))
    assert history.summary(42) is None
    assert history.count(42) == 30


def test_reset_clears_history(history):
    history.add(42, "user", "oi")
    history.set_summary(42, "resumo")
    update, context = FakeUpdate(), FakeContext()
    run(bot.reset(update, context))
    assert history.recent(42) == []
    assert history.summary(42) is None
    assert "cleared" in update.message.texts[0]


def test_history_callback_clears(history):
    history.add(42, "user", "oi")
    query = FakeCallbackQuery("history:reset", user_id=42)
    update = FakeUpdate()
    update.callback_query = query
    run(bot.history_callback(update, FakeContext()))
    assert history.recent(42) == []
    assert query.answers == ["Historico limpo."]


def test_build_application_registers_handlers():
    application = bot.build_application("123456:TEST-TOKEN")
    assert application.handlers
    commands = {
        command
        for handler in application.handlers[0]
        if isinstance(handler, bot.CommandHandler)
        for command in handler.commands
    }
    expected = {
        "ask",
        "nota",
        "resumo",
        "reset",
        "coin",
        "cep",
        "clima",
        "lembrete",
        "status",
    }
    assert expected <= commands
    callback_handlers = [
        handler
        for handler in application.handlers[0]
        if isinstance(handler, bot.CallbackQueryHandler)
    ]
    assert callback_handlers
