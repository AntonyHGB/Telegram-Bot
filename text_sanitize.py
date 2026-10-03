"""Sanitização de título de produto (sem PII) para armazenamento e envio.

Extraído de `jev` para ser reutilizado **sem** a dependência de rede (`httpx`):
o importador offline (`telegram_export_import`) e o coletor MTProto usam este
módulo. Mantém o mesmo comportamento (padrões e limites) do piloto do Jev.
"""

import re

from deals import COUPON_RE, URL_RE

_TITLE_PATTERNS = (
    URL_RE,
    re.compile(r"\b(?:t\.me|telegram\.me)/\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"@[A-Za-z0-9_]{2,}"),
    re.compile(r"\d(?:[\s.\-()]*\d){7,}"),
    COUPON_RE,
    re.compile(r"R\$\s*[\d.,]+", re.IGNORECASE),
    re.compile(r"\d{1,3}\s*%\s*(?:off|de\s+desconto)?", re.IGNORECASE),
    re.compile(r"[^\w\s\-.,/&+]", re.UNICODE),
)

# Checagem residual fail-closed: se algo disso sobreviver apos a limpeza, o
# titulo nao e enviado (o estado usa o placeholder "produto").
_PERSONAL_RESIDUAL = (
    URL_RE,
    re.compile(r"\b(?:t\.me|telegram\.me)/\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"@[A-Za-z0-9_]{2,}"),
    re.compile(r"\d{3}\.\d{3}\.\d{3}-?\d{2}"),  # CPF
    re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-?\d{2}"),  # CNPJ
    re.compile(r"\d(?:[\s.\-()]*\d){7,}"),  # telefone/ID longo
    re.compile(
        r"\b(?:cpf|cnpj|pix|telefone|whats|whatsapp|zap|contato|chama|chamar)\b",
        re.IGNORECASE,
    ),
)
# Acima disso tratamos como texto livre (nao um titulo de produto) e omitimos.
_TITLE_MAX_WORDS = 15


def sanitize_title(text):
    """Deriva um titulo generico do produto (sem truncar) a partir do texto parseado."""
    cleaned = text or ""
    for pattern in _TITLE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -.,/")


def is_safe_title(title, limit=100):
    """True so se o titulo limpo nao tem dado pessoal residual nem cara de texto livre."""
    if not title or len(title) > limit or len(title.split()) > _TITLE_MAX_WORDS:
        return False
    return not any(pattern.search(title) for pattern in _PERSONAL_RESIDUAL)


def safe_title(text, limit=100):
    """Titulo generico do produto truncado, ou None se nao puder ser garantido seguro."""
    title = sanitize_title(text)
    if not is_safe_title(title, limit):
        return None
    return title[:limit].rstrip()
