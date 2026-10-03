import json
import sqlite3
import time

import pytest

import bot_tele_parrot as bot
import telegram_export_import as imp
from telegram_export_import import (
    ExportError,
    ImportStore,
    flatten_text,
    import_export,
    load_allowlist,
    minimized_text,
    parse_export,
    rollback_export,
    safe_photo_path,
)
from test_bot import FakeContext, FakeUpdate, run

DAY = 86400
CHAT = 987654321


def write_export(tmp_path, messages, *, chat_id=CHAT, chat_type="public_channel"):
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps({"id": chat_id, "name": "grupo privado", "type": chat_type, "messages": messages}),
        encoding="utf-8",
    )
    return str(path)


def write_allowlist(tmp_path, chat_ids, *, source=imp.DEFAULT_SOURCE):
    path = tmp_path / "allowlist.json"
    path.write_text(
        json.dumps({"sources": {source: {"chats": [{"id": str(c)} for c in chat_ids]}}}),
        encoding="utf-8",
    )
    return str(path)


def message(mid, ts, text, photo=None):
    item = {
        "id": mid,
        "type": "message",
        "date": "x",
        "date_unixtime": str(ts),
        "from": "Fulano",
        "from_id": "user123",
        "text": text,
    }
    if photo is not None:
        item["photo"] = photo
    return item


def rows(db, query):
    with sqlite3.connect(db) as conn:
        return conn.execute(query).fetchall()


# -- funções puras ---------------------------------------------------------


def test_flatten_text_str_and_entities():
    assert flatten_text("  oi   mundo ") == "oi mundo"
    assert flatten_text(["R$ ", {"type": "bold", "text": "50"}, " por R$ 30"]) == "R$ 50 por R$ 30"
    assert flatten_text("coração 💙") == "coração 💙"


def test_flatten_text_rejects_malicious():
    assert flatten_text(None) is None
    assert flatten_text(123) is None
    assert flatten_text({"type": "bold"}) is None
    assert flatten_text([]) is None
    assert flatten_text([{"type": "bold"}, 42, None]) is None


def test_safe_photo_path():
    assert safe_photo_path("photos/photo_1@01-01-2026.jpg") == "photos/photo_1@01-01-2026.jpg"
    assert safe_photo_path("photos/IMG.JPEG") == "photos/IMG.JPEG"
    assert safe_photo_path("photos/a.png") == "photos/a.png"
    assert safe_photo_path("/etc/passwd") is None
    assert safe_photo_path("photos/../../etc/passwd.jpg") is None
    assert safe_photo_path("../photos/a.jpg") is None
    assert safe_photo_path("files/a.jpg") is None
    assert safe_photo_path("photos/a.exe") is None
    assert safe_photo_path(None) is None
    assert safe_photo_path(123) is None
    assert safe_photo_path("photos/") is None


def test_minimized_text_removes_pii_and_prices():
    assert minimized_text("SSD NVMe 1TB de R$ 400,00 por R$ 200,00 cupom ABC10") == "ssd nvme 1tb"
    assert minimized_text("R$ 50,00") == "produto"
    assert minimized_text("") == "produto"
    assert minimized_text(None) == "produto"
    # CPF/telefone residual -> placeholder (fail-closed)
    assert minimized_text("produto 123.456.789-00") == "produto"
    # URL/@/e-mail não sobram
    cleaned = minimized_text("Fone https://x.com/a @fulano fulano@x.com SSD 1TB")
    assert "http" not in cleaned and "@" not in cleaned and "fulano" not in cleaned


def test_load_allowlist(tmp_path):
    path = write_allowlist(tmp_path, [CHAT, 111])
    assert load_allowlist(path)[imp.DEFAULT_SOURCE] == {str(CHAT), "111"}


def test_load_allowlist_malformed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["nope"]), encoding="utf-8")
    assert load_allowlist(str(path)) == {}


def test_parse_export_rejects_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"messages": []}), encoding="utf-8")
    with pytest.raises(ExportError):
        parse_export(str(bad))
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ExportError):
        parse_export(str(bad))


# -- dry-run e allowlist ---------------------------------------------------


def test_dry_run_totals_and_no_write(tmp_path):
    messages = [
        message(1, 100, "SSD 1TB de R$ 100,00 por R$ 50,00 cupom ABC10"),
        message(2, 200, "bom dia pessoal"),
        message(3, 300, "Fone de R$ 30,00 à vista", photo="photos/a.jpg"),
        {"id": 4, "type": "service", "date_unixtime": "400", "text": "entrou"},
        {"id": "x", "type": "message", "date_unixtime": "nope", "text": "R$ 1,00"},
    ]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")

    report = import_export(export, allowlist_path=allowlist, db_path=db, retention_days=0)

    assert report["applied"] is False
    assert report["chat_type_allowed"] is True
    assert report["allowlisted"] is True
    assert report["total_messages"] == 5
    assert report["importable"] == 3
    assert report["skipped"] == 2
    assert report["deals"] == 2
    assert report["non_deals"] == 1
    assert report["photos_present"] == 1
    assert report["photos_safe"] == 1
    assert report["would_insert"] == 3
    assert report["would_feed_deals"] == 2
    assert not (tmp_path / "imp.db").exists()


