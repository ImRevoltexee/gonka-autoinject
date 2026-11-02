"""Client for a local Turnstile solver exposing POST /turnstile {"url","sitekey"}."""

import json
import time
import urllib.request

from . import config as C


def solve(tries=5):
    """Return a fresh Turnstile token, or None.

    Tokens are single-use. Solve again for every submit attempt; replaying a
    token that was already consumed yields HTTP 403 challenge_failed.
    """
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
        except Exception:
            pass
        time.sleep(4 + attempt * 3)
    return None
