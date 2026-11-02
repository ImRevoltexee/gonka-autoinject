"""Single account flow: disposable inbox -> Turnstile -> send -> verify -> reveal key."""

import time

from . import config as C
from . import mail, net, turnstile


def reveal_key(jwt, proxy):
    """Read the account's first API key. Retries while the key is still provisioning."""
    for _ in range(8):
        status, payload = net.get("/api/keys", jwt, proxy=proxy)
        items = (payload or {}).get("items") or [] if isinstance(payload, dict) else []
        if items:
            key_id = items[0].get("id")
            status, revealed = net.post(f"/api/keys/{key_id}/reveal", {}, token=jwt, proxy=proxy)
            if status in (200, 201) and isinstance(revealed, dict) and revealed.get("key"):
                return revealed["key"]
        time.sleep(3)
    return None


def create_account(pool):
    """Run one registration. Returns a result dict; never raises."""
    result = {"ts": int(time.time()), "ok": False}

    email = inbox_token = password = None
    for attempt in range(3):
        try:
            email, inbox_token, password = mail.new_inbox()
            break
        except Exception as err:
            if attempt == 2:
                result["err"] = "tempmail: " + str(err)[:60]
                return result
            time.sleep(8)
    result["email"] = email
    result["password"] = password

    # Send the verification mail. Rotate proxies until one is accepted.
    used_proxy = None
    for _ in range(C.MAX_PROXY_ATTEMPTS):
        proxy = pool.next() if pool else None
        if pool and not proxy:
            break
        token = turnstile.solve()
        if not token:
            result["err"] = "no_turnstile"
            continue
        status, payload = net.post("/auth/email/send",
                                   {"email": email, "channel": C.CHANNEL,
                                    "turnstile_token": token},
                                   proxy=proxy)
        result["send_status"] = status
        if status in (200, 201):
            used_proxy = proxy
            break
        if status == 429 or (status == 400 and "limit" in str(payload).lower()):
            result["err"] = "signup_limit"
        elif status != "ERR":
            result["send_resp"] = str(payload)[:120]

    result["proxy"] = used_proxy
    if used_proxy is None and pool:
        result.setdefault("err", "send_failed")
        return result

    code = mail.wait_for_code(inbox_token)
    if not code:
        result["err"] = "no_otp"
        return result

    # Verify. Retry across proxies because slow exits time out mid-handshake.
    jwt = None
    for attempt in range(4):
        proxy = used_proxy if attempt == 0 else (pool.next() if pool else None)
        if pool and not proxy:
            break
        status, payload = net.post("/auth/email/verify",
                                   {"email": email, "channel": C.CHANNEL,
                                    "code": code, "source": "web"},
                                   proxy=proxy)
        result["verify_status"] = status
        if status in (200, 201) and isinstance(payload, dict) and payload.get("token"):
            jwt = payload["token"]
            used_proxy = proxy
            break
        if status in (200, 201):
            result["verify_resp"] = str(payload)[:120]
            break

    if not jwt:
        result["err"] = "verify_failed"
        return result

    result["proxy"] = used_proxy
    key = reveal_key(jwt, used_proxy)
    if key:
        result["first_key"] = key
        result["ok"] = True
    else:
        result["err"] = "no_key"
    return result
