"""
Reestr akkauntov i hranilishche tokenov.

Razdelenie otvetstvennosti:
- data/accounts.json  — SOSTOYANIE i DOSTUPY (beз tokenov, mozhno v git):
                        rezhim dostupa, loginy, ID schetchikov/kontеjnerov, celi.
- secrets/secrets.json — TOKENY (v .gitignore, v git NE popadaet).

Bez shifrovaniya: dlya 3-5 klientov etogo dostatochno, a pereezd na
shifrovanie ne potrebuet menyat' ostatnoj kod — menyayutsya tol'ko
_read_secrets / _write_secrets.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("accounts")

BASE_DIR = Path(__file__).resolve().parent
ACCOUNTS_FILE = BASE_DIR / "data" / "accounts.json"
SECRETS_FILE = BASE_DIR / "secrets" / "secrets.json"

DEFAULT_ACCOUNT = os.getenv("DEFAULT_ACCOUNT", "default")


# --------------------------------------------------------------------------
# nizkourovnevoe chtenie/zapis'
# --------------------------------------------------------------------------

def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Не читается %s: %s", path, e)
        return default


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _read_secrets() -> dict:
    return _read_json(SECRETS_FILE, {})


def _write_secrets(data: dict):
    _write_json(SECRETS_FILE, data)


def _read_accounts() -> dict:
    return _read_json(ACCOUNTS_FILE, {})


def _write_accounts(data: dict):
    _write_json(ACCOUNTS_FILE, data)


# --------------------------------------------------------------------------
# bootstrap: prezhnie .env stanovitsya akkauntom "default"
# --------------------------------------------------------------------------

def ensure_bootstrap():
    """
    Esli reestra net — sozdaem akkaunt default iz .env.

    Obratnaya sovmestimost': vse suschestvuyuschie vyzovy prodolzhayut rabotat',
    potomu chto oni ushli by v etot zhe akkaunt.
    """
    accounts = _read_accounts()
    if accounts:
        return accounts

    secrets = _read_secrets()
    token_map = {
        "direct": os.getenv("YANDEX_DIRECT_TOKEN"),
        "metrica": os.getenv("YANDEX_METRICA_TOKEN"),
        "ytm": os.getenv("YANDEX_YTM_TOKEN"),
    }

    accounts = {
        "default": {
            "title": os.getenv("DEFAULT_ACCOUNT_TITLE", "Аккаунт по умолчанию (.env)"),
            "direct": {
                "mode": "token",
                "token_ref": "default",
                "client_login": os.getenv("DEFAULT_DIRECT_LOGIN") or None,
            },
            "metrica": {"counter_ids": [], "ulogin": None, "token_ref": "default"},
            "ytm": {"container_ids": [1007795], "token_ref": "default"},
            "goals": {},
        }
    }
    secrets["default"] = {k: v for k, v in token_map.items() if v}

    _write_accounts(accounts)
    _write_secrets(secrets)
    log.info("Создан реестр аккаунтов: аккаунт 'default' из .env")
    return accounts


# --------------------------------------------------------------------------
# publichnyj dostup
# --------------------------------------------------------------------------

def all_accounts() -> dict:
    return ensure_bootstrap()


def account_ids() -> list:
    return sorted(ensure_bootstrap().keys())


def get(name: str | None) -> dict:
    """Vozvrashchaet zapis' akkaunta po imeni. Brosaet KeyError."""
    acc_name = name or DEFAULT_ACCOUNT
    accounts = ensure_bootstrap()
    if acc_name not in accounts:
        raise KeyError(acc_name)
    return accounts[acc_name]


def get_secret(name: str, account: dict, contour: str) -> str | None:
    """
    Token dlya kontura.

    Poryadok poiska:
      1. token_ref iz zapisi kontura (odin token na neskol'ko akkauntov);
      2. imya samogo akkaunta v secrets.json (obychnyj sluchaj);
      3. po client_login — dlya agentskih zapisej.
    """
    ref = (account.get(contour) or {}).get("token_ref") or name or _ref_by_login(account)
    if not ref:
        return None
    secrets = _read_secrets()
    token = (secrets.get(ref) or {}).get(contour)
    if token:
        return token
    # Poslednij shans: ischem po lyubomu sovpadeniyu s imenem akkaunta
    for key, bucket in secrets.items():
        if key.lower() == str(ref).lower():
            return bucket.get(contour)
    return None


