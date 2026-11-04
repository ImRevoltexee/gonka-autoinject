"""Single account flow: disposable inbox -> Turnstile -> send -> verify -> reveal key."""

import time

from . import config as C
from . import mail, net, turnstile


def _emit(on_stage, message):
    """Send progress text to the caller's logger, if any; never fail the flow."""
    if on_stage is None:
        return
    try:
        on_stage(message)
    except Exception:
        pass


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


def create_account(pool, on_stage=None):
    """Run one registration. Returns a result dict; never raises.

    on_stage, when provided, receives short human-readable progress messages.
    """
    result = {"ts": int(time.time()), "ok": False}

    email = inbox_token = password = None
    for attempt in range(3):
        try:
            email, inbox_token, password = mail.new_inbox()
            _emit(on_stage, f"inbox ready: {email}")
            break
        except Exception as err:
            if attempt == 2:
                result["err"] = "tempmail: " + str(err)[:60]
                return result
            _emit(on_stage, f"inbox attempt {attempt + 1}/3 failed "
                            f"({str(err)[:60]}); retrying in 8s")
            time.sleep(8)
    result["email"] = email
    result["password"] = password

    # Send the verification mail. Rotate proxies until one is accepted.
    used_proxy = None
    last_send_err = "send_failed"
    for attempt in range(C.MAX_PROXY_ATTEMPTS):
        proxy = pool.next() if pool else None
        if pool and not proxy:
            _emit(on_stage, "no proxy available for send")
            break
        _emit(on_stage, f"send attempt {attempt + 1}/{C.MAX_PROXY_ATTEMPTS} "
                        f"via {proxy or 'direct'}; solving Turnstile")
        token = turnstile.solve()
        if not token:
            last_send_err = "no_turnstile"
            result["err"] = last_send_err
            _emit(on_stage, f"Turnstile solver returned no token ({turnstile.last_error})")
            continue
        status, payload = net.post("/auth/email/send",
                                   {"email": email, "channel": C.CHANNEL,
                                    "turnstile_token": token},
                                   proxy=proxy)
        result["send_status"] = status
        if status in (200, 201):
            used_proxy = proxy
            _emit(on_stage, f"verification email accepted (HTTP {status})")
            break
        if status == 429 or (status == 400 and "limit" in str(payload).lower()):
            last_send_err = "signup_limit"
            result["err"] = last_send_err
            _emit(on_stage, f"send rejected: rate limited (HTTP {status}); rotating proxy")
        elif status == 403 and "challenge_failed" in str(payload):
            last_send_err = "challenge_failed"
            result["err"] = last_send_err
            _emit(on_stage, "send rejected: Turnstile token was refused (HTTP 403); "
                            "the solver's token may be stale or IP-bound")
        elif status == "ERR":
            last_send_err = "send_failed"
            reason = payload.get("err") if isinstance(payload, dict) else payload
            _emit(on_stage, f"send failed: {str(reason)[:80]}")
        else:
            last_send_err = "send_failed"
            result["send_resp"] = str(payload)[:120]
            _emit(on_stage, f"send rejected (HTTP {status}): {str(payload)[:80]}")

    result["proxy"] = used_proxy
    if used_proxy is None and pool:
        result["err"] = last_send_err
        _emit(on_stage, "all send attempts failed; abandoning this account")
        return result

    _emit(on_stage, f"waiting for OTP email (timeout {C.OTP_TIMEOUT}s)")
    code = mail.wait_for_code(inbox_token)
    if not code:
        result["err"] = "no_otp"
        _emit(on_stage, "OTP not received before the timeout")
        return result
    _emit(on_stage, "OTP received")

    # Verify. Retry across proxies because slow exits time out mid-handshake.
    jwt = None
    for attempt in range(4):
        proxy = used_proxy if attempt == 0 else (pool.next() if pool else None)
        if pool and not proxy:
            _emit(on_stage, "no proxy available for verify")
            break
        _emit(on_stage, f"verify attempt {attempt + 1}/4 via {proxy or 'direct'}")
        status, payload = net.post("/auth/email/verify",
                                   {"email": email, "channel": C.CHANNEL,
                                    "code": code, "source": "web"},
                                   proxy=proxy)
        result["verify_status"] = status
        if status in (200, 201) and isinstance(payload, dict) and payload.get("token"):
            jwt = payload["token"]
            used_proxy = proxy
            _emit(on_stage, "email verified; token acquired")
            break
        if status in (200, 201):
            result["verify_resp"] = str(payload)[:120]
            _emit(on_stage, f"verify returned HTTP {status} without a token")
            break
        if status == "ERR":
            reason = payload.get("err") if isinstance(payload, dict) else payload
            _emit(on_stage, f"verify failed: {str(reason)[:80]}")
        else:
            _emit(on_stage, f"verify rejected (HTTP {status}): {str(payload)[:80]}")

    if not jwt:
        result["err"] = "verify_failed"
        return result

    result["proxy"] = used_proxy
    _emit(on_stage, "fetching first API key")
    key = reveal_key(jwt, used_proxy)
    if key:
        result["first_key"] = key
        result["ok"] = True
    else:
        result["err"] = "no_key"
    return result
