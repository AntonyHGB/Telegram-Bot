"""Coletor MTProto mínimo (user API) para o único canal público aprovado.

Escopo
------
- Resolve **apenas** a entidade autorizada na allowlist privada (por username)
  e nunca itera diálogos/contatos da conta.
- Limita a coleta à janela de retenção (≤ 90 dias), com cursor incremental
  (`min_id`) e dedupe contra o checkpoint do export.
- Trata `FloodWait`/429 com backoff; aborta se o tempo de espera for grande.
- Nunca baixa mídia: no máximo guarda um caminho de metadado opcional.
- Reutiliza o mesmo parsing/minimização e o mesmo checkpoint do importador
  offline (`telegram_export_import`), então nada de mensagem bruta é gravado.
- Não envia nada ao Telegram nem ao Zen.

Identidade e idempotência
-------------------------
`source='tdesktop_export'` só é reutilizado se a identidade do peer for
**comprovadamente igual** à esperada (`expected_peer_id` da allowlist). Se não
for, usa-se o namespace separado `source='mtproto'` com o id do peer, sem
presumir que os ids de mensagem coincidem com o export.

Sessão e segredos
-----------------
`api_id`/`api_hash` vêm do ambiente (`TELEGRAM_API_ID`/`TELEGRAM_API_HASH`);
nunca de token de bot, caches, `tdata` ou arquivos de auth. O arquivo de sessão
do Telethon concede **acesso total à conta** e nunca deve ser versionado nem
compartilhado. Login é sempre interativo e feito pelo usuário.

Telethon é importado de forma preguiçosa: o módulo não exige a dependência só
para ser importado (testes usam um client fake, sem rede).
"""

import argparse
import json
import logging
import os
import sys
import time

from deals import DealStore
from price_history import PriceHistory
from telegram_export_import import (
    DEFAULT_SOURCE,
    ImportStore,
    build_record,
)

logger = logging.getLogger(__name__)

DEFAULT_ALLOWLIST_NAME = ".telegram_import_allowlist.json"
DEFAULT_SESSION_PATH = os.path.expanduser(
    "~/.local/share/telegram-bot-import/collector.session"
)
MAX_WINDOW_DAYS = 90
DEFAULT_LIMIT = 20000
DEFAULT_MAX_FLOOD_WAIT = 600
DEFAULT_MAX_RETRIES = 3
MT_PROTO_SOURCE = "mtproto"


class CollectorError(Exception):
    """Erro de configuração/uso do coletor (sem expor segredos)."""


