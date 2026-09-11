import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import bot_tele_parrot as bot
import extra_commands as extra
from integrations import NotionConfigurationError, create_task
from test_bot import FakeContext, FakeUpdate, run
from test_bot import history as history


def handler(name):
    app = bot.build_application("123456:TEST-TOKEN")
    return next(h.callback for h in app.handlers[0] if name in getattr(h, "commands", []))


def test_notion_uses_schema_title_and_explicit_destination(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "test-secret")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "test-source")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-secret"
        if request.method == "GET":
            return httpx.Response(200, json={"properties": {"Tarefa": {"type": "title"}}})
        body = json.loads(request.content)
        assert body["parent"]["data_source_id"] == "test-source"
        assert body["properties"]["Tarefa"]["title"][0]["text"]["content"] == "Estudar"
        return httpx.Response(200, json={"url": "https://www.notion.so/test-page"})

    assert run(create_task("Estudar", transport=httpx.MockTransport(respond))).endswith("test-page")
    assert len(requests) == 2


def test_notion_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(NotionConfigurationError):
        run(create_task("Estudar"))


def test_notion_does_not_retry_uncertain_creation(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "test-secret")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "test-source")
    posts = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"properties": {"Name": {"type": "title"}}})
        posts.append(request)
        raise httpx.ReadTimeout("response lost")

    with pytest.raises(httpx.ReadTimeout):
        run(create_task("Estudar", transport=httpx.MockTransport(respond)))
    assert len(posts) == 1


def test_task_rejects_group_even_for_owner(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    update = FakeUpdate(chat_type="group")
    run(handler("tarefa")(update, FakeContext(["Estudar"])))
    assert "privada" in update.message.texts[-1]


def test_task_creates_page_for_authorized_user(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    create = AsyncMock(return_value="https://www.notion.so/test")
    monkeypatch.setattr(extra, "create_task", create)
    update = FakeUpdate()
    run(handler("tarefa")(update, FakeContext(["Estudar", "Python"])))
    create.assert_awaited_once_with("Estudar Python")
    assert "Tarefa criada" in update.message.texts[-1]


def voice_handler(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    app = bot.build_application("123456:TEST-TOKEN")
    return next(h.callback for h in app.handlers[0] if h.callback.__name__ == "voice")


def test_voice_transcribes_and_removes_temporary_file(monkeypatch):
    paths = []

    async def download(custom_path):
        custom_path.write_bytes(b"fixture")
        paths.append(custom_path)

    def transcribe(path):
        assert paths[0].exists()
        return "Teste de transcrição"

    update = FakeUpdate()
    update.message.voice = SimpleNamespace(
        duration=5, file_size=100,
        get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=download)),
    )
    monkeypatch.setattr(extra.speech, "transcribe", transcribe)
    run(voice_handler(monkeypatch)(update, FakeContext()))
    assert update.message.texts[-1] == "Teste de transcrição"
    assert not paths[0].exists()


def test_voice_rejects_oversized_before_download(monkeypatch):
    update = FakeUpdate()
    update.message.voice = SimpleNamespace(duration=121, file_size=100, get_file=AsyncMock())
    run(voice_handler(monkeypatch)(update, FakeContext()))
    update.message.voice.get_file.assert_not_called()
    assert "2 minutos" in update.message.texts[-1]


def test_guide_and_user_id():
    update = FakeUpdate()
    run(extra.guia(update, FakeContext()))
    assert all(command in update.message.texts[-1] for command in ["/tarefa", "/ask", "/guia"])
    run(extra.meuid(update, FakeContext()))
    assert "42" in update.message.texts[-1]


def test_private_history_is_not_used_in_groups(history):
    history.add(42, "assistant", "conteudo privado")
    update = FakeUpdate(chat_type="group")
    run(bot.ask(update, FakeContext(["oi"])))
    assert "privada" in update.message.texts[-1]
