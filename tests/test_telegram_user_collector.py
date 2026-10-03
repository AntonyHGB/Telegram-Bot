import ast
import json
from datetime import datetime, timezone

import pytest

import telegram_export_import as imp
import telegram_user_collector as col
from telegram_export_import import ImportStore


class FakeChannel:
    def __init__(self, peer_id, broadcast=True, megagroup=False):
        self.id = peer_id
        self.broadcast = broadcast
        self.megagroup = megagroup


class FakeUser:
    def __init__(self, peer_id):
        self.id = peer_id


class FakeMessage:
    def __init__(self, message_id, ts, text):
        self.id = message_id
        self.date = datetime.fromtimestamp(ts, tz=timezone.utc)
        self.message = text
        self.text = text


class FloodWaitError(Exception):
    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__("flood")


class FakeClient:
    """Client mínimo no shape do Telethon; falha se métodos proibidos forem usados."""

    def __init__(self, entity, messages, *, flood_once=None, flood_seconds=5):
        self.entity = entity
        self.messages = messages
        self._flood_once = flood_once
        self._flood_seconds = flood_seconds
        self.get_entity_calls = []
        self.iter_calls = []
        self.dialogs_called = False
        self.contacts_called = False

    def get_entity(self, username):
        self.get_entity_calls.append(username)
        return self.entity

    def iter_messages(self, entity, limit=None, min_id=0, offset_id=0):
        self.iter_calls.append({"limit": limit, "min_id": min_id, "offset_id": offset_id})
        if self._flood_once and self._flood_once > 0:
            self._flood_once -= 1
            raise FloodWaitError(self._flood_seconds)
        for message in self.messages:
            if message.id > min_id and (offset_id == 0 or message.id < offset_id):
                yield message

    def get_dialogs(self, *args, **kwargs):  # pragma: no cover - não deve ser chamado
        self.dialogs_called = True
        raise AssertionError("get_dialogs não deve ser chamado")

    def get_contacts(self, *args, **kwargs):  # pragma: no cover - não deve ser chamado
        self.contacts_called = True
        raise AssertionError("get_contacts não deve ser chamado")


def write_allowlist(tmp_path, *, username="canal_autorizado", expected=None,
                    reuse_source=imp.DEFAULT_SOURCE, chat_type="public_channel"):
    path = tmp_path / "allowlist.json"
    path.write_text(
        json.dumps(
            {
                "sources": {imp.DEFAULT_SOURCE: {"chats": [{"id": "111", "type": "public_channel"}]}},
                "mtproto": {
                    "username": username,
                    "expected_peer_id": None if expected is None else str(expected),
                    "reuse_source": reuse_source,
                    "chat_type": chat_type,
                    "max_window_days": 90,
                },
            }
        ),
        encoding="utf-8",
    )
    return str(path)


NOW = 10_000_000
PEER = 123456789


def msg(mid, age_days, text):
    return FakeMessage(mid, NOW - age_days * 86400, text)


# -- configuração / credenciais -------------------------------------------


def test_load_credentials_missing_does_not_leak(monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    with pytest.raises(col.CollectorError) as exc:
        col.load_credentials()
    assert "TELEGRAM_API_ID" in str(exc.value)


def test_load_credentials_ok(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abcdef")
    assert col.load_credentials() == (12345, "abcdef")


def test_load_mtproto_config_missing(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"sources": {}}), encoding="utf-8")
    with pytest.raises(col.CollectorError):
        col.load_mtproto_config(str(path))


# -- identidade ------------------------------------------------------------


def test_check_identity_match_and_mismatch():
    config = {"expected_peer_id": str(PEER)}
    match = col.check_identity(FakeChannel(PEER), config)
    assert match["matches_expected"] and match["is_group_like"]
    mismatch = col.check_identity(FakeChannel(PEER + 1), config)
    assert not mismatch["matches_expected"]


def test_check_identity_rejects_user():
    assert not col.check_identity(FakeUser(PEER), {"expected_peer_id": str(PEER)})["is_group_like"]


def test_preflight_returns_booleans_only(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(FakeChannel(PEER), [])
    report = col.preflight(client, allowlist_path=allowlist)
    assert report == {"identity_verified": True, "is_group_like": True}
    assert str(PEER) not in json.dumps(report)


# -- coleta / dry-run ------------------------------------------------------


def test_dry_run_no_write(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    db = str(tmp_path / "imp.db")
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, "SSD de R$ 400,00 por R$ 200,00")])
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=db, dry_run=True, now=NOW
    )
    assert report["dry_run"] is True and report["applied"] is False
    assert report["fetched"] == 1 and report["new"] == 1 and report["deals"] == 1
    assert report["reused_export_source"] is True
    assert not (tmp_path / "imp.db").exists()
    assert str(PEER) not in json.dumps(report)


