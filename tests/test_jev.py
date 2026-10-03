import json

import httpx
import pytest

from jev import (
    JEV_DEFAULT_MODEL,
    JEV_URL,
    build_state,
    jev_enabled,
    rank_deals,
    safe_title,
)
from test_bot import run
from text_sanitize import sanitize_title


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


def score_response(count, scores, confidence=0.9):
    answers = {}
    for index in range(1, count + 1):
        answers[f"oferta_{index}"] = {
            "type": "score",
            "score": scores[index - 1],
            "confidence": confidence,
            "probabilities": {},
        }
    return {"model": "jev-1.13.0", "answers": answers, "usage": {}}


def test_sanitize_title_strips_sensitive_and_promo_noise():
    title = sanitize_title(
        "SSD NVMe @promo_bot fone: 11 98888-7777 cupom: SOM10 "
        "https://t.me/+abc123 R$ 200,00 50% off"
    )
    assert "http" not in title.lower()
    assert "t.me" not in title.lower()
    assert "@" not in title
    assert "98888" not in title
    assert "SOM10" not in title
    assert "R$" not in title
    assert "SSD NVMe" in title


def test_build_state_keeps_only_derived_fields():
    state = build_state(
        [
            sample_deal(
                text="Oferta @vendedor https://amzn.to/x t.me/convite 11 98888-7777",
                link="https://amzn.to/x",
            )
        ]
    )
    assert "Amazon" in state
    assert "R$ 200,00" in state
    assert "50.0% off" in state
    assert "amzn.to" not in state
    assert "@" not in state
    assert "98888" not in state


def test_safe_title_accepts_products_and_rejects_personal_or_free_text():
    assert safe_title("SSD NVMe 1TB") == "SSD NVMe 1TB"
    assert safe_title("Fone Sony WH-1000XM5") == "Fone Sony WH-1000XM5"
    assert safe_title("me chama no zap pra negociar o fone") is None
    assert safe_title("cpf 123.456.789-00 chama no whatsapp") is None
    assert safe_title(" ".join(["palavra"] * 16)) is None


def test_build_state_omits_unsafe_title():
    state = build_state([sample_deal(text="me chama no zap @vendedor pra fechar o fone")])
    assert "zap" not in state.lower()
    assert "@" not in state
    assert "produto" in state


def test_rank_deals_skips_call_without_safe_title():
    def respond(request):  # pragma: no cover - nao deve ser chamado
        raise AssertionError("nao deveria chamar o Jev sem titulo seguro")

    deals = [sample_deal(text="cpf 123.456.789-00 chama no zap")]
    result = run(rank_deals(deals, api_key="k", transport=httpx.MockTransport(respond)))
    assert result is None


def test_jev_enabled_requires_key_or_explicit_flag():
    assert jev_enabled("", "") is False
    assert jev_enabled("", "key") is True
    assert jev_enabled("1", "") is True
    assert jev_enabled("on", "") is True
    assert jev_enabled("0", "key") is False
    assert jev_enabled("false", "key") is False


def test_rank_deals_reorders_by_score_and_sends_sanitized_payload():
    captured = {}

    def respond(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=score_response(2, [0.0, 4.0]))

    deals = [
        sample_deal(text="Oferta ruim https://amzn.to/x", discount=90.0),
        sample_deal(text="Oferta boa", discount=10.0),
    ]
    ranked = run(
        rank_deals(
            deals,
            api_key="test-key",
            transport=httpx.MockTransport(respond),
        )
    )
    assert [deal["text"] for deal in ranked] == ["Oferta boa", "Oferta ruim https://amzn.to/x"]
    assert captured["url"] == JEV_URL
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == JEV_DEFAULT_MODEL
    assert set(captured["body"]["questions"]) == {"oferta_1", "oferta_2"}
    # Nenhum campo sensivel cru deve aparecer no payload enviado.
    assert "amzn.to" not in json.dumps(captured["body"])
    assert "chat_id" not in captured["body"]


def test_rank_deals_without_key_does_not_call():
    def respond(request):  # pragma: no cover - nao deve ser chamado
        raise AssertionError("nao deveria chamar o Jev sem chave")

    assert (
        run(rank_deals([sample_deal()], api_key="", transport=httpx.MockTransport(respond)))
        is None
    )


@pytest.mark.parametrize(
    "respond",
    [
        lambda request: httpx.Response(500, json={"error": "boom"}),
        lambda request: httpx.Response(200, text="nao e json"),
        lambda request: httpx.Response(200, json={"answers": {}}),
        lambda request: httpx.Response(
            200, json={"answers": {"oferta_1": {"type": "score", "score": "alto", "confidence": 1}}}
        ),
        lambda request: httpx.Response(200, json=score_response(1, [4.0], confidence=0.1)),
    ],
)
def test_rank_deals_falls_back_on_bad_response(respond):
    result = run(
        rank_deals([sample_deal()], api_key="k", transport=httpx.MockTransport(respond))
    )
    assert result is None


def test_rank_deals_falls_back_on_timeout():
    def respond(request):
        raise httpx.ReadTimeout("timeout")

    result = run(
        rank_deals([sample_deal()], api_key="k", transport=httpx.MockTransport(respond))
    )
    assert result is None


def test_rank_deals_limits_candidates():
    seen = {}

    def respond(request):
        seen["count"] = len(json.loads(request.content)["questions"])
        return httpx.Response(200, json=score_response(1, [1.0]))

    deals = [sample_deal(text=f"oferta {i}") for i in range(30)]
    run(rank_deals(deals, api_key="k", transport=httpx.MockTransport(respond)))
    assert seen["count"] <= 10
