"""Importação offline de UM chat do export JSON do Telegram Desktop para SQLite.

Escopo
------
- Lê o `result.json` de um export de chat único (top-level `id`/`type`/`messages`).
- Valida o tipo do chat e uma allowlist explícita (um único chat autorizado).
- Checkpoint idempotente por mensagem (`UNIQUE source + source_chat_id + message_id`),
  sem texto bruto de mensagens que não são oferta.
- Ao aplicar, alimenta `deals` (`DealStore`) e `price_observations`
  (`PriceHistory`) com os **timestamps originais**, dentro de uma única
  transação por chat, e faz backfill limitado à retenção (90 dias).
- Timestamps vêm de `date_unixtime` (UTC) e a importação é cronológica.

Privacidade e minimização
-------------------------
- Só ofertas parseadas viram linha em `deals`/`price_observations`.
- O texto gravado em `deals` é minimizado com o sanitizador existente do Jev
  (`sanitize_title` + `normalize_title` + `is_safe_title`); se não for seguro,
  vira o placeholder `produto`. Nunca se grava a mensagem bruta.
- Remetente (`from`/`from_id`), nome do chat, reações e mídia não são gravados.
- Só o caminho relativo seguro da foto é preservado, e apenas em ofertas.
- Não lê, copia nem transmite mídia; não faz rede, login nem notificação
  Telegram (não importa o bot).

Identidade e colisão (Telegram Desktop vs Bot API)
--------------------------------------------------
O id do chat do Telegram Desktop é guardado como TEXT no namespace `source`
(padrão `tdesktop_export`). Para alimentar `deals.chat_id INTEGER` sem colidir
com ids reais do Bot API, cada origem ganha um id reservado em
`telegram_import_origins`, alocado a partir de `IMPORT_CHAT_ID_BASE`, fora da
faixa usada pelo Telegram. O rollback apaga só as linhas dessa origem, então
nunca remove ofertas capturadas ao vivo.
"""

import argparse
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from deals import DealStore, parse_deal
from price_history import PriceHistory, canonical_url, normalize_title
from text_sanitize import is_safe_title, sanitize_title

DEFAULT_SOURCE = "tdesktop_export"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_ALLOWLIST_NAME = ".telegram_import_allowlist.json"

# Faixa reservada para ids de chat sintéticos da importação. O Telegram usa
# ids bem menores em magnitude (supergrupos ~ -1e12), então este valor fica
# fora de qualquer colisão prática com o Bot API.
IMPORT_CHAT_ID_BASE = -2_000_000_000_000_000_000

# Tipos de chat aceitos: grupo/supergrupo (gate original) e canais, aceitos
# explicitamente no piloto para este único chat.
ALLOWED_CHAT_TYPES = frozenset(
    {
        "group",
        "supergroup",
        "public_group",
        "private_group",
        "public_supergroup",
        "private_supergroup",
        "public_channel",
        "private_channel",
    }
)

MAX_TEXT_WORDS = 12
_PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_PHOTO_DIR = "photos"
_PLACEHOLDER_TITLE = "produto"


class ExportError(Exception):
    """Erro de export inválido, fora da allowlist ou de tipo não permitido."""


def flatten_text(text):
    """Converte `text` (str ou lista de entidades) em texto plano minimizado.

    Entidades de formatação são descartadas; só o texto é preservado. Devolve
    None para tipos inesperados (input malicioso) e para texto vazio.
    """
    if isinstance(text, str):
        raw = text
    elif isinstance(text, list):
        parts = []
        for part in text:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                value = part.get("text")
                if isinstance(value, str):
                    parts.append(value)
        raw = "".join(parts)
    else:
        return None
    normalized = re.sub(r"\s+", " ", raw).strip()
    return normalized or None


def safe_photo_path(value):
    """Caminho relativo seguro dentro de `photos/`, ou None.

    Rejeita absolutos, `..`, prefixo fora de `photos/` e extensões de mídia
    desconhecidas. Nunca abre nem resolve o arquivo.
    """
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    if normalized.startswith("/"):
        return None
    parts = normalized.split("/")
    if any(part in ("", "..") for part in parts):
        return None
    if parts[0] != _PHOTO_DIR:
        return None
    if not parts[-1].lower().endswith(_PHOTO_EXTS):
        return None
    return "/".join(parts)