def load_mtproto_config(path):
    """Lê a seção `mtproto` da allowlist privada (sem expor valores)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    config = data.get("mtproto") if isinstance(data, dict) else None
    if not isinstance(config, dict):
        raise CollectorError("allowlist sem seção 'mtproto'")
    return config


def load_credentials(environ=None):
    """Lê api_id/api_hash do ambiente; nunca loga os valores."""
    environ = os.environ if environ is None else environ
    api_id = environ.get("TELEGRAM_API_ID")
    api_hash = environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise CollectorError(
            "defina TELEGRAM_API_ID e TELEGRAM_API_HASH (veja COLLECTOR.md)"
        )
    try:
        api_id = int(api_id)
    except (TypeError, ValueError):
        raise CollectorError("TELEGRAM_API_ID inválido") from None
    return api_id, api_hash


def import_telethon():
    """Importa o Telethon só quando necessário (evita dependência em testes)."""
    try:
        import telethon
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise CollectorError(
            "Telethon não instalado; veja requirements-collector.txt"
        ) from exc
    return telethon


def create_client(session_path, api_id, api_hash):
    """Cria o cliente Telethon (sem conectar/logar)."""
    telethon = import_telethon()
    return telethon.TelegramClient(session_path, api_id, api_hash)


def secure_session(session_path):
    """Garante permissão 600 no arquivo de sessão, se ele existir (best-effort)."""
    if session_path and os.path.exists(session_path):
        try:
            os.chmod(session_path, 0o600)
        except OSError:  # pragma: no cover - FS sem permissão
            pass


def is_group_like(entity):
    """True só para canal/supergrupo/grupo; False para usuário/bot."""
    name = type(entity).__name__
    if name in ("User", "UserEmpty"):
        return False
    return bool(
        getattr(entity, "broadcast", False)
        or getattr(entity, "megagroup", False)
        or name in ("Channel", "Chat", "ChannelForbidden")
    )


def _peer_id_forms(peer_id):
    """Formas equivalentes de id de peer (canal puro e formato -100…).

    O export do Telegram Desktop costuma guardar o id puro do canal, enquanto o
    Bot API/Telethon pode expor `-100…`. Aceitamos as duas formas sem presumir.
    """
    forms = {str(peer_id)}
    try:
        value = int(peer_id)
    except (TypeError, ValueError):
        return forms
    if value > 0:
        forms.add(str(-1_000_000_000_000 - value))
    elif value < 0 and str(value).startswith("-100"):
        forms.add(str(-1_000_000_000_000 - value))
    return forms


def check_identity(entity, config):
    """Compara a identidade do peer com o `expected_peer_id` da allowlist.

    Devolve apenas booleanos/estrutura interna; nunca loga o id.
    """
    peer_id = getattr(entity, "id", None)
    expected = config.get("expected_peer_id")
    matches = peer_id is not None and expected is not None and str(expected) in _peer_id_forms(peer_id)
    return {
        "matches_expected": bool(matches),
        "is_group_like": is_group_like(entity),
        "peer_key": str(peer_id) if peer_id is not None else None,
        "expected_key": str(expected) if expected is not None else None,
    }


def flood_wait_seconds(exc):
    """Segundos de espera se a exceção for FloodWait/429, senão None."""
    seconds = getattr(exc, "seconds", None)
    if seconds is None:
        return None
    try:
        return float(seconds)
    except (TypeError, ValueError):
        return None


def collect_messages(
    client,
    entity,
    *,
    min_id,
    since,
    limit,
    page_size=100,
    sleep=time.sleep,
    max_flood_wait=DEFAULT_MAX_FLOOD_WAIT,
    max_retries=DEFAULT_MAX_RETRIES,
):
    """Itera mensagens do peer autorizado, com cursor, paginação e backoff.

    Nunca itera diálogos: só a entidade recebida. Pagina do mais novo para o
    mais antigo (`offset_id`) até sair da janela (`since`) ou esvaziar. Se o
    `limit` for atingido antes de cobrir a janela, **aborta** (não cria buraco
    no cursor incremental). Para em `FloodWait`/429 com backoff.
    """
    seen = {}
    offset_id = 0
    attempts = 0
    truncated = False
    while len(seen) < limit:
        try:
            batch = list(
                client.iter_messages(entity, limit=page_size, min_id=min_id, offset_id=offset_id)
            )
        except Exception as exc:  # noqa: BLE001 - reavaliado por flood_wait_seconds
            wait = flood_wait_seconds(exc)
            if wait is None or attempts >= max_retries or wait > max_flood_wait:
                raise
            attempts += 1
            logger.warning("FloodWait de %ss; aguardando (tentativa %d).", int(wait), attempts)
            sleep(wait)
            continue
        if not batch:
            break
        stop = False
        for message in batch:
            if message.date.timestamp() < since:
                stop = True
                break
            seen[message.id] = message
            if len(seen) >= limit:
                truncated = True
                stop = True
                break
        if stop:
            break
        offset_id = batch[-1].id
        if len(batch) < page_size:
            break
    if truncated:
        raise CollectorError(
            f"limite de {limit} mensagens atingido antes de cobrir a janela; aumente --limit"
        )
    return sorted(seen.values(), key=lambda message: (message.date.timestamp(), message.id))


def message_to_record(message, media_path=None):
    """Converte uma mensagem MTProto no registro minimizado padrão.

    Não acessa `message.media` nem baixa nada; `media_path` é metadado opcional.
    """
    text = getattr(message, "message", None)
    if not isinstance(text, str) or not text:
        text = getattr(message, "text", None)
    if not isinstance(text, str):
        text = None
    return build_record(message.id, message.date.timestamp(), text, photo=media_path)


def collect_once(
    client,
    *,
    allowlist_path,
    db_path,
    source=DEFAULT_SOURCE,
    window_days=MAX_WINDOW_DAYS,
    limit=DEFAULT_LIMIT,
    dry_run=True,
    now=None,
    sleep=time.sleep,
    max_flood_wait=DEFAULT_MAX_FLOOD_WAIT,
    max_retries=DEFAULT_MAX_RETRIES,
):
    """Preflight + coleta de uma passada. `dry_run=True` não escreve nada.

    Devolve um relatório só com totais/booleanos (sem id, username ou texto).
    """
    if window_days < 1 or window_days > MAX_WINDOW_DAYS:
        raise CollectorError(f"janela deve estar entre 1 e {MAX_WINDOW_DAYS} dias")
    now = time.time() if now is None else now
    config = load_mtproto_config(allowlist_path)
    username = (config.get("username") or "").strip()
    if not username:
        raise CollectorError("username do canal autorizado não configurado na allowlist")

    entity = client.get_entity(username)
    identity = check_identity(entity, config)
    if not identity["is_group_like"]:
        raise CollectorError("entidade autorizada não é canal/grupo")

    reuse = identity["matches_expected"] and config.get("reuse_source") == source
    effective_source = source if reuse else MT_PROTO_SOURCE
    source_chat_id = identity["expected_key"] if reuse else identity["peer_key"]
    if not source_chat_id:
        raise CollectorError("não foi possível resolver a identidade do peer")

    store = ImportStore(db_path)
    existing = store.existing_ids(effective_source, source_chat_id)
    min_id = max(existing) if existing else 0
    since = now - window_days * 86400

    messages = collect_messages(
        client,
        entity,
        min_id=min_id,
        since=since,
        limit=limit,
        sleep=sleep,
        max_flood_wait=max_flood_wait,
        max_retries=max_retries,
    )
    records = []
    skipped = 0
    for message in messages:
        try:
            records.append(message_to_record(message))
        except (AttributeError, TypeError, ValueError, OverflowError):
            skipped += 1
    new_records = [record for record in records if record["message_id"] not in existing]

    report = {
        "identity_verified": bool(reuse),
        "reused_export_source": bool(reuse),
        "is_group_like": identity["is_group_like"],
        "window_days": window_days,
        "fetched": len(records),
        "skipped": skipped,
        "new": len(new_records),
        "deals": sum(1 for record in new_records if record["is_deal"]),
        "cursor_from_checkpoint": min_id > 0,
        "dry_run": dry_run,
        "applied": False,
    }
    logger.info(
        "coleta: fetched=%d new=%d deals=%d reuse_export=%s dry_run=%s",
        report["fetched"],
        report["new"],
        report["deals"],
        report["reused_export_source"],
        dry_run,
    )

    if dry_run:
        return report
    if not db_path:
        raise CollectorError("--apply exige --db")
    deal_store = DealStore(db_path)
    history = PriceHistory(db_path, retention_days=window_days)
    result = store.apply(
        effective_source,
        source_chat_id,
        config.get("chat_type", "public_channel"),
        new_records,
        retention_days=window_days,
        imported_at=now,
        deal_store=deal_store,
        history=history,
    )
    report["inserted"] = result["inserted"]
    report["fed"] = result["fed"]
    report["applied"] = True
    return report


def preflight(client, *, allowlist_path):
    """Resolve a entidade autorizada e devolve só booleanos (sem id/username)."""
    config = load_mtproto_config(allowlist_path)
    username = (config.get("username") or "").strip()
    if not username:
        raise CollectorError("username do canal autorizado não configurado na allowlist")
    entity = client.get_entity(username)
    identity = check_identity(entity, config)
    return {
        "identity_verified": identity["matches_expected"],
        "is_group_like": identity["is_group_like"],
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description="Coletor MTProto (user API) do canal autorizado — dry-run por padrão."
    )
    parser.add_argument(
        "--allowlist",
        default=os.environ.get("TELEGRAM_IMPORT_ALLOWLIST", DEFAULT_ALLOWLIST_NAME),
    )
    parser.add_argument("--db", default=os.environ.get("TELEGRAM_IMPORT_DB"))
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--window-days", type=int, default=MAX_WINDOW_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--session",
        default=os.environ.get("TELEGRAM_COLLECTOR_SESSION", DEFAULT_SESSION_PATH),
    )
    parser.add_argument("--login", action="store_true", help="login interativo (usuário)")
    parser.add_argument("--preflight", action="store_true", help="resolve a entidade autorizada")
    parser.add_argument("--apply", action="store_true", help="grava de fato (default: dry-run)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.login:
            api_id, api_hash = load_credentials()
            client = create_client(args.session, api_id, api_hash)
            client.start()  # interativo: o usuário digita telefone/código
            secure_session(args.session)
            print(json.dumps({"login": "ok"}, sort_keys=True))
            return 0
        if args.preflight:
            api_id, api_hash = load_credentials()
            client = create_client(args.session, api_id, api_hash)
            secure_session(args.session)
            print(json.dumps(preflight(client, allowlist_path=args.allowlist), sort_keys=True))
            return 0
        if not args.apply:
            # dry-run sem rede: valida allowlist/janela sem exigir credenciais
            config = load_mtproto_config(args.allowlist)
            if args.window_days < 1 or args.window_days > MAX_WINDOW_DAYS:
                raise CollectorError(f"janela deve estar entre 1 e {MAX_WINDOW_DAYS} dias")
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "username_configured": bool((config.get("username") or "").strip()),
                        "window_days": args.window_days,
                        "limit": args.limit,
                        "note": "use --preflight (após login) para verificar a identidade",
                    },
                    sort_keys=True,
                )
            )
            return 0
        api_id, api_hash = load_credentials()
        client = create_client(args.session, api_id, api_hash)
        secure_session(args.session)
        report = collect_once(
            client,
            allowlist_path=args.allowlist,
            db_path=args.db,
            source=args.source,
            window_days=args.window_days,
            limit=args.limit,
            dry_run=False,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    except CollectorError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