def test_dry_run_never_leaks_chat_id(tmp_path):
    export = write_export(tmp_path, [message(1, 100, "SSD 1TB de R$ 1,00 por R$ 0,50")])
    allowlist = write_allowlist(tmp_path, [CHAT])
    report = import_export(export, allowlist_path=allowlist)
    dumped = json.dumps(report, ensure_ascii=False)
    assert str(CHAT) not in dumped
    assert "grupo privado" not in dumped


def test_dry_run_does_not_create_schema(tmp_path):
    export = write_export(tmp_path, [message(1, 100, "SSD 1TB de R$ 1,00 por R$ 0,50")])
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "existing.db")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
    report = import_export(export, allowlist_path=allowlist, db_path=db)
    assert report["applied"] is False
    tables = {row[0] for row in rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "telegram_import_messages" not in tables
    assert "telegram_import_origins" not in tables


def test_rejects_chat_outside_allowlist(tmp_path):
    export = write_export(tmp_path, [message(1, 100, "oi")])
    allowlist = write_allowlist(tmp_path, [111])
    with pytest.raises(ExportError):
        import_export(export, allowlist_path=allowlist)


def test_rejects_disallowed_chat_type(tmp_path):
    export = write_export(tmp_path, [message(1, 100, "oi")], chat_type="private_chat")
    allowlist = write_allowlist(tmp_path, [CHAT])
    with pytest.raises(ExportError):
        import_export(export, allowlist_path=allowlist)


# -- apply: feed, idempotência, minimização, datas, retenção, rollback -----


def test_apply_feeds_deals_and_history(tmp_path):
    messages = [
        message(1, 100, "SSD NVMe 1TB de R$ 400,00 por R$ 200,00 na Amazon"),
        message(2, 200, "bom dia pessoal"),
        message(3, 300, "Fone de R$ 30,00 à vista", photo="photos/a.jpg"),
    ]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")

    report = import_export(
        export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0
    )
    assert report["inserted"] == 3
    assert report["fed"] == 2
    assert report["applied"] is True

    # mapping estável para um id reservado, fora da faixa do Telegram
    origins = rows(db, "SELECT source_chat_id, deals_chat_id FROM telegram_import_origins")
    assert origins == [(str(CHAT), imp.IMPORT_CHAT_ID_BASE)]
    assert origins[0][1] < 0

    # deals alimentado com texto minimizado (sem preço/URL/cupom bruto)
    deals = rows(db, "SELECT chat_id, message_id, text, price FROM deals ORDER BY message_id")
    assert [row[1] for row in deals] == [1, 3]
    assert all(row[0] == imp.IMPORT_CHAT_ID_BASE for row in deals)
    assert deals[0][2].startswith("ssd nvme 1tb")
    assert "R$" not in deals[0][2] and "cupom" not in deals[0][2]

    # histórico com timestamp original e identidade de produto
    obs = rows(db, "SELECT price, created_at FROM price_observations")
    assert obs == [(200.0, 100.0)]


def test_apply_idempotent_second_run(tmp_path):
    messages = [message(1, 100, "SSD NVMe 1TB de R$ 400,00 por R$ 200,00 na Amazon")]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")

    first = import_export(
        export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0
    )
    second = import_export(
        export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0
    )
    assert first["inserted"] == 1 and first["fed"] == 1
    assert second["inserted"] == 0 and second["fed"] == 0
    assert second["already_imported"] == 1
    assert len(rows(db, "SELECT 1 FROM deals")) == 1
    assert len(rows(db, "SELECT 1 FROM price_observations")) == 1
    # o id reservado não muda entre execuções
    assert rows(db, "SELECT deals_chat_id FROM telegram_import_origins") == [
        (imp.IMPORT_CHAT_ID_BASE,)
    ]


def test_checkpoint_has_no_text_for_non_deals(tmp_path):
    messages = [message(1, 100, "SSD NVMe 1TB de R$ 400,00 por R$ 200,00"), message(2, 200, "oi")]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    import_export(export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0)
    got = rows(db, "SELECT message_id, is_deal, text FROM telegram_import_messages ORDER BY message_id")
    assert got[1] == (2, 0, None)


def test_chronological_order_and_dates(tmp_path):
    messages = [message(9, 300, "SSD de R$ 3,00 por R$ 2,00"), message(2, 100, "HD de R$ 1,00 por R$ 0,50")]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    report = import_export(export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0)
    assert report["first_posted_at"] == 100.0
    assert report["last_posted_at"] == 300.0
    ids = [row[0] for row in rows(db, "SELECT message_id FROM telegram_import_messages")]
    assert ids == [2, 9]


def test_unicode_price_parsed(tmp_path):
    export = write_export(tmp_path, [message(1, 100, "SSD: de R$ 1.299,90 por R$ 999,90 🎉")])
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    import_export(export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0)
    row = rows(db, "SELECT price, old_price FROM telegram_import_messages")[0]
    assert row == (999.9, 1299.9)


def test_backfill_limits_to_retention(tmp_path):
    now = 10_000_000
    messages = [
        message(1, now - 10 * DAY, "SSD antigo de R$ 10,00 por R$ 5,00 na Amazon"),
        message(2, now - 1 * DAY, "SSD novo de R$ 20,00 por R$ 10,00 na Amazon"),
    ]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    report = import_export(
        export, allowlist_path=allowlist, db_path=db, apply=True, backup=False,
        retention_days=1, imported_at=now,
    )
    assert report["would_insert"] == 1
    assert report["inserted"] == 1
    assert report["would_feed_deals"] == 1
    assert report["fed"] == 1
    # só a mensagem recente entra em deals e no checkpoint
    assert rows(db, "SELECT message_id FROM deals") == [(2,)]
    assert rows(db, "SELECT message_id FROM telegram_import_messages") == [(2,)]


def test_retention_prunes_aged_checkpoints(tmp_path):
    now = 10_000_000
    messages = [
        message(1, now - 10 * DAY, "SSD antigo de R$ 10,00 por R$ 5,00 na Amazon"),
        message(2, now - 1 * DAY, "SSD novo de R$ 20,00 por R$ 10,00 na Amazon"),
    ]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    import_export(
        export, allowlist_path=allowlist, db_path=db, apply=True, backup=False,
        retention_days=0, imported_at=now,
    )
    assert len(rows(db, "SELECT 1 FROM telegram_import_messages")) == 2
    report = import_export(
        export, allowlist_path=allowlist, db_path=db, apply=True, backup=False,
        retention_days=1, imported_at=now,
    )
    assert report["would_prune"] == 1
    assert report["pruned"] == 1
    assert rows(db, "SELECT message_id FROM telegram_import_messages") == [(2,)]


def test_rollback_keeps_live_deals(tmp_path):
    export = write_export(tmp_path, [message(1, 100, "SSD de R$ 1,00 por R$ 0,50")])
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    import_export(export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0)
    # oferta "ao vivo" com chat_id real do Bot API
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO deals (chat_id, message_id, created_at, text) VALUES (-1001234567890, 7, 1, 'live')"
        )
    result = rollback_export(export, allowlist_path=allowlist, db_path=db)
    assert result["deals"] == 1
    assert result["messages"] == 1
    live = rows(db, "SELECT chat_id, text FROM deals")
    assert live == [(-1001234567890, "live")]


