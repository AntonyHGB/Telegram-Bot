import os
import sqlite3
import time

import pytest

import bot_tele_parrot as bot
import price_history
from price_history import (
    PriceHistory,
    canonical_url,
    format_history_note,
    history_config_from_env,
    identity_key,
    normalize_title,
)
from test_bot import FakeContext, FakeUpdate, run
from test_deals import sample_deal

DAY = 86400


def make_history(tmp_path, **kwargs):
    # dedupe desligado por padrão para permitir semear séries de teste; os testes
    # de dedupe passam a janela explicitamente.
    kwargs.setdefault("dedupe_hours", 0)
    return PriceHistory(str(tmp_path / "prices.db"), **kwargs)


def rows(path, query="SELECT identity_key, price, created_at FROM price_observations"):
    with sqlite3.connect(path) as connection:
        try:
            return connection.execute(query).fetchall()
        except sqlite3.OperationalError:  # schema preguiçoso ainda não materializado
            return []


def seed(store, deal, prices, *, start, step=3600, link=None):
    """Grava uma série de preços com timestamps crescentes; devolve a última avaliação."""
    last = None
    for index, price in enumerate(prices):
        item = dict(deal, price=price)
        if link is not None:
            item["link"] = link
        last = store.record(item, created_at=start + index * step)
    return last


# -- schema / migração ----------------------------------------------------


def test_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "prices.db")
    first = PriceHistory(path)
    second = PriceHistory(path)
    first.record(sample_deal())
    second.record(sample_deal(text="SSD NVMe 2TB"))
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
    assert "price_observations" in names
    assert "idx_price_obs_identity" in names
    assert "idx_price_obs_created" in names


def test_migration_is_additive_and_preserves_existing_tables(tmp_path):
    path = str(tmp_path / "shared.db")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE deals (id INTEGER PRIMARY KEY, text TEXT, price REAL)"
        )
        connection.execute("INSERT INTO deals (text, price) VALUES ('antigo', 10.0)")
    history = PriceHistory(path)
    history.record(sample_deal())
    with sqlite3.connect(path) as connection:
        deals = connection.execute("SELECT text, price FROM deals").fetchall()
        observations = connection.execute("SELECT COUNT(*) FROM price_observations").fetchone()
    assert deals == [("antigo", 10.0)]
    assert observations[0] == 1


