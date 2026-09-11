import pytest

import bot_tele_parrot as bot
from deals import DealStore, format_deal, parse_deal, parse_price
from test_bot import FakeCallbackQuery, FakeContext, FakeUpdate, run


@pytest.fixture
def deals(tmp_path, monkeypatch):
    store = DealStore(str(tmp_path / "deals.db"))
    monkeypatch.setattr(bot, "DEALS", store)
    return store


def sample_deal(**overrides):
    deal = {
        "text": "SSD NVMe 1TB",
        "price": 200.0,
        "old_price": 400.0,
        "discount": 50.0,
        "coupon": None,
        "store": "Amazon",
        "link": None,
    }
    deal.update(overrides)
    return deal


def test_parse_price():
    assert parse_price("1.234,56") == 1234.56
    assert parse_price("70") == 70.0
    assert parse_price("12,90") == 12.9
    assert parse_price("abc") is None


def test_parse_deal_de_por():
    deal = parse_deal("SSD de R$ 400,00 por R$ 200,00 na Amazon")
    assert deal["price"] == 200.0
    assert deal["old_price"] == 400.0
    assert deal["discount"] == 50.0
    assert deal["store"] == "Amazon"


def test_parse_deal_ignores_off_amount():
    assert parse_deal("VOLTOU CUPOM NA SHOPEE R$70 OFF em eletronicos") is None


def test_parse_deal_coupon_store_link():
    deal = parse_deal("Fone por apenas R$ 89,90, cupom: SOM10 https://shopee.com.br/produto")
    assert deal["price"] == 89.9
    assert deal["coupon"] == "SOM10"
    assert deal["store"] == "Shopee"
    assert deal["link"] == "https://shopee.com.br/produto"


def test_parse_deal_explicit_discount():
    deal = parse_deal("RTX 4060 30% off por R$ 1.899,00")
    assert deal["discount"] == 30.0
    assert deal["price"] == 1899.0


def test_store_ranks_by_discount(deals):
    deals.add(1, 1, sample_deal(discount=10.0, text="dez"))
    deals.add(1, 2, sample_deal(discount=70.0, text="setenta"))
    deals.add(1, 3, sample_deal(discount=None, text="sem desconto"))
    top = deals.top()
    assert [deal["text"] for deal in top] == ["setenta", "dez", "sem desconto"]


def test_store_filters_by_term(deals):
    deals.add(1, 1, sample_deal(text="SSD NVMe", discount=20.0))
    deals.add(1, 2, sample_deal(text="Fone bluetooth", discount=50.0))
    top = deals.top(term="ssd")
    assert len(top) == 1
    assert top[0]["text"] == "SSD NVMe"


def test_store_dedups_repeated_link(deals):
    first = sample_deal(link="https://amzn.to/x")
    second = sample_deal(link="https://amzn.to/x")
    assert deals.add(1, 1, first) is True
    assert deals.add(2, 2, second) is False
    assert len(deals.top()) == 1


def test_store_ignores_duplicate_message(deals):
    assert deals.add(1, 1, sample_deal()) is True
    assert deals.add(1, 1, sample_deal(text="outro")) is False


def test_capture_stores_group_message(deals):
    update = FakeUpdate(chat_type="group", text="SSD de R$ 400 por R$ 200 na Amazon")
    update.message.message_id = 9
    run(bot.capture_deal(update, FakeContext()))
    top = deals.top()
    assert len(top) == 1
    assert top[0]["discount"] == 50.0


def test_capture_ignores_private_messages(deals):
    update = FakeUpdate(text="SSD de R$ 400 por R$ 200 na Amazon")
    run(bot.capture_deal(update, FakeContext()))
    assert deals.top() == []


def test_capture_alerts_high_discount(deals, monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    update = FakeUpdate(chat_type="group", text="SSD de R$ 400 por R$ 200 na Amazon")
    context = FakeContext()
    run(bot.capture_deal(update, context))
    assert context.bot.messages
    assert context.bot.messages[0][0] == 42
    assert "desconto alto" in context.bot.messages[0][1].lower()


def test_capture_does_not_alert_low_discount(deals, monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {42})
    update = FakeUpdate(chat_type="group", text="SSD de R$ 100 por R$ 95 na Amazon")
    context = FakeContext()
    run(bot.capture_deal(update, context))
    assert context.bot.messages == []


def test_ofertas_requires_allowed_user(deals, monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", set())
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    assert "restrito" in update.message.texts[0].lower()


def test_ofertas_lists_deals(deals):
    deals.add(1, 1, sample_deal())
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    text = update.message.texts[-1]
    assert "50,0% off" in text
    assert "R$ 200,00" in text
    assert "Amazon" in text
    assert update.message.markups[-1] is not None


def test_ofertas_without_deals(deals):
    update = FakeUpdate()
    run(bot.ofertas(update, FakeContext()))
    assert "Nenhuma oferta" in update.message.texts[-1]


def test_ofertas_summary_callback(deals, monkeypatch):
    deals.add(1, 1, sample_deal())
    captured = {}
    monkeypatch.setattr(bot, "stream_chat", fake_stream(["Melhor: ", "SSD."], captured))
    query = FakeCallbackQuery("deals:summary")
    update = FakeUpdate()
    update.callback_query = query
    run(bot.deals_summary_callback(update, FakeContext()))
    assert "Resumo IA" in query.message.texts[-1]
    assert "SSD." in query.message.texts[-1]
    assert "grupos de promocao" in captured["messages"][-1]["content"]


def fake_stream(chunks, captured=None):
    async def _stream(messages, base_url, timeout=bot.LLM_TIMEOUT):
        if captured is not None:
            captured["messages"] = messages
            captured["base_url"] = base_url
        for chunk in chunks:
            yield chunk

    return _stream


def test_format_deal_with_coupon_and_old_price():
    text = format_deal(sample_deal(coupon="X10", link="https://x.test"), index=1)
    assert "1) 50,0% off" in text
    assert "R$ 200,00 (de R$ 400,00)" in text
    assert "Cupom: X10" in text
    assert "https://x.test" in text
