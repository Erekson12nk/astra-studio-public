import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

SHIPPED = os.path.join(ROOT, "app_key.json")
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/auth"


def _pair_from_config(data):
    if not isinstance(data, dict):
        return "", ""
    block = data.get("installed") or data.get("web") or data
    return (block.get("client_id", "") or "").strip(), \
           (block.get("client_secret", "") or "").strip()


def _read(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def shipped_pair():
    client_id, client_secret = _pair_from_config(_read(SHIPPED))
    if client_id and client_secret:
        return client_id, client_secret
    return (os.environ.get("ASTRA_GOOGLE_CLIENT_ID", "").strip(),
            os.environ.get("ASTRA_GOOGLE_CLIENT_SECRET", "").strip())


def available():
    client_id, client_secret = shipped_pair()
    return bool(client_id and client_secret)


def client_config(client_id=None, client_secret=None):
    if client_id is None or client_secret is None:
        client_id, client_secret = shipped_pair()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Нет ключа приложения.\n\n"
            "В окне Google Cloud нажмите «Загрузить JSON» и положите файл рядом "
            "с программой под именем app_key.json."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "project_id": client_id.split("-", 1)[0],
            "auth_uri": AUTH_URL,
            "token_uri": TOKEN_URL,
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def install(path):
    if os.path.isfile(path) or not available():
        return False
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(client_config(), handle, ensure_ascii=False, indent=1)
    return True


def check(client_id=None, client_secret=None, timeout=30):
    if client_id is None or client_secret is None:
        client_id, client_secret = shipped_pair()
    if not client_id or not client_secret:
        return False, "ключ не найден: нет ни app_key.json, ни своего ключа"
    payload = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": "probe-not-a-real-code",
        "grant_type": "authorization_code",
        "redirect_uri": "http://localhost",
    }).encode("utf-8")
    try:
        urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=payload),
                               timeout=timeout)
        return True, "ключ принят"
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode("utf-8", "ignore"))
        except Exception:
            return False, "непонятный ответ Google (код %s)" % error.code
        kind = body.get("error", "")
        if kind == "invalid_grant":
            return True, "ключ рабочий"
        if kind == "invalid_client":
            return False, "ключ не подошёл: Google не знает такую пару id и секрета"
        return False, "%s: %s" % (kind, body.get("error_description", ""))
    except urllib.error.URLError as error:
        return False, "нет связи с Google: %s" % error.reason


if __name__ == "__main__":
    ok, message = check()
    print(message)
    sys.exit(0 if ok else 1)
