"""Disposable inbox via mail.tm (hydra API)."""

import json
import random
import re
import string
import time
import urllib.error
import urllib.request

from . import config as C

_domains = []


def _call(path, data=None, method="GET", token=None, tries=4):
    last = None
    for attempt in range(tries):
        headers = {"User-Agent": C.USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            C.MAIL_BASE + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers, method=method)
        try:
            return json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        except urllib.error.HTTPError as err:
            last = err.code
            if err.code == 429:
                time.sleep(10 + attempt * 8)
                continue
            try:
                return json.loads(err.read().decode())
            except Exception:
                return {"__err": err.code}
        except Exception as err:
            last = str(err)[:60]
            time.sleep(3)
    return {"__err": last}


def _random_password():
    body = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(12))
    return "Frm" + body + "9"


def new_inbox():
    """Create a mailbox. Returns (email, token, password)."""
    global _domains
    if not _domains:
        listing = _call("/domains")
        _domains = [d["domain"] for d in listing.get("hydra:member", [])]
    if not _domains:
        raise RuntimeError("mail.tm returned no domains")

    address = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
    email = f"{address}@{random.choice(_domains)}"
    password = _random_password()

    _call("/accounts", {"address": email, "password": password}, "POST")
    token = _call("/token", {"address": email, "password": password}, "POST").get("token")
    if not token:
        raise RuntimeError("mail.tm login failed")
    return email, token, password


def wait_for_code(token, timeout=None):
    """Poll the inbox for a 6-digit verification code."""
    deadline = time.time() + (timeout or C.OTP_TIMEOUT)
    while time.time() < deadline:
        listing = _call("/messages", token=token)
        for message in listing.get("hydra:member", []):
            full = _call("/messages/" + message["id"], token=token)
            text = " ".join(full.get("text") or []) + " " + str(full.get("html") or "")
            found = re.findall(r"\b(\d{6})\b", text)
            if found:
                return found[0]
        time.sleep(4)
    return None
