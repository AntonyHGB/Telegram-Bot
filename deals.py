"""Parsing, storage and formatting of promotion deals captured from groups."""

import re
import sqlite3
import time
from contextlib import closing

PRICE_RE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)")
OFF_RE = re.compile(r"R\$\s*[\d.,]+\s*(?:off|de\s+desconto)", re.IGNORECASE)
DE_POR_RE = re.compile(
    r"de\s*R\$\s*([\d.,]+).{0,60}?por\s*(?:apenas\s*)?R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)
POR_RE = re.compile(r"(?:por|apenas|s[oó])\s+(?:apenas\s+)?R\$\s*([\d.,]+)", re.IGNORECASE)
DISCOUNT_RE = re.compile(r"(\d{1,3})\s*%\s*(?:off|de\s+desconto)?", re.IGNORECASE)
COUPON_RE = re.compile(
    r"(?:cupom|c[oó]digo|code)[:\s]*([A-Z0-9][A-Z0-9_-]{3,19})", re.IGNORECASE
)
URL_RE = re.compile(r"https?://\S+")

STORE_HINTS = (
    ("mercado livre", "Mercado Livre"),
    ("mercadolivre", "Mercado Livre"),
    ("magazine luiza", "Magazine Luiza"),
    ("magalu", "Magazine Luiza"),
    ("casas bahia", "Casas Bahia"),
    ("americanas", "Americanas"),
    ("submarino", "Submarino"),
    ("aliexpress", "AliExpress"),
    ("shopee", "Shopee"),
    ("amazon", "Amazon"),
    ("kabum", "KaBuM"),
    ("terabyte", "Terabyte"),
    ("pichau", "Pichau"),
    ("nuuvem", "Nuuvem"),
    ("steam", "Steam"),
    ("playstation", "PlayStation"),
    ("nintendo", "Nintendo"),
    ("epic games", "Epic Games"),
    ("xbox", "Xbox"),
    ("gog", "GOG"),
)


def parse_price(value):
    cleaned = value.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def detect_store(text):
    lowered = text.lower()
    for hint, name in STORE_HINTS:
        if hint in lowered:
            return name
    link = URL_RE.search(text)
    if link:
        host = link.group(0).split("//")[-1].split("/")[0].lower()
        compact = host.replace("-", "").replace(".", "")
        for hint, name in STORE_HINTS:
            if hint.replace(" ", "") in compact:
                return name
        return host.removeprefix("www.")
    return None


def parse_deal(text):
    if not text or "R$" not in text:
        return None
    cleaned = OFF_RE.sub(" ", text)
    prices = [price for price in (parse_price(item) for item in PRICE_RE.findall(cleaned)) if price]
    if not prices:
        return None
    old_price = new_price = None
    pair = DE_POR_RE.search(cleaned)
    if pair:
        old_price = parse_price(pair.group(1))
        new_price = parse_price(pair.group(2))
    else:
        found = POR_RE.search(cleaned)
        if found:
            new_price = parse_price(found.group(1))
        if len(prices) > 1:
            old_price = old_price or max(prices)
            new_price = new_price or min(prices)
        else:
            new_price = new_price or prices[0]
    if not new_price:
        return None
    discount = None
    explicit = DISCOUNT_RE.search(cleaned)
    if explicit:
        value = int(explicit.group(1))
        if 0 < value < 100:
            discount = float(value)
    if discount is None and old_price and old_price > new_price:
        discount = round((old_price - new_price) / old_price * 100, 1)
    coupon = COUPON_RE.search(cleaned)
    link = URL_RE.search(cleaned)
    return {
        "text": re.sub(r"\s+", " ", text).strip(),
        "price": new_price,
        "old_price": old_price if old_price and old_price > new_price else None,
        "discount": discount,
        "coupon": coupon.group(1).upper() if coupon else None,
        "store": detect_store(cleaned),
        "link": link.group(0).rstrip(").,") if link else None,
    }


def format_brl(value):
    number = f"{float(value):,.2f}"
    return number.replace(",", "X").replace(".", ",").replace("X", ".")


def format_percent(value):
    return f"{float(value):.1f}".replace(".", ",")


def format_deal(deal, index=None):
    prefix = f"{index}) " if index else ""
    if deal.get("discount"):
        headline = f"{prefix}{format_percent(deal['discount'])}% off"
    else:
        headline = f"{prefix}oferta"
    price = f"R$ {format_brl(deal['price'])}" if deal.get("price") is not None else "preço n/d"
    old = f" (de R$ {format_brl(deal['old_price'])})" if deal.get("old_price") else ""
    lines = [f"{headline} — {price}{old}"]
    if deal.get("store"):
        lines.append(f"Loja: {deal['store']}")
    if deal.get("coupon"):
        lines.append(f"Cupom: {deal['coupon']}")
    snippet = deal.get("text", "").strip()
    if len(snippet) > 220:
        snippet = snippet[:217] + "..."
    lines.append(snippet)
    if deal.get("link"):
        lines.append(deal["link"])
    return "\n".join(lines)


def format_deal_alert(deal):
    return "Oferta com desconto alto!\n\n" + format_deal(deal)


def deals_summary_prompt(deals):
    blocks = [format_deal(deal) for deal in deals]
    return (
        "Estas ofertas foram capturadas de grupos de promocao. Liste as 3 mais "
        "interessantes em portugues, considerando o maior desconto, e explique em "
        "uma frase o porque. Nao invente precos.\n\n" + "\n\n".join(blocks)
    )


class DealStore:
    def __init__(self, path):
        self.path = path
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS deals ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "chat_id INTEGER NOT NULL,"
                "message_id INTEGER NOT NULL,"
                "created_at REAL NOT NULL,"
                "text TEXT NOT NULL,"
                "price REAL,"
                "old_price REAL,"
                "discount REAL,"
                "coupon TEXT,"
                "store TEXT,"
                "link TEXT,"
                "UNIQUE(chat_id, message_id))"
            )

    def _connect(self):
        return sqlite3.connect(self.path)

    def add(self, chat_id, message_id, deal, created_at=None):
        created_at = created_at if created_at is not None else time.time()
        with closing(self._connect()) as connection, connection:
            if deal.get("link"):
                duplicate = connection.execute(
                    "SELECT 1 FROM deals WHERE link = ? AND created_at > ?",
                    (deal["link"], created_at - 6 * 3600),
                ).fetchone()
                if duplicate:
                    return False
            cursor = connection.execute(
                "INSERT OR IGNORE INTO deals (chat_id, message_id, created_at, text, price,"
                " old_price, discount, coupon, store, link) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_id,
                    message_id,
                    created_at,
                    deal["text"],
                    deal.get("price"),
                    deal.get("old_price"),
                    deal.get("discount"),
                    deal.get("coupon"),
                    deal.get("store"),
                    deal.get("link"),
                ),
            )
            return cursor.rowcount > 0

    def top(self, limit=5, hours=24, term=None):
        since = time.time() - hours * 3600
        query = (
            "SELECT text, price, old_price, discount, coupon, store, link, created_at"
            " FROM deals WHERE created_at >= ?"
        )
        params = [since]
        if term:
            query += " AND (text LIKE ? OR store LIKE ?)"
            params += [f"%{term}%", f"%{term}%"]
        query += " ORDER BY (discount IS NULL), discount DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "text": row[0],
                "price": row[1],
                "old_price": row[2],
                "discount": row[3],
                "coupon": row[4],
                "store": row[5],
                "link": row[6],
                "created_at": row[7],
            }
            for row in rows
        ]