def load_allowlist(path):
    """Lê a allowlist privada e devolve {source: {chat_id(str), ...}}."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    allowed = {}
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, dict):
        return allowed
    for source, config in sources.items():
        chats = config.get("chats") if isinstance(config, dict) else None
        if not isinstance(chats, list):
            continue
        ids = {
            str(item["id"])
            for item in chats
            if isinstance(item, dict) and item.get("id") is not None
        }
        allowed[str(source)] = ids
    return allowed


def parse_export(path):
    """Lê o `result.json` de um chat único e devolve só os metadados mínimos."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ExportError("export inválido")
    chat_id = data.get("id")
    chat_type = data.get("type")
    messages = data.get("messages")
    if chat_id is None or not isinstance(chat_type, str):
        raise ExportError("export sem id/type de chat")
    if not isinstance(messages, list):
        raise ExportError("export sem lista de mensagens")
    return {"chat_id": str(chat_id), "chat_type": chat_type, "messages": messages}


def minimized_text(raw):
    """Título de produto minimizado e seguro, ou o placeholder.

    Usa o sanitizador existente do Jev (remove URL, @, e-mail, telefone,
    cupom, preço e %) e depois o normalizador do histórico (sem stopwords,
    promoção, pontuação). Se sobrar PII residual ou nada, devolve `produto`.
    """
    if not raw:
        return _PLACEHOLDER_TITLE
    tokens = normalize_title(sanitize_title(raw))
    if not tokens:
        return _PLACEHOLDER_TITLE
    tokens = " ".join(tokens.split()[:MAX_TEXT_WORDS])
    if not is_safe_title(tokens):
        return _PLACEHOLDER_TITLE
    return tokens


def _stored_deal(record):
    """Dict de oferta com texto minimizado, pronto para DealStore/PriceHistory."""
    return {
        "text": record["text"],
        "price": record["price"],
        "old_price": record["old_price"],
        "discount": record["discount"],
        "coupon": record["coupon"],
        "store": record["store"],
        "link": record["link"],
    }


def build_record(message_id, posted_at, text, photo=None):
    """Projeta uma mensagem (export ou MTProto) no registro minimizado padrão.

    Reutilizado pelo importador offline e pelo coletor MTProto para garantir o
    mesmo parsing/minimização e o mesmo shape de checkpoint. `text` é o texto
    plano já extraído; `photo` é um caminho relativo opcional (nunca baixado).
    """
    deal = parse_deal(text) if text else None
    return {
        "message_id": message_id,
        "posted_at": float(posted_at),
        "is_deal": 1 if deal else 0,
        "text": minimized_text(deal.get("text")) if deal else None,
        "price": deal.get("price") if deal else None,
        "old_price": deal.get("old_price") if deal else None,
        "discount": deal.get("discount") if deal else None,
        "coupon": deal.get("coupon") if deal else None,
        "store": deal.get("store") if deal else None,
        "link": canonical_url(deal.get("link")) if deal else None,
        "photo_path": safe_photo_path(photo) if deal else None,
    }


def _build_records(messages):
    """Projeta as mensagens em registros cronológicos e contadores agregados.

    Só mensagens `type == "message"` com `id`/`date_unixtime` válidos entram.
    O texto minimizado só é mantido para ofertas; o caminho de foto só é
    mantido para ofertas e se for relativo e seguro.
    """
    records = []
    stats = {
        "skipped": 0,
        "deals": 0,
        "photos_present": 0,
        "photos_safe": 0,
        "photos_rejected": 0,
    }
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "message":
            stats["skipped"] += 1
            continue
        message_id = message.get("id")
        raw_date = message.get("date_unixtime")
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            stats["skipped"] += 1
            continue
        if not str(raw_date).isdigit():
            stats["skipped"] += 1
            continue

        text = flatten_text(message.get("text"))
        deal = parse_deal(text) if text else None

        photo_raw = message.get("photo")
        if isinstance(photo_raw, str):
            stats["photos_present"] += 1
        photo_safe = safe_photo_path(photo_raw)
        if photo_safe:
            stats["photos_safe"] += 1
        elif isinstance(photo_raw, str):
            stats["photos_rejected"] += 1

        if deal:
            stats["deals"] += 1
        records.append(build_record(message_id, int(raw_date), text, photo_raw))
    records.sort(key=lambda record: (record["posted_at"], record["message_id"]))
    return records, stats


