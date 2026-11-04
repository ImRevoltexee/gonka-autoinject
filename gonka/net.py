"""Proxy rotation and HTTP helpers for the GonkaRouter API."""

import json
import random
import threading
import urllib.error
import urllib.request

from . import config as C


class ProxyPool:
    """Round-robin proxy pool loaded from a text file (one host:port per line)."""

    def __init__(self, path=None):
        self._path = path or C.PROXY_FILE
        self._lock = threading.Lock()
        self._idx = 0
        self.items = self._load()

    def _load(self):
        try:
            with open(self._path) as fh:
                pool = [line.strip() for line in fh if line.strip()]
        except FileNotFoundError:
            pool = []
        random.shuffle(pool)
        return pool

    def reload(self):
        with self._lock:
            self.items = self._load()
            self._idx = 0
        return len(self.items)

    def next(self):
        with self._lock:
            if not self.items:
                return None
            proxy = self.items[self._idx % len(self.items)]
            self._idx += 1
            return proxy

    def __len__(self):
        return len(self.items)


def opener(proxy=None):
    if proxy:
        if "@" not in proxy and proxy.count(":") == 3:
            parts = proxy.split(":")
            proxy = f"{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
        handler = urllib.request.ProxyHandler({"http": f"http://{proxy}",
                                               "https": f"http://{proxy}"})
    else:
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


def check_proxy(proxy, timeout=10):
    """Return the egress IP if the proxy works, otherwise None."""
    try:
        url = "https://api.ipify.org"
        ip = opener(proxy).open(url, timeout=timeout).read().decode().strip()
        return ip if ip.count(".") == 3 else None
    except Exception:
        return None


def _decode(raw):
    raw = raw.decode(errors="replace")
    if raw.strip().startswith(("{", "[")):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw[:200]


def request(method, path, body=None, token=None, proxy=None, timeout=30, base=None):
    """Call the GonkaRouter API. Returns (status, parsed_body)."""
    headers = {
        "Content-Type": "application/json",
        "Origin": C.ORIGIN,
        "Referer": C.ORIGIN + "/",
        "User-Agent": C.USER_AGENT,
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request((base or C.API) + path, data=data,
                                 headers=headers, method=method)
    try:
        resp = opener(proxy).open(req, timeout=timeout)
        return resp.status, _decode(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, _decode(err.read())
    except Exception as err:  # network, TLS, timeout
        return "ERR", {"err": str(err)[:100]}


def post(path, body, **kw):
    return request("POST", path, body=body, **kw)


def get(path, token, **kw):
    return request("GET", path, token=token, **kw)
