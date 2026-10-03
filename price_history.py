"""Histórico de preços de produtos (SQLite) para detectar ofertas realmente baratas.

Escopo
------

- Grava observações de preço de ofertas já parseadas por `deals.parse_deal`.
- Identidade conservadora: loja + título normalizado (+ URL canônica quando
  disponível). Cupom, contato, tracking e texto de promoção não entram na chave.
- Compara o preço atual com a mediana/mínimo de janelas anteriores, exigindo
  amostra mínima. Sem amostra suficiente não existe veredito: o rótulo é
  explicitamente "sem histórico suficiente".
- Separa preço listado (`price_kind = 'listed'`) de preço final. Cupom e frete
  não entram no preço efetivo nem no baseline.
- Retenção e dedupe configuráveis para o histórico não crescer sem limite.

O que este módulo **não** faz (limitações documentadas no readme):

- Não usa `old_price` anunciado como baseline (preço antigo pode ser fictício).
- Não distingue variantes/tamanhos que compartilhem loja e título.
- Não incorpora frete nem desconto de cupom ao preço comparado.
- Não infere promoção real sem amostra mínima de observações anteriores.
"""

import math
import os
import re
import sqlite3
import statistics
import time
from contextlib import closing
from urllib.parse import parse_qsl, urlsplit

from deals import COUPON_RE, URL_RE, format_percent

DEFAULT_RETENTION_DAYS = 90
DEFAULT_DEDUPE_HOURS = 6.0
DEFAULT_MIN_SAMPLE = 5
DEFAULT_WINDOWS_DAYS = (7, 30, 90)
DEFAULT_CHEAP_MEDIAN_PCT = 20.0

# Todo preço gravado hoje é o preço listado (o único confiável do regex).
PRICE_KIND_LISTED = "listed"

# Mínimo de tokens "específicos" para a chave. Títulos vagos (categoria pura)
# não ganham histórico: evita agregar produtos diferentes.
MIN_TITLE_TOKENS = 2
MIN_TITLE_CHARS = 8

_PROMO_WORDS = frozenset(
    {
        "promocao",
        "promoção",
        "promo",
        "oferta",
        "ofertas",
        "desconto",
        "imperdivel",
        "imperdível",
        "cupom",
        "frete",
        "gratis",
        "grátis",
        "apenas",
        "por",
        "de",
        "compre",
        "corre",
        "barato",
        "barata",
        "super",
        "link",
        "loja",
        "unidade",
        "unidades",
    }
)

_STOPWORDS = frozenset(
    {
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "e",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "do",
        "da",
        "dos",
        "das",
        "para",
        "pra",
        "que",
        "the",
        "and",
        "of",
        "with",
        "new",
    }
)

# Parâmetros de tracking que nunca devem influenciar a identidade.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "_ga",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "referrer",
        "aff_platform",
        "aff_id",
        "aff_source",
        "linkcode",
        "smid",
        "spm",
        "tag",
        "si",
    }
)


def normalize_title(text):
    """Normaliza o título para comparação: sem URL, cupom, preço, promo ou ruído."""
    cleaned = (text or "").lower()
    cleaned = URL_RE.sub(" ", cleaned)
    cleaned = COUPON_RE.sub(" ", cleaned)
    cleaned = re.sub(r"r\$\s*[\d.,]+", " ", cleaned)
    cleaned = re.sub(r"\d{1,3}\s*%\s*(?:off|de\s+desconto)?", " ", cleaned)
    cleaned = re.sub(r"[\W_]+", " ", cleaned, flags=re.UNICODE)
    tokens = [
        token
        for token in cleaned.split()
        if token
        and token not in _STOPWORDS
        and token not in _PROMO_WORDS
        and len(token) > 1
    ]
    return " ".join(tokens)


def _title_is_specific(title):
    """True só quando o título identifica um produto, não uma categoria vaga.

    Exige ao menos 2 tokens, tamanho mínimo e um sinal de especificidade: um
    token com dígito (modelo/medida) ou ao menos 3 tokens distintos.
    """
    tokens = title.split()
    if len(tokens) < MIN_TITLE_TOKENS or len(title) < MIN_TITLE_CHARS:
        return False
    if len(set(tokens)) < 2:
        return False
    has_digit = any(any(char.isdigit() for char in token) for token in tokens)
    return has_digit or len(tokens) >= 3