def _table_exists(connection, name):
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


class ImportStore:
    """Checkpoint, mapeamento de origem e feed das tabelas do bot.

    Cria tabelas próprias (`telegram_import_*`) com `CREATE TABLE/INDEX IF NOT
    EXISTS`, sem alterar `deals`, `history`, `reminders` nem
    `price_observations`. O feed usa as APIs existentes (`DealStore.add`,
    `PriceHistory.record`) com uma conexão compartilhada, então tudo entra numa
    única transação.
    """

    def __init__(self, path, *, busy_timeout_ms=5000):
        self.path = path
        self.busy_timeout_ms = int(busy_timeout_ms)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:  # pragma: no cover - FS sem WAL
            pass
        return connection

    def _ensure_schema(self, connection):
        connection.execute(
            "CREATE TABLE IF NOT EXISTS telegram_import_origins ("
            "source TEXT NOT NULL,"
            "source_chat_id TEXT NOT NULL,"
            "deals_chat_id INTEGER NOT NULL UNIQUE,"
            "chat_type TEXT NOT NULL,"
            "created_at REAL NOT NULL,"
            "PRIMARY KEY (source, source_chat_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS telegram_import_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "source TEXT NOT NULL,"
            "source_chat_id TEXT NOT NULL,"
            "message_id INTEGER NOT NULL,"
            "posted_at REAL NOT NULL,"
            "is_deal INTEGER NOT NULL DEFAULT 0,"
            "text TEXT,"
            "price REAL,"
            "old_price REAL,"
            "discount REAL,"
            "coupon TEXT,"
            "store TEXT,"
            "link TEXT,"
            "photo_path TEXT,"
            "imported_at REAL NOT NULL,"
            "UNIQUE (source, source_chat_id, message_id))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tg_import_posted"
            " ON telegram_import_messages(source, source_chat_id, posted_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tg_import_deal"
            " ON telegram_import_messages(is_deal, posted_at)"
        )

    def existing_ids(self, source, chat_id):
        """Ids de mensagem já gravados para (source, chat_id).

        Abre o banco em modo somente-leitura e não cria schema: o dry-run não
        deve escrever nada. Se a tabela ainda não existe, devolve vazio.
        """
        if not os.path.exists(self.path):
            return set()
        uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=self.busy_timeout_ms / 1000)
        try:
            if not _table_exists(connection, "telegram_import_messages"):
                return set()
            rows = connection.execute(
                "SELECT message_id FROM telegram_import_messages"
                " WHERE source = ? AND source_chat_id = ?",
                (source, str(chat_id)),
            ).fetchall()
        finally:
            connection.close()
        return {row[0] for row in rows}

    def _ensure_origin(self, connection, source, chat_id, chat_type, now):
        """Devolve o `deals_chat_id` reservado da origem, alocando se novo."""
        row = connection.execute(
            "SELECT deals_chat_id FROM telegram_import_origins"
            " WHERE source = ? AND source_chat_id = ?",
            (source, str(chat_id)),
        ).fetchone()
        if row is not None:
            return row[0]
        used = connection.execute("SELECT COUNT(*) FROM telegram_import_origins").fetchone()[0]
        deals_chat_id = IMPORT_CHAT_ID_BASE - int(used)
        connection.execute(
            "INSERT INTO telegram_import_origins"
            " (source, source_chat_id, deals_chat_id, chat_type, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (source, str(chat_id), deals_chat_id, chat_type, now),
        )
        return deals_chat_id

    def apply(
        self,
        source,
        chat_id,
        chat_type,
        records,
        *,
        retention_days=90,
        imported_at=None,
        deal_store=None,
        history=None,
    ):
        """Grava checkpoint + feed numa transação; idempotente.

        Devolve `{"inserted", "fed", "pruned"}`. Reimportar não duplica:
        `deals` tem `UNIQUE(chat_id, message_id)` e o histórico tem dedupe por
        identidade/preço/tempo. O feed só ocorre para ofertas dentro da
        retenção (backfill de `retention_days`).
        """
        imported_at = time.time() if imported_at is None else imported_at
        cutoff = imported_at - retention_days * 86400 if retention_days > 0 else None
        inserted = 0
        fed = 0
        with closing(self._connect()) as connection, connection:
            self._ensure_schema(connection)
            origin_id = self._ensure_origin(connection, source, chat_id, chat_type, imported_at)
            for record in records:
                if record["is_deal"] and (cutoff is None or record["posted_at"] >= cutoff):
                    deal = _stored_deal(record)
                    if deal_store is not None and deal_store.add(
                        origin_id,
                        record["message_id"],
                        deal,
                        created_at=record["posted_at"],
                        connection=connection,
                    ):
                        fed += 1
                        if history is not None:
                            history.record(
                                deal, created_at=record["posted_at"], connection=connection
                            )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO telegram_import_messages"
                    " (source, source_chat_id, message_id, posted_at, is_deal, text, price,"
                    " old_price, discount, coupon, store, link, photo_path, imported_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source,
                        str(chat_id),
                        record["message_id"],
                        record["posted_at"],
                        record["is_deal"],
                        record["text"],
                        record["price"],
                        record["old_price"],
                        record["discount"],
                        record["coupon"],
                        record["store"],
                        record["link"],
                        record["photo_path"],
                        imported_at,
                    ),
                )
                inserted += cursor.rowcount > 0
            pruned = 0
            if cutoff is not None:
                cursor = connection.execute(
                    "DELETE FROM telegram_import_messages"
                    " WHERE source = ? AND source_chat_id = ? AND posted_at < ?",
                    (source, str(chat_id), cutoff),
                )
                pruned = cursor.rowcount
        return {"inserted": inserted, "fed": fed, "pruned": pruned}

    def rollback(self, source, chat_id):
        """Remove só as linhas desta origem, sem tocar em ofertas ao vivo.

        Apaga as ofertas importadas (pelo `deals_chat_id` reservado), o
        checkpoint e o mapeamento. Observações de preço **não** são apagadas:
        são agregados estatísticos sem vínculo de origem, e removê-las poderia
        afetar o histórico vivo.
        """
        if not os.path.exists(self.path):
            return {"messages": 0, "origins": 0, "deals": 0}
        with closing(self._connect()) as connection, connection:
            self._ensure_schema(connection)
            deals_removed = 0
            row = connection.execute(
                "SELECT deals_chat_id FROM telegram_import_origins"
                " WHERE source = ? AND source_chat_id = ?",
                (source, str(chat_id)),
            ).fetchone()
            if row is not None and _table_exists(connection, "deals"):
                deals_removed = connection.execute(
                    "DELETE FROM deals WHERE chat_id = ?", (row[0],)
                ).rowcount
            messages = connection.execute(
                "DELETE FROM telegram_import_messages WHERE source = ? AND source_chat_id = ?",
                (source, str(chat_id)),
            ).rowcount
            origins = connection.execute(
                "DELETE FROM telegram_import_origins WHERE source = ? AND source_chat_id = ?",
                (source, str(chat_id)),
            ).rowcount
        return {"messages": messages, "origins": origins, "deals": deals_removed}

    def backup(self, destination):
        """Cópia consistente via API de backup do SQLite (segura com WAL ativo).

        Não escreve na origem e recusa sobrescrever um backup existente.
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


def import_export(
    export_path,
    *,
    allowlist_path,
    source=DEFAULT_SOURCE,
    db_path=None,
    apply=False,
    retention_days=DEFAULT_RETENTION_DAYS,
    backup=True,
    imported_at=None,
):
    """Valida, projeta e (se `apply`) grava um export na allowlist.

    `apply=False` é o padrão: nada é escrito. Devolve um relatório com totais
    agregados que nunca inclui id/nome do chat, texto ou caminho de mídia.
    """
    imported_at = time.time() if imported_at is None else imported_at
    allowlist = load_allowlist(allowlist_path)
    export = parse_export(export_path)

    report = {
        "source": source,
        "chat_type": export["chat_type"],
        "chat_type_allowed": export["chat_type"] in ALLOWED_CHAT_TYPES,
        "allowlisted": False,
        "total_messages": len(export["messages"]),
        "skipped": 0,
        "importable": 0,
        "deals": 0,
        "non_deals": 0,
        "photos_present": 0,
        "photos_safe": 0,
        "photos_rejected": 0,
        "first_posted_at": None,
        "last_posted_at": None,
        "already_imported": 0,
        "would_insert": 0,
        "would_feed_deals": 0,
        "inserted": 0,
        "fed": 0,
        "would_prune": 0,
        "pruned": 0,
        "backup": None,
        "applied": False,
    }

    if not report["chat_type_allowed"]:
        raise ExportError("tipo de chat não permitido")
    if export["chat_id"] not in allowlist.get(source, set()):
        raise ExportError("chat fora da allowlist")
    report["allowlisted"] = True

    records, stats = _build_records(export["messages"])
    report["skipped"] = stats["skipped"]
    report["importable"] = len(records)
    report["deals"] = stats["deals"]
    report["non_deals"] = len(records) - stats["deals"]
    report["photos_present"] = stats["photos_present"]
    report["photos_safe"] = stats["photos_safe"]
    report["photos_rejected"] = stats["photos_rejected"]
    if records:
        report["first_posted_at"] = records[0]["posted_at"]
        report["last_posted_at"] = records[-1]["posted_at"]

    store = ImportStore(db_path) if db_path else None
    existing = store.existing_ids(source, export["chat_id"]) if store else set()
    report["already_imported"] = sum(1 for record in records if record["message_id"] in existing)
    cutoff = imported_at - retention_days * 86400 if retention_days > 0 else None
    # Fora da retenção nem entra (evita churn de checkpoint que seria podado).
    new_records = [
        record
        for record in records
        if record["message_id"] not in existing
        and (cutoff is None or record["posted_at"] >= cutoff)
    ]
    report["would_insert"] = len(new_records)
    report["would_prune"] = sum(
        1
        for record in records
        if record["message_id"] in existing
        and cutoff is not None
        and record["posted_at"] < cutoff
    )
    report["would_feed_deals"] = sum(1 for record in new_records if record["is_deal"])

    if not apply:
        return report
    if store is None:
        raise ExportError("--apply exige --db")
    if backup and os.path.exists(db_path):
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(imported_at))
        report["backup"] = store.backup(f"{db_path}.bak-{stamp}")
    deal_store = DealStore(db_path)
    history = PriceHistory(db_path, retention_days=retention_days)
    result = store.apply(
        source,
        export["chat_id"],
        export["chat_type"],
        new_records,
        retention_days=retention_days,
        imported_at=imported_at,
        deal_store=deal_store,
        history=history,
    )
    report["inserted"] = result["inserted"]
    report["fed"] = result["fed"]
    report["pruned"] = result["pruned"]
    report["applied"] = True
    return report


def rollback_export(export_path, *, allowlist_path, source=DEFAULT_SOURCE, db_path):
    """Rollback não destrutivo: apaga só as linhas do chat autorizado do export."""
    allowlist = load_allowlist(allowlist_path)
    export = parse_export(export_path)
    if export["chat_id"] not in allowlist.get(source, set()):
        raise ExportError("chat fora da allowlist")
    if not db_path:
        raise ExportError("rollback exige --db")
    return ImportStore(db_path).rollback(source, export["chat_id"])


def build_parser():
    parser = argparse.ArgumentParser(
        description="Importa offline UM chat do export JSON do Telegram Desktop (dry-run por padrão)."
    )
    parser.add_argument("export", help="caminho do result.json (cópia estável privada)")
    parser.add_argument(
        "--allowlist",
        default=os.environ.get("TELEGRAM_IMPORT_ALLOWLIST", DEFAULT_ALLOWLIST_NAME),
        help="arquivo privado de allowlist (não versionado)",
    )
    parser.add_argument("--db", default=os.environ.get("TELEGRAM_IMPORT_DB"))
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--apply", action="store_true", help="grava de fato (default: dry-run)")
    parser.add_argument("--no-backup", action="store_true", help="não faz backup antes de --apply")
    parser.add_argument("--rollback", action="store_true", help="remove as linhas deste chat")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.rollback:
        result = rollback_export(
            args.export, allowlist_path=args.allowlist, source=args.source, db_path=args.db
        )
        print(json.dumps({"rollback": result}, ensure_ascii=False, sort_keys=True))
        return 0
    report = import_export(
        args.export,
        allowlist_path=args.allowlist,
        source=args.source,
        db_path=args.db,
        apply=args.apply,
        retention_days=args.retention_days,
        backup=not args.no_backup,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