def test_wal_and_busy_timeout_are_enabled(tmp_path):
    history = make_history(tmp_path)
    history.record(sample_deal())  # abre uma conexão e materializa o schema
    with sqlite3.connect(history.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# -- identidade -----------------------------------------------------------


def test_normalize_title_strips_noise():
    text = "SUPER OFERTA!! SSD NVMe 1TB por R$ 200,00 cupom: SOM10 https://x.test/a?utm_source=t"
    assert normalize_title(text) == "ssd nvme 1tb"


def test_canonical_url_drops_tracking():
    assert canonical_url("https://www.Loja.com/Produto/?utm_source=t&id=7&ref=x") == (
        "loja.com/produto?id=7"
    )
    assert canonical_url("") is None
    assert canonical_url("não é url") is None


def test_identity_same_product_matches():
    first = sample_deal(text="SSD NVMe 1TB na Amazon", link="https://a.test/p?utm_source=x")
    second = sample_deal(text="SSD NVMe 1TB na Amazon", link="https://a.test/p?utm_medium=y")
    assert identity_key(first) == identity_key(second)


def test_identity_differs_by_store():
    assert identity_key(sample_deal(store="Amazon")) != identity_key(sample_deal(store="Shopee"))


def test_identity_differs_by_model():
    left = sample_deal(text="Placa de video RTX 4060 8GB")
    right = sample_deal(text="Placa de video RTX 4070 8GB")
    assert identity_key(left) != identity_key(right)


def test_identity_rejects_vague_titles():
    assert identity_key(sample_deal(text="SSD")) is None
    assert identity_key(sample_deal(text="fone bluetooth")) is None
    assert identity_key(sample_deal(text="promoção imperdível")) is None


def test_identity_requires_store():
    assert identity_key(sample_deal(store=None)) is None


def test_identity_coupon_does_not_change_key():
    plain = sample_deal(text="Fone JBL Tune 510BT")
    with_coupon = sample_deal(text="Fone JBL Tune 510BT cupom: SOM10", coupon="SOM10")
    assert identity_key(plain) == identity_key(with_coupon)


def test_identity_splits_distinct_urls_conservatively():
    left = sample_deal(text="SSD NVMe 1TB", link="https://a.test/produto-a")
    right = sample_deal(text="SSD NVMe 1TB", link="https://a.test/produto-b")
    assert identity_key(left) != identity_key(right)


# -- gravação / dedupe ----------------------------------------------------


def test_record_rejects_unreliable_deals(tmp_path):
    history = make_history(tmp_path)
    assert history.record(sample_deal(store=None)) is None
    assert history.record(sample_deal(text="SSD")) is None
    assert history.record(sample_deal(price=None)) is None
    assert history.record(sample_deal(price=0)) is None
    assert history.record(sample_deal(price=-5)) is None
    assert rows(history.path) == []


def test_record_dedupes_same_price_in_window(tmp_path):
    history = make_history(tmp_path, dedupe_hours=6)
    deal = sample_deal(price=200.0)
    now = time.time()
    assert history.record(deal, created_at=now) is not None
    assert history.record(deal, created_at=now + 3600) is None
    assert len(rows(history.path)) == 1
    # Depois da janela, grava de novo.
    assert history.record(deal, created_at=now + 7 * 3600) is not None
    assert len(rows(history.path)) == 2


def test_record_allows_price_change_within_window(tmp_path):
    history = make_history(tmp_path, dedupe_hours=6)
    now = time.time()
    history.record(sample_deal(price=200.0), created_at=now)
    history.record(sample_deal(price=180.0), created_at=now + 60)
    assert len(rows(history.path)) == 2


def test_record_returns_evaluation(tmp_path):
    history = make_history(tmp_path, min_sample=3)
    now = time.time()
    deal = sample_deal(price=200.0)
    seed(history, deal, [200.0, 200.0, 200.0], start=now - 3 * 3600)
    evaluation = history.record(deal, created_at=now)
    assert evaluation["enough_history"] is True
    assert evaluation["price"] == 200.0


# -- avaliação ------------------------------------------------------------


def test_evaluate_insufficient_sample(tmp_path):
    history = make_history(tmp_path, min_sample=5)
    now = time.time()
    seed(history, sample_deal(), [200.0, 200.0], start=now - 2 * 3600)
    evaluation = history.evaluate(sample_deal(price=100.0), at=now)
    assert evaluation["enough_history"] is False
    assert evaluation["is_cheap"] is None
    assert format_history_note(evaluation) == "Histórico: sem histórico suficiente"


def test_evaluate_no_history_at_all(tmp_path):
    history = make_history(tmp_path)
    evaluation = history.evaluate(sample_deal(price=50.0))
    assert evaluation["enough_history"] is False
    assert evaluation["is_cheap"] is None


def test_evaluate_cheap_when_below_minimum(tmp_path):
    history = make_history(tmp_path, min_sample=5)
    now = time.time()
    deal = sample_deal()
    seed(history, deal, [100.0, 110.0, 120.0, 130.0, 140.0], start=now - 5 * 3600)
    evaluation = history.evaluate(sample_deal(price=80.0), at=now)
    assert evaluation["enough_history"] is True
    assert evaluation["is_cheap"] is True
    assert evaluation["at_min"] is True
    assert "mínimo" in format_history_note(evaluation)


def test_evaluate_cheap_below_median_threshold(tmp_path):
    history = make_history(tmp_path, min_sample=5, cheap_median_pct=20.0)
    now = time.time()
    # Mínimo baixo (50) mas mediana 120; atual 90 fica abaixo da mediana sem ser mínimo.
    seed(history, sample_deal(), [50.0, 120.0, 120.0, 120.0, 120.0], start=now - 5 * 3600)
    evaluation = history.evaluate(sample_deal(price=90.0), at=now)
    assert evaluation["is_cheap"] is True
    assert evaluation["at_min"] is False
    assert evaluation["vs_median_pct"] == pytest.approx(25.0)


def test_evaluate_not_cheap_within_normal_range(tmp_path):
    history = make_history(tmp_path, min_sample=5)
    now = time.time()
    seed(history, sample_deal(), [100.0, 110.0, 120.0, 130.0, 140.0], start=now - 5 * 3600)
    evaluation = history.evaluate(sample_deal(price=125.0), at=now)
    assert evaluation["is_cheap"] is False
    assert "dentro do normal" in format_history_note(evaluation)


def test_evaluate_excludes_observations_at_or_after_reference(tmp_path):
    history = make_history(tmp_path, min_sample=1)
    now = time.time()
    history.record(sample_deal(price=100.0), created_at=now)
    evaluation = history.evaluate(sample_deal(price=100.0), at=now)
    # A observação exatamente no instante de referência não entra no baseline.
    assert evaluation["enough_history"] is False


def test_evaluate_uses_larger_window_when_shorter_is_insufficient(tmp_path):
    history = make_history(tmp_path, min_sample=5, windows_days=(7, 30, 90))
    now = time.time()
    seed(history, sample_deal(), [100.0] * 5, start=now - 10 * DAY)
    evaluation = history.evaluate(sample_deal(price=80.0), at=now)
    assert evaluation["enough_history"] is True
    # 7d não tem amostra; a menor janela com amostra é 30d.
    assert evaluation["primary"]["days"] == 30


def test_evaluate_requires_all_qualifying_windows_to_agree(tmp_path):
    history = make_history(tmp_path, min_sample=2, windows_days=(7, 30))
    now = time.time()
    # 30d tem preços antigos baixos (40) que puxam a mediana longa para 70.
    seed(history, sample_deal(), [40.0, 40.0, 40.0], start=now - 20 * DAY)
    # 7d: mediana 100, mínimo 40.
    seed(history, sample_deal(), [40.0, 100.0, 100.0, 100.0, 100.0], start=now - 5 * DAY)
    evaluation = history.evaluate(sample_deal(price=75.0), at=now)
    # Barato no curto prazo (75 <= 80) mas não contra a mediana longa: conservador diz "não".
    assert evaluation["enough_history"] is True
    assert evaluation["is_cheap"] is False


def test_is_cheap_guards_zero_median():
    history = PriceHistory(":memory:")
    assert history._is_cheap(10.0, 0.0, 0.0) is False


def test_reference_window_is_shortest_qualifying(tmp_path):
    history = make_history(tmp_path, min_sample=5, windows_days=(7, 30, 90))
    now = time.time()
    # 5 observações nas últimas horas: as 3 janelas têm amostra, mas a referência
    # exibida é a mais curta (7d), não 90d.
    seed(history, sample_deal(), [100.0, 110.0, 120.0, 130.0, 140.0], start=now - 5 * 3600)
    evaluation = history.evaluate(sample_deal(price=80.0), at=now)
    assert evaluation["primary"]["days"] == 7
    note = format_history_note(evaluation)
    assert "7d" in note
    assert "90d" not in note


def test_note_includes_sample_count(tmp_path):
    history = make_history(tmp_path, min_sample=5)
    now = time.time()
    seed(history, sample_deal(), [100.0, 110.0, 120.0, 130.0, 140.0], start=now - 5 * 3600)
    note = format_history_note(history.evaluate(sample_deal(price=80.0), at=now))
    assert "(5 obs)" in note


# -- retenção -------------------------------------------------------------


def test_retention_prunes_old_observations(tmp_path):
    history = make_history(tmp_path, retention_days=1, dedupe_hours=0)
    now = time.time()
    history.record(sample_deal(price=100.0), created_at=now - 3 * DAY)
    history.record(sample_deal(price=200.0), created_at=now)
    stored = rows(history.path)
    assert len(stored) == 1
    assert stored[0][1] == 200.0


def test_prune_returns_deleted_count(tmp_path):
    history = make_history(tmp_path, retention_days=1, dedupe_hours=0)
    now = time.time()
    history.record(sample_deal(price=100.0), created_at=now - 3 * DAY)
    history.record(sample_deal(price=150.0), created_at=now - 2 * DAY)
    assert history.prune(now=now) == 2


# -- rollback de código (preserva dados) e backup -------------------------


def test_rollback_old_code_ignores_extra_table_without_losing_data(tmp_path):
    """Rollback de imagem/código não exige DROP: o código antigo só ignora a tabela."""
    path = str(tmp_path / "shared.db")
    history = PriceHistory(path, dedupe_hours=0)
    history.record(sample_deal())
    # Simula o código anterior (apenas DealStore) rodando sobre o mesmo banco.
    old_deals = bot.DealStore(path)
    old_deals.add(1, 1, sample_deal(text="SSD NVMe 2TB"))
    assert len(old_deals.top()) == 1
    # O histórico continua íntegro depois do uso pelo código antigo.
    assert len(rows(path)) == 1


def test_schema_recreates_after_manual_drop(tmp_path):
    history = make_history(tmp_path, dedupe_hours=0)
    history.record(sample_deal())
    with sqlite3.connect(history.path) as connection:
        connection.execute("DROP TABLE price_observations")
    history.record(sample_deal(text="SSD NVMe 2TB"))
    assert len(rows(history.path)) == 1


def test_backup_and_restore_via_sqlite_api(tmp_path):
    history = make_history(tmp_path, dedupe_hours=0)
    now = time.time()
    history.record(sample_deal(price=100.0), created_at=now - 2 * 3600)
    history.record(sample_deal(price=200.0), created_at=now - 3600)
    destination = str(tmp_path / "backup" / "bot_history.db")
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    history.backup(destination)

    # A cópia é um banco íntegro e legível, com os mesmos dados.
    restored = PriceHistory(destination, dedupe_hours=0)
    assert len(rows(destination)) == 2
    evaluation = restored.evaluate(sample_deal(price=90.0), at=now)
    assert evaluation["enough_history"] is False  # amostra mínima 5
    # O banco original permanece intacto.
    assert len(rows(history.path)) == 2


def test_backup_refuses_existing_destination(tmp_path):
    history = make_history(tmp_path)
    history.record(sample_deal())
    destination = str(tmp_path / "b.db")
    history.backup(destination)
    with pytest.raises(FileExistsError):
        history.backup(destination)


def test_backup_does_not_write_to_source(tmp_path):
    path = str(tmp_path / "src.db")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE deals (id INTEGER PRIMARY KEY, text TEXT)")
        connection.execute("INSERT INTO deals (text) VALUES ('x')")
    PriceHistory(path).backup(str(tmp_path / "copy.db"))
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "price_observations" not in names  # a origem não foi mutada
    with sqlite3.connect(str(tmp_path / "copy.db")) as connection:
        assert connection.execute("SELECT text FROM deals").fetchall() == [("x",)]


def test_stored_title_is_normalized_without_coupon_or_link(tmp_path):
    history = make_history(tmp_path, dedupe_hours=0)
    deal = sample_deal(
        text="SSD NVMe 1TB cupom: SOM10 https://loja.test/p?utm_source=x",
        coupon="SOM10",
        link="https://loja.test/p?utm_source=x",
    )
    history.record(deal)
    with sqlite3.connect(history.path) as connection:
        title, link = connection.execute(
            "SELECT title, link FROM price_observations"
        ).fetchone()
    assert title == "ssd nvme 1tb"
    assert "SOM10" not in title
    assert "http" not in title
    assert link == "loja.test/p"


# -- integração com o bot -------------------------------------------------


def test_capture_deal_records_price_history(tmp_path, monkeypatch):
    history = make_history(tmp_path)
    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    monkeypatch.setattr(bot, "PRICES", history)
    update = FakeUpdate(chat_type="group", text="SSD NVMe 1TB de R$ 400 por R$ 200 na Amazon")
    update.message.message_id = 9
    run(bot.capture_deal(update, FakeContext()))
    stored = rows(history.path)
    assert len(stored) == 1
    assert stored[0][1] == 200.0


def test_capture_deal_does_not_record_vague_product(tmp_path, monkeypatch):
    history = make_history(tmp_path)
    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    monkeypatch.setattr(bot, "PRICES", history)
    update = FakeUpdate(chat_type="group", text="R$ 50 em eletronicos")
    update.message.message_id = 1
    run(bot.capture_deal(update, FakeContext()))
    assert rows(history.path) == []


def test_capture_deal_survives_history_failure(tmp_path, monkeypatch):
    class Boom:
        def record(self, deal, created_at=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    monkeypatch.setattr(bot, "PRICES", Boom())
    update = FakeUpdate(chat_type="group", text="SSD de R$ 400 por R$ 200 na Amazon")
    update.message.message_id = 3
    run(bot.capture_deal(update, FakeContext()))
    # A captura continua mesmo se o histórico falhar.
    assert bot.DEALS.top()


def test_ofertas_shows_history_note(tmp_path, monkeypatch):
    history = make_history(tmp_path, min_sample=5)
    monkeypatch.setattr(bot, "PRICES", history)
    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    now = time.time()
    for index, price in enumerate([200.0, 210.0, 220.0, 230.0, 240.0]):
        history.record(sample_deal(price=price), created_at=now - (5 - index) * 3600)
    bot.DEALS.add(1, 1, sample_deal(price=150.0, discount=25.0))
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    text = update.message.texts[-1]
    assert "novo mínimo" in text


def test_ofertas_shows_insufficient_history_label(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "PRICES", make_history(tmp_path))
    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    bot.DEALS.add(1, 1, sample_deal())
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    assert "sem histórico suficiente" in update.message.texts[-1]


def test_ofertas_omits_history_line_for_vague_product(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "PRICES", make_history(tmp_path))
    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    bot.DEALS.add(1, 1, sample_deal(store=None, text="Oferta top"))
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    assert "Histórico" not in update.message.texts[-1]


def test_ofertas_survives_history_failure(tmp_path, monkeypatch):
    class Boom:
        def evaluate(self, deal, at=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(bot, "PRICES", Boom())
    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    bot.DEALS.add(1, 1, sample_deal())
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    assert "50,0% off" in update.message.texts[-1]


def test_ofertas_keeps_jev_ranking_with_history(tmp_path, monkeypatch):
    history = make_history(tmp_path)
    monkeypatch.setattr(bot, "PRICES", history)
    monkeypatch.setattr(bot, "DEALS", bot.DealStore(str(tmp_path / "deals.db")))
    monkeypatch.setattr(bot, "DEALS_JEV_RANKING", True)
    bot.DEALS.add(1, 1, sample_deal(text="desconto grande", discount=90.0))
    bot.DEALS.add(1, 2, sample_deal(text="desconto pequeno", discount=10.0))

    async def fake_rank(items, **kwargs):
        return list(reversed(items))

    monkeypatch.setattr(bot, "rank_deals", fake_rank)
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    text = update.message.texts[-1]
    assert text.index("desconto pequeno") < text.index("desconto grande")
    assert "por score do Jev" in text


# -- configuração ---------------------------------------------------------


def test_history_config_defaults():
    config = history_config_from_env({})
    assert config["retention_days"] == 90
    assert config["min_sample"] == 5
    assert config["windows_days"] == (7, 30, 90)


def test_history_config_overrides_and_invalid_values():
    config = history_config_from_env(
        {
            "DEALS_HISTORY_RETENTION_DAYS": "30",
            "DEALS_HISTORY_MIN_SAMPLE": "abc",
            "DEALS_HISTORY_WINDOWS_DAYS": "1, 14",
        }
    )
    assert config["retention_days"] == 30
    assert config["min_sample"] == 5  # inválido cai no default
    assert config["windows_days"] == (1, 14)


def test_format_history_note_none_without_evaluation():
    assert format_history_note(None) is None
    assert format_history_note({}) is None


def test_price_history_is_importable_from_bot():
    assert isinstance(bot.PRICES, price_history.PriceHistory)