def canonical_url(link):
    """URL canônica conservadora: host + path + query sem parâmetros de tracking."""
    if not link:
        return None
    try:
        parsed = urlsplit(link)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower().removeprefix("www.")
    if not host:
        return None
    path = parsed.path.rstrip("/").lower()
    params = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_PARAMS
    )
    query = "&".join(f"{key}={value}" for key, value in params)
    canonical = f"{host}{path}"
    return f"{canonical}?{query}" if query else canonical


def identity_key(deal):
    """Chave conservadora (loja + título normalizado + URL canônica) ou None.

    Retorna None quando não há loja ou quando o título é vago demais para
    identificar um produto — nesse caso nada é gravado nem comparado.
    """
    store = (deal.get("store") or "").strip()
    title = normalize_title(deal.get("text", ""))
    if not store or not _title_is_specific(title):
        return None
    parts = [store.lower(), title]
    url = canonical_url(deal.get("link"))
    if url:
        parts.append(url)
    return "|".join(parts)


class PriceHistory:
    """Histórico de preços com migração aditiva e idempotente.

    A criação da tabela é preguiçosa (no primeiro acesso) e usa
    `CREATE TABLE/INDEX IF NOT EXISTS`, então não altera o schema existente nem
    o conteúdo de `deals`/`reminders`/`history`.
    """

    def __init__(
        self,
        path,
        *,
        retention_days=DEFAULT_RETENTION_DAYS,
        dedupe_hours=DEFAULT_DEDUPE_HOURS,
        min_sample=DEFAULT_MIN_SAMPLE,
        windows_days=DEFAULT_WINDOWS_DAYS,
        cheap_median_pct=DEFAULT_CHEAP_MEDIAN_PCT,
        busy_timeout_ms=5000,
    ):
        self.path = path
        self.retention_days = max(1, int(retention_days))
        self.dedupe_hours = max(0.0, float(dedupe_hours))
        self.min_sample = max(1, int(min_sample))
        windows = tuple(sorted({int(day) for day in windows_days if int(day) > 0}))
        self.windows_days = windows or DEFAULT_WINDOWS_DAYS
        self.cheap_median_pct = float(cheap_median_pct)
        self.busy_timeout_ms = int(busy_timeout_ms)

    # -- conexão / schema -------------------------------------------------

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:  # pragma: no cover - alguns FS não suportam WAL
            pass
        return connection

    def _ensure_schema(self, connection):
        connection.execute(
            "CREATE TABLE IF NOT EXISTS price_observations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "identity_key TEXT NOT NULL,"
            "store TEXT,"
            "title TEXT,"
            "price REAL NOT NULL,"
            "price_kind TEXT NOT NULL DEFAULT 'listed',"
            "link TEXT,"
            "created_at REAL NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_obs_identity"
            " ON price_observations(identity_key, price_kind, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_obs_created"
            " ON price_observations(created_at)"
        )

    # -- gravação ---------------------------------------------------------

    @staticmethod
    def _valid_price(price):
        if price is None or isinstance(price, bool):
            return False
        try:
            return math.isfinite(price) and price > 0
        except TypeError:
            return False

    def record(self, deal, created_at=None, connection=None):
        """Grava a observação (se confiável) e devolve a avaliação do preço.

        Retorna None quando o produto/preço não são confiáveis (sem loja, título
        vago ou preço inválido). Quando a observação é apenas uma repetição
        dentro da janela de dedupe, não grava e devolve None.

        `connection` opcional permite transação do chamador: sem ela, abre e
        fecha a própria conexão; com ela, não faz commit (quem chamou controla).
        """
        price = deal.get("price")
        if not self._valid_price(price):
            return None
        key = identity_key(deal)
        if not key:
            return None
        created_at = time.time() if created_at is None else created_at
        if connection is not None:
            self._ensure_schema(connection)
            return self._record_with(connection, deal, key, price, created_at)
        with closing(self._connect()) as conn, conn:
            self._ensure_schema(conn)
            return self._record_with(conn, deal, key, price, created_at)

    def _record_with(self, connection, deal, key, price, created_at):
        if self._is_duplicate(connection, key, price, created_at):
            return None
        evaluation = self._evaluate_with(connection, key, price, created_at)
        connection.execute(
            "INSERT INTO price_observations"
            " (identity_key, store, title, price, price_kind, link, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                deal.get("store"),
                normalize_title(deal.get("text", "")),
                float(price),
                PRICE_KIND_LISTED,
                canonical_url(deal.get("link")),
                created_at,
            ),
        )
        self._prune_with(connection, created_at)
        return evaluation

    def _is_duplicate(self, connection, key, price, created_at):
        if self.dedupe_hours <= 0:
            return False
        since = created_at - self.dedupe_hours * 3600
        row = connection.execute(
            "SELECT 1 FROM price_observations"
            " WHERE identity_key = ? AND price_kind = ? AND price = ? AND created_at > ?"
            " LIMIT 1",
            (key, PRICE_KIND_LISTED, float(price), since),
        ).fetchone()
        return row is not None

    def _prune_with(self, connection, now):
        cutoff = now - self.retention_days * 86400
        connection.execute("DELETE FROM price_observations WHERE created_at < ?", (cutoff,))

    def prune(self, now=None):
        """Remove observações além da retenção. Devolve quantas foram apagadas."""
        now = time.time() if now is None else now
        with closing(self._connect()) as connection, connection:
            self._ensure_schema(connection)
            cutoff = now - self.retention_days * 86400
            cursor = connection.execute(
                "DELETE FROM price_observations WHERE created_at < ?", (cutoff,)
            )
            return cursor.rowcount

    def backup(self, destination):
        """Cópia consistente via API de backup do SQLite (segura com WAL ativo).

        - Não escreve na origem: usa uma conexão só de leitura e **não** cria
          schema nem troca o modo de journal.
        - Recusa sobrescrever um backup já existente (`FileExistsError`).

        Deve ser preferida a `cp` do arquivo: com WAL há dados recentes no
        `-wal`/`-shm`, e copiar só o `.db` pode perder ou corromper informação.
        """
        if os.path.exists(destination):
            raise FileExistsError(f"backup já existe: {destination}")
        source = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        try:
            source.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            dest = sqlite3.connect(destination)
            try:
                source.backup(dest)
                dest.commit()
            finally:
                dest.close()
        finally:
            source.close()
        return destination

    # -- avaliação --------------------------------------------------------

    def evaluate(self, deal, at=None):
        """Avalia o preço atual contra o histórico anterior (sem gravar).

        Devolve None quando o produto não é identificável. Devolve um dict com
        `enough_history=False` quando a amostra é insuficiente.
        """
        at = time.time() if at is None else at
        with closing(self._connect()) as connection:
            self._ensure_schema(connection)
            return self._evaluate_one(connection, deal, at)

    def evaluate_many(self, deals, at=None):
        """Avalia vários deals numa única conexão/schema (uso no `/ofertas`).

        Devolve uma lista alinhada a `deals`, com None para produtos não
        identificáveis. Mantém a mesma semântica de `evaluate`.
        """
        at = time.time() if at is None else at
        with closing(self._connect()) as connection:
            self._ensure_schema(connection)
            return [self._evaluate_one(connection, deal, at) for deal in deals]

    def _evaluate_one(self, connection, deal, at):
        price = deal.get("price")
        if not self._valid_price(price):
            return None
        key = identity_key(deal)
        if not key:
            return None
        return self._evaluate_with(connection, key, price, at)

    def _evaluate_with(self, connection, key, price, at):
        # Uma única leitura cobre a maior janela; as demais são filtradas em memória.
        earliest = at - max(self.windows_days) * 86400
        observations = connection.execute(
            "SELECT price, created_at FROM price_observations"
            " WHERE identity_key = ? AND price_kind = ?"
            " AND created_at < ? AND created_at >= ?",
            (key, PRICE_KIND_LISTED, at, earliest),
        ).fetchall()

        windows = {}
        qualifying = []
        for days in self.windows_days:
            since = at - days * 86400
            prices = sorted(row[0] for row in observations if row[1] >= since)
            sample = len(prices)
            if sample < self.min_sample:
                windows[days] = {"sample": sample, "median": None, "min": None, "cheap": None}
                continue
            median = statistics.median(prices)
            minimum = prices[0]
            cheap = self._is_cheap(price, median, minimum)
            windows[days] = {
                "sample": sample,
                "median": median,
                "min": minimum,
                "cheap": cheap,
            }
            qualifying.append(days)

        evaluation = {
            "identity_key": key,
            "price": float(price),
            "price_kind": PRICE_KIND_LISTED,
            "enough_history": bool(qualifying),
            "is_cheap": None,
            "at_min": False,
            "vs_median_pct": None,
            "primary": None,
            "windows": windows,
        }
        if not qualifying:
            return evaluation

        # Conservador: só é "realmente barato" se todas as janelas com amostra
        # suficiente concordarem. A janela MAIS CURTA com amostra é a referência de
        # exibição: é a mais recente e evita alegar "90d" quando há poucas horas de
        # dados (o texto ainda informa o número de observações).
        primary_days = min(qualifying)
        primary = windows[primary_days]
        evaluation["is_cheap"] = all(windows[day]["cheap"] for day in qualifying)
        evaluation["at_min"] = price <= primary["min"]
        if primary["median"] > 0:
            evaluation["vs_median_pct"] = (primary["median"] - price) / primary["median"] * 100
        evaluation["primary"] = {
            "days": primary_days,
            "median": primary["median"],
            "min": primary["min"],
            "sample": primary["sample"],
        }
        return evaluation

    def _is_cheap(self, price, median, minimum):
        if price <= minimum:
            return True
        if median <= 0:
            return False
        return (median - price) / median * 100 >= self.cheap_median_pct