def _ref_by_login(account: dict) -> str | None:
    """Fallback: ischem ref po client_login (udobno dlya agentskih zapisej)."""
    login = (account.get("direct") or {}).get("client_login")
    if not login:
        return None
    for ref, tok in _read_secrets().items():
        if ref.lower() == str(login).lower().replace("-", ""):
            return ref
    return None


def agency_token() -> str | None:
    """Obschij agentskij token (odin na vseh klientov) iz .env ili secrets."""
    tok = os.getenv("YANDEX_AGENCY_TOKEN")
    if tok:
        return tok
    return (_read_secrets().get("agency") or {}).get("direct")


# --------------------------------------------------------------------------
# razreshenie lyubogo znakomogo ID v imya akkaunta
# --------------------------------------------------------------------------

def resolve(identifier) -> str | None:
    """
    Prinimaet imya akkaunta ILI lyuboj znakomyj ID:
      - imya akkaunta ("printyard")
      - ID schetchika Metriki (106104483)
      - ID kontejnera YTM (1007795)
      - client_login / client_id Direct

    Vozvrashchaet kanonicheskoe imya akkaunta ili None.
    """
    if identifier is None:
        return None
    accounts = ensure_bootstrap()

    if isinstance(identifier, str) and identifier in accounts:
        return identifier

    key = str(identifier).strip()
    if key in accounts:
        return key

    for name, acc in accounts.items():
        direct = acc.get("direct") or {}
        metrica = acc.get("metrica") or {}
        ytm = acc.get("ytm") or {}

        if key in [str(c) for c in metrica.get("counter_ids", [])]:
            return name
        if key in [str(c) for c in ytm.get("container_ids", [])]:
            return name
        if key in [str(c) for c in direct.get("campaign_ids", [])]:
            return name
        if key and key.lower() in {
            str(direct.get("client_login") or "").lower(),
            str(direct.get("client_id") or "").lower(),
        }:
            return name
    return None


def resolve_or_default(identifier) -> str:
    """Kak resolve, no s otkatom na DEFAULT_ACCOUNT."""
    return resolve(identifier) or DEFAULT_ACCOUNT


# --------------------------------------------------------------------------
# izmenenie reestra
# --------------------------------------------------------------------------

def upsert_account(name: str, record: dict, tokens: dict | None = None):
    """Sozdaet ili obnovlyaet akkaunt. tokens = {'direct': ..., 'metrica': ...}."""
    accounts = ensure_bootstrap()
    accounts[name] = record
    _write_accounts(accounts)

    if tokens:
        secrets = _read_secrets()
        bucket = secrets.setdefault(name, {})
        for contour, token in tokens.items():
            if token:
                bucket[contour] = token.strip()
        _write_secrets(secrets)
    return accounts[name]


def link_counters(name: str, counter_ids, ulogin: str | None = None):
    accounts = ensure_bootstrap()
    acc = accounts[name]
    acc.setdefault("metrica", {})["counter_ids"] = sorted(
        {int(c) for c in (acc.get("metrica", {}).get("counter_ids") or [])} | {int(c) for c in counter_ids}
    )
    if ulogin:
        acc["metrica"]["ulogin"] = ulogin
    _write_accounts(accounts)
    return acc


def link_containers(name: str, container_ids):
    accounts = ensure_bootstrap()
    acc = accounts[name]
    acc.setdefault("ytm", {})["container_ids"] = sorted(
        {int(c) for c in (acc.get("ytm", {}).get("container_ids") or [])} | {int(c) for c in container_ids}
    )
    _write_accounts(accounts)
    return acc


def set_tokens(name: str, tokens: dict):
    secrets = _read_secrets()
    bucket = secrets.setdefault(name, {})
    for contour, token in tokens.items():
        if token:
            bucket[contour] = token.strip()
    _write_secrets(secrets)
    return {k: bool(v) for k, v in bucket.items()}


def mask(token: str | None) -> str:
    """Maskirovka tokena dlya lyubogo vyvoda: y0__...f3a2. Token NE logiruem."""
    if not token:
        return "—"
    if len(token) <= 10:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"
