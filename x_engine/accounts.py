"""Parse account records as data only. Never include record values in errors."""
import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


def handle(value: str) -> str:
    value = value.strip().removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,15}", value):
        raise ValueError("An X handle must contain 1–15 letters, numbers, or underscores.")
    return value


@dataclass(repr=False)
class Account:
    username: str
    password: str = field(repr=False)
    email: str = field(repr=False)
    cookies: dict = field(repr=False)
    totp_secret: str | None = field(default=None, repr=False)

    def secret_json(self) -> str:
        return json.dumps({"password": self.password, "email": self.email,
                           "cookies": self.cookies, "totp_secret": self.totp_secret})


def parse_record(line: str) -> Account:
    parts = line.strip().split(":", 6)
    if len(parts) == 6:
        username, password, email, _mail_password, auth, csrf = parts
        if "@" not in email or not re.fullmatch(r"[a-fA-F0-9]{40}", auth) or not re.fullmatch(r"[a-fA-F0-9]{32,256}", csrf):
            raise ValueError("Invalid six-field account record.")
        return Account(handle(username), password, email, {"auth_token": auth, "ct0": csrf})
    if len(parts) == 7:
        username, password, totp, email, _mail_password, auth, encoded = parts
        try:
            cookies = json.loads(base64.b64decode(encoded, validate=True))
            if not isinstance(cookies, list):
                raise ValueError()
            selected = {}
            for cookie in cookies:
                if cookie.get("domain", "").lstrip(".").lower() in {"x.com", "twitter.com"}:
                    name, value = cookie["name"], cookie["value"]
                    if not isinstance(name, str) or not isinstance(value, str):
                        raise ValueError()
                    selected[name] = value
            if not selected.get("ct0") or selected.get("auth_token") != auth:
                raise ValueError()
            if "@" not in email:
                raise ValueError()
        except Exception:
            raise ValueError("Invalid seven-field cookie record.") from None
        # Some exports contain a placeholder in the TOTP field. Cookies remain
        # usable; do not send an invalid secret to the password-login flow.
        valid_totp = totp if re.fullmatch(r"[A-Z2-7]{16,64}", totp.upper()) else None
        return Account(handle(username), password, email, selected, valid_totp)
    raise ValueError("Expected a six-field or seven-field account record.")


def import_files(store, paths: list[str]) -> dict:
    imported, invalid, duplicates = 0, [], 0
    for filename in paths:
        path = Path(filename)
        if path.name.lower() == 'x accounts 125.txt':
            raise ValueError('The 125-account file is excluded because its accounts are suspended.')
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                account = parse_record(line)
            except ValueError:
                invalid.append({"file": path.name, "line": number})
                continue
            if store.add_account(account, path.name):
                imported += 1
            else:
                duplicates += 1
    return {"imported": imported, "duplicates_skipped": duplicates, "invalid_records": invalid}


def refresh_session(store, username, filename):
    try:
        raw = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
        if isinstance(raw, list):
            cookies = {c["name"]: c["value"] for c in raw
                       if c.get("domain", "").lstrip(".").lower() in {"x.com", "twitter.com"}}
        elif isinstance(raw, dict):
            cookies = raw
        else:
            raise ValueError()
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in cookies.items()):
            raise ValueError()
        if not cookies.get("auth_token") or not cookies.get("ct0"):
            raise ValueError()
    except Exception:
        raise ValueError("Cookie file must be a JSON dictionary or browser cookie list with auth_token and ct0.") from None
    secret = store.credentials(username)
    secret["cookies"] = cookies
    store.save_credentials(username, secret)
    # Refreshing credentials never clears a rate-limit cooldown.
    with store.db:
        store.db.execute("UPDATE accounts SET status='unverified',reason=NULL,verified_at=NULL WHERE username=?", (username,))
        store.db.execute("UPDATE targets SET next_run=0 WHERE account=?", (username,))
    return {"account": username, "status": "unverified", "message": "Session replaced; verify before use."}