def history_config_from_env(environ=None):
    """Lê a configuração de histórico das variáveis de ambiente (defaults seguros)."""
    environ = os.environ if environ is None else environ

    def _int(name, default):
        try:
            return int(environ.get(name, default))
        except (TypeError, ValueError):
            return int(default)

    def _float(name, default):
        try:
            return float(environ.get(name, default))
        except (TypeError, ValueError):
            return float(default)

    raw_windows = environ.get("DEALS_HISTORY_WINDOWS_DAYS", "7,30,90")
    windows = tuple(
        int(item) for item in re.split(r"[,\s]+", raw_windows) if item.strip().isdigit()
    )
    return {
        "retention_days": _int("DEALS_HISTORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
        "dedupe_hours": _float("DEALS_HISTORY_DEDUPE_HOURS", DEFAULT_DEDUPE_HOURS),
        "min_sample": _int("DEALS_HISTORY_MIN_SAMPLE", DEFAULT_MIN_SAMPLE),
        "windows_days": windows or DEFAULT_WINDOWS_DAYS,
        "cheap_median_pct": _float("DEALS_HISTORY_CHEAP_PCT", DEFAULT_CHEAP_MEDIAN_PCT),
    }


def format_history_note(evaluation):
    """Linha curta de histórico para exibição, ou None se não houver avaliação."""
    if not evaluation:
        return None
    if not evaluation.get("enough_history"):
        return "Histórico: sem histórico suficiente"
    primary = evaluation.get("primary") or {}
    days = primary.get("days")
    sample = primary.get("sample")
    if evaluation.get("is_cheap"):
        if evaluation.get("at_min"):
            return f"Histórico: novo mínimo em {days}d ({sample} obs)"
        return (
            f"Histórico: {format_percent(evaluation['vs_median_pct'])}% abaixo da mediana"
            f" de {days}d ({sample} obs)"
        )
    return f"Histórico: dentro do normal de {days}d ({sample} obs)"