def test_apply_reuses_export_source_when_identity_matches(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    db = str(tmp_path / "imp.db")
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, "SSD de R$ 400,00 por R$ 200,00")])
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=db, dry_run=False, now=NOW
    )
    assert report["applied"] is True and report["reused_export_source"] is True
    assert report["inserted"] == 1 and report["fed"] == 1
    store = ImportStore(db)
    assert store.existing_ids(imp.DEFAULT_SOURCE, str(PEER)) == {10}


def test_apply_uses_mtproto_source_when_identity_differs(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER + 999)
    db = str(tmp_path / "imp.db")
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, "SSD de R$ 400,00 por R$ 200,00")])
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=db, dry_run=False, now=NOW
    )
    assert report["reused_export_source"] is False
    store = ImportStore(db)
    assert store.existing_ids(col.MT_PROTO_SOURCE, str(PEER)) == {10}
    assert store.existing_ids(imp.DEFAULT_SOURCE, str(PEER)) == set()


def test_window_filters_old_messages(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(
        FakeChannel(PEER),
        [msg(3, 1, "SSD de R$ 3,00 por R$ 2,00"), msg(1, 200, "SSD de R$ 1,00 por R$ 0,50")],
    )
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
        dry_run=True, window_days=90, now=NOW,
    )
    assert report["fetched"] == 1  # a antiga (200d) fica fora da janela


def test_incremental_cursor_from_checkpoint(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    db = str(tmp_path / "imp.db")
    store = ImportStore(db)
    store.apply(
        imp.DEFAULT_SOURCE, str(PEER), "public_channel",
        [imp.build_record(10, NOW - 86400, "SSD de R$ 400,00 por R$ 200,00")],
        retention_days=0, imported_at=NOW,
    )
    client = FakeClient(
        FakeChannel(PEER),
        [msg(12, 1, "SSD de R$ 4,00 por R$ 2,00"), msg(10, 2, "SSD de R$ 4,00 por R$ 2,00")],
    )
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=db, dry_run=True, now=NOW
    )
    assert client.iter_calls[0]["min_id"] == 10
    assert report["cursor_from_checkpoint"] is True
    assert report["new"] == 1  # só a 12 é nova


def test_window_above_90_rejected(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(FakeChannel(PEER), [])
    with pytest.raises(col.CollectorError):
        col.collect_once(
            client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
            dry_run=True, window_days=91, now=NOW,
        )


# -- FloodWait / limites ---------------------------------------------------


def test_floodwait_backoff_then_success(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    sleeps = []
    client = FakeClient(
        FakeChannel(PEER), [msg(10, 1, "SSD de R$ 400,00 por R$ 200,00")],
        flood_once=1, flood_seconds=7,
    )
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
        dry_run=True, now=NOW, sleep=sleeps.append,
    )
    assert sleeps == [7.0]
    assert report["fetched"] == 1


def test_floodwait_too_long_aborts(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(FakeChannel(PEER), [], flood_once=1, flood_seconds=10_000)
    with pytest.raises(FloodWaitError):
        col.collect_once(
            client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
            dry_run=True, now=NOW, sleep=lambda _s: None,
        )


def test_never_iterates_dialogs_or_contacts(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, "SSD de R$ 1,00 por R$ 0,50")])
    col.collect_once(client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
                     dry_run=True, now=NOW)
    assert client.dialogs_called is False
    assert client.contacts_called is False


# -- mídia / privacidade ---------------------------------------------------


def test_no_media_download_and_photo_path_none(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, "SSD de R$ 400,00 por R$ 200,00")])
    report = col.collect_once(client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
                              dry_run=False, now=NOW)
    assert report["applied"] is True
    import sqlite3

    with sqlite3.connect(str(tmp_path / "imp.db")) as conn:
        photo = conn.execute("SELECT photo_path FROM telegram_import_messages").fetchone()[0]
    assert photo is None
    assert "download" not in open(col.__file__, encoding="utf-8").read()


