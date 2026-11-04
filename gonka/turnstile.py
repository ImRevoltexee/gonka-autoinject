"""Client for a local Turnstile solver exposing POST /turnstile {"url","sitekey"}."""

import json
import time
import urllib.error
import urllib.request

from . import config as C

# Why the last solve() failed. Kept module-level so callers can surface a
# useful message instead of a bare "no token".
last_error = None


def solve(tries=5):
    """Return a fresh Turnstile token, or None.

    Tokens are single-use. Solve again for every submit attempt; replaying a
    token that was already consumed yields HTTP 403 challenge_failed.
    """
    global last_error
    last_error = None

    for attempt in range(tries):
        payload = {"url": C.ORIGIN + "/", "sitekey": C.SITEKEY}
        req = urllib.request.Request(
            C.SOLVER_URL + "/turnstile",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=C.SOLVER_TIMEOUT).read())
            if resp.get("token"):
                return resp["token"]
            last_error = "solver replied without a token: " + str(resp)[:120]
        except urllib.error.HTTPError as err:
            try:
                body = err.read().decode(errors="replace")[:120]
            except Exception:
                body = ""
            last_error = f"solver HTTP {err.code}: {body}"
        except Exception as err:
            last_error = f"{type(err).__name__}: {str(err)[:120]}"
        if attempt + 1 < tries:
            time.sleep(4 + attempt * 3)
    return None