def test_backup_refuses_overwrite(tmp_path):
    db = str(tmp_path / "imp.db")
    store = ImportStore(db)
    store.apply("s", "1", "group", [], imported_at=1)
    dest = str(tmp_path / "bkp.db")
    store.backup(dest)
    with pytest.raises(FileExistsError):
        store.backup(dest)


def test_apply_creates_backup(tmp_path):
    export = write_export(tmp_path, [message(1, 100, "SSD de R$ 1,00 por R$ 0,50")])
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    import_export(export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0)
    report = import_export(export, allowlist_path=allowlist, db_path=db, apply=True, retention_days=0)
    assert report["backup"] and report["backup"].startswith(db + ".bak-")


# -- integração com /ofertas (sem Zen/rede) --------------------------------


def test_ofertas_uses_imported_deals_without_zen(tmp_path, monkeypatch):
    now = int(time.time())
    messages = [message(1, now - 3600, "SSD NVMe 1TB de R$ 400,00 por R$ 200,00 na Amazon")]
    export = write_export(tmp_path, messages)
    allowlist = write_allowlist(tmp_path, [CHAT])
    db = str(tmp_path / "imp.db")
    import_export(export, allowlist_path=allowlist, db_path=db, apply=True, backup=False, retention_days=0)

    monkeypatch.setattr(bot, "DEALS", bot.DealStore(db))
    monkeypatch.setattr(bot, "PRICES", bot.PriceHistory(db))
    monkeypatch.setattr(bot, "DEALS_WINDOW_HOURS", 24)
    monkeypatch.setattr(bot, "DEALS_JEV_RANKING", False)  # garante que não chama Zen

    update = FakeUpdate(chat_type="private")
    run(bot.ofertas(update, FakeContext()))
    assert update.message.texts
    assert "Melhores ofertas" in update.message.texts[0]
    assert "ssd nvme 1tb" in update.message.texts[0]


# -- privacidade / custo ---------------------------------------------------


def test_module_has_no_network_imports():
    import ast

    tree = ast.parse(open(imp.__file__, encoding="utf-8").read())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert modules.isdisjoint({"requests", "httpx", "urllib", "socket", "telegram", "http"})


def test_module_never_notifies_telegram():
    source = open(imp.__file__, encoding="utf-8").read()
    assert "send_message" not in source
    assert "bot_tele_parrot" not in source


def test_safe_photo_path_does_not_open_files():
    import inspect

    assert "open(" not in inspect.getsource(imp.safe_photo_path)


def test_jev_sanitizer_blocks_export_pii():
    from jev import build_state, safe_title

    assert safe_title("CPF 123.456.789-00 SSD 1TB") is None
    state = build_state([{"text": "pix 123.456.789-00 fulano@x.com", "price": 10.0}])
    assert "123.456" not in state and "@" not in state