def test_no_raw_text_in_logs(tmp_path, caplog):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    secret = "MENSAGEM SECRETA 123.456.789-00"
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, f"SSD {secret} de R$ 1,00 por R$ 0,50")])
    with caplog.at_level("INFO"):
        col.collect_once(client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
                         dry_run=True, now=NOW)
    assert secret not in caplog.text
    assert "SSD" not in caplog.text


def test_message_to_record_minimizes_pii():
    record = col.message_to_record(FakeMessage(1, NOW, "CPF 123.456.789-00 SSD 1TB de R$ 9,00 por R$ 5,00"))
    assert "123.456" not in (record["text"] or "")
    assert "@" not in (record["text"] or "")


def test_module_lazy_telethon_import():
    tree = ast.parse(open(col.__file__, encoding="utf-8").read())
    modules = set()
    for node in tree.body:  # só imports de topo; telethon é importado dentro de função
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert "telethon" not in modules
    assert modules.isdisjoint({"requests", "httpx", "urllib", "socket", "telegram", "http"})


# -- correções de revisão (P0/P1) ------------------------------------------


def test_peer_id_forms_accept_100_prefix():
    # export guarda o id puro; Bot API usa -100…
    config = {"expected_peer_id": str(-1_000_000_000_000 - PEER)}
    assert col.check_identity(FakeChannel(PEER), config)["matches_expected"] is True
    # e o inverso: expected puro, peer -100…
    config2 = {"expected_peer_id": str(PEER)}
    assert col.check_identity(FakeChannel(-1_000_000_000_000 - PEER), config2)[
        "matches_expected"
    ] is True


def test_window_zero_rejected(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(FakeChannel(PEER), [])
    with pytest.raises(col.CollectorError):
        col.collect_once(
            client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
            dry_run=True, window_days=0, now=NOW,
        )


def test_limit_hit_aborts_instead_of_gap(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(
        FakeChannel(PEER),
        [msg(3, 1, "SSD de R$ 3,00 por R$ 2,00"), msg(2, 1, "SSD de R$ 2,00 por R$ 1,00")],
    )
    with pytest.raises(col.CollectorError):
        col.collect_once(
            client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
            dry_run=True, limit=1, now=NOW,
        )


def test_pagination_covers_window(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    messages = [msg(i, 1, f"SSD {i} de R$ {i},00 por R$ 1,00") for i in range(1, 6)]
    client = FakeClient(FakeChannel(PEER), list(reversed(messages)))
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
        dry_run=True, now=NOW,
    )
    assert report["fetched"] == 5
    assert len(client.iter_calls) >= 1


def test_malformed_message_is_skipped(tmp_path, monkeypatch):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, "SSD de R$ 1,00 por R$ 0,50")])

    def boom(_message, media_path=None):
        raise ValueError("malformada")

    monkeypatch.setattr(col, "message_to_record", boom)
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=str(tmp_path / "imp.db"),
        dry_run=True, now=NOW,
    )
    assert report["fetched"] == 0 and report["skipped"] == 1


def test_dry_run_does_not_create_schema_on_existing_db(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    db = str(tmp_path / "existing.db")
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
    client = FakeClient(FakeChannel(PEER), [msg(10, 1, "SSD de R$ 1,00 por R$ 0,50")])
    report = col.collect_once(
        client, allowlist_path=allowlist, db_path=db, dry_run=True, now=NOW
    )
    assert report["dry_run"] is True
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "telegram_import_messages" not in tables


def test_secure_session_sets_600(tmp_path):
    session = tmp_path / "collector.session"
    session.write_text("x", encoding="utf-8")
    session.chmod(0o644)
    col.secure_session(str(session))
    assert (session.stat().st_mode & 0o777) == 0o600


def test_credentials_never_logged(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "segredo-hash-abc")
    with caplog.at_level("DEBUG"):
        col.load_credentials()
    assert "segredo-hash-abc" not in caplog.text
    assert "12345" not in caplog.text


def test_cli_dry_run_validates_window(tmp_path):
    allowlist = write_allowlist(tmp_path, expected=PEER)
    assert col.main(["--allowlist", allowlist, "--window-days", "0"]) == 1
    assert col.main(["--allowlist", allowlist, "--window-days", "120"]) == 1
    assert col.main(["--allowlist", allowlist, "--window-days", "90"]) == 0


def test_collector_chain_does_not_require_httpx():
    # telegram_export_import não deve mais depender de jev/httpx
    tree = ast.parse(open(imp.__file__, encoding="utf-8").read())
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    assert "jev" not in top and "httpx" not in top
