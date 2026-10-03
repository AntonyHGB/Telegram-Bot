"""Piloto de ranking de ofertas com Jev (TypeSafe System One).

Campos que saem da maquina (por oferta, derivados e minimizados):

- titulo generico do produto (texto limpo: sem URL, @menção, e-mail, telefone,
  convite do Telegram, cupom, preco ou % off);
- preco e preco antigo;
- desconto;
- nome da loja.

Nunca saem: nomes/IDs de usuario, grupo/chat, links, mensagens brutas, cupons,
convites ou historico. O estado e montado a partir do objeto ja parseado pelo
regex local (`deals.parse_deal`); a mensagem original nunca e enviada.

Se a sanitizacao nao puder ser garantida, `rank_deals` retorna None e o
`/ofertas` mantem a ordenacao atual por desconto. Para desligar o piloto,
remova `DEALS_JEV_RANKING` (ou deixe-a falsa) e reinicie o bot.
"""

import httpx
from deals import format_brl
from text_sanitize import safe_title

JEV_URL = "https://opencode.ai/zen/v1/systemone"
JEV_DEFAULT_MODEL = "jev-1.13-free"
JEV_DEFAULT_TIMEOUT = 8.0
# Abaixo disso o score e tratado como incerto e o ranking e descartado.
JEV_MIN_CONFIDENCE = 0.5
# Teto de candidatos por chamada, mesmo se DEALS_TOP for maior.
JEV_MAX_CANDIDATES = 10

def build_state(deals):
    """Monta o `state` com apenas os campos derivados e minimizados."""
    lines = []
    for index, deal in enumerate(deals, start=1):
        title = safe_title(deal.get("text", ""))
        parts = [f"{index}. {title if title else 'produto'}"]
        if deal.get("price") is not None:
            parts.append(f"R$ {format_brl(deal['price'])}")
        if deal.get("old_price"):
            parts.append(f"(de R$ {format_brl(deal['old_price'])})")
        if deal.get("discount"):
            parts.append(f"{deal['discount']}% off")
        if deal.get("store"):
            parts.append(f"loja {deal['store']}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def build_questions(count):
    """Uma pergunta `score` por oferta, referenciando a linha numerada."""
    return {
        f"oferta_{index}": {
            "type": "score",
            "instructions": (
                f"Considerando apenas a oferta numero {index} da lista, "
                "quao atraente ela e para um consumidor brasileiro?"
            ),
            "criteria": ["Ruim", "Fraca", "Media", "Boa", "Excelente"],
        }
        for index in range(1, count + 1)
    }


def _score_for(answer):
    """Extrai (score, confidence) validos de uma resposta `score`, ou None."""
    if not isinstance(answer, dict):
        return None
    score = answer.get("score")
    confidence = answer.get("confidence")
    for value in (score, confidence):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    if confidence < JEV_MIN_CONFIDENCE:
        return None
    return score, confidence


def jev_enabled(flag, api_key):
    """Liga o piloto: flag explicita manda; sem flag, liga so se houver chave."""
    flag = (flag or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(api_key)


async def rank_deals(
    deals,
    *,
    api_key,
    model=JEV_DEFAULT_MODEL,
    timeout=JEV_DEFAULT_TIMEOUT,
    transport=None,
):
    """Reordena as ofertas pelo score do Jev.

    Retorna a lista reordenada em caso de sucesso, ou None quando o piloto deve
    ser ignorado (sem chave, lista vazia, falha HTTP/timeout, score incerto ou
    resposta invalida). Nunca levanta excecao: o `/ofertas` nao pode falhar.
    """
    if not api_key or not deals:
        return None
    deals = deals[:JEV_MAX_CANDIDATES]
    if not any(safe_title(deal.get("text", "")) for deal in deals):
        # Nenhum titulo seguro: sem sinal util, nao vale transmitir nada.
        return None
    payload = {
        "model": model,
        "state": build_state(deals),
        "questions": build_questions(len(deals)),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(
            trust_env=False, timeout=timeout, transport=transport
        ) as client:
            response = await client.post(JEV_URL, headers=headers, json=payload)
            response.raise_for_status()
            answers = response.json().get("answers")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(answers, dict):
        return None
    scores = []
    for index in range(len(deals)):
        extracted = _score_for(answers.get(f"oferta_{index + 1}"))
        if extracted is None:
            return None
        scores.append(extracted[0])
    order = sorted(range(len(deals)), key=lambda i: (-scores[i], i))
    return [deals[i] for i in order]
