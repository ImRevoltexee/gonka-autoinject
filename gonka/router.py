"""9Router client: authenticate, ensure the provider node, register keys."""

import hashlib
import http.cookiejar
import json
import sqlite3
import time
import urllib.error
import urllib.request

from . import config as C


class Router:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def _api(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(C.ROUTER_BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = self.opener.open(req, timeout=timeout)
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
        except urllib.error.HTTPError as err:
            raw = err.read().decode()
            try:
                return err.code, json.loads(raw)
            except ValueError:
                return err.code, raw[:200]
        except Exception as err:
            return "ERR", {"err": str(err)[:100]}

    def login(self, retries=5):
        for attempt in range(retries):
            status, payload = self._api("POST", "/api/auth/login",
                                        {"password": C.ROUTER_PASSWORD})
            if isinstance(payload, dict) and payload.get("success"):
                return True
            time.sleep(3 + attempt * 3)
        return False

    def node_id(self):
        status, payload = self._api("GET", "/api/provider-nodes")
        if isinstance(payload, dict):
            for node in payload.get("nodes", []):
                if node.get("name", "").upper() == C.NODE_NAME.upper():
                    return node["id"]

        status, payload = self._api("POST", "/api/provider-nodes", {
            "name": C.NODE_NAME,
            "prefix": C.NODE_PREFIX,
            "apiType": "chat",
            "baseUrl": C.PROVIDER_BASEURL,
            "type": "openai-compatible",
        })
        if status not in (200, 201) or not isinstance(payload, dict):
            return None
        return (payload.get("node") or {}).get("id")

    def registered_keys(self, node_id):
        """Read existing connections straight from the router database (read-only)."""
        try:
            conn = sqlite3.connect(f"file:{C.ROUTER_DB}?mode=ro", uri=True, timeout=10)
            rows = conn.execute(
                "select data from providerConnections where provider=?", (node_id,)).fetchall()
            conn.close()
        except Exception:
            return set()
        keys = set()
        for (blob,) in rows:
            if not blob:
                continue
            try:
                key = json.loads(blob).get("apiKey")
            except ValueError:
                continue
            if key:
                keys.add(key)
        return keys

    def add_key(self, node_id, key):
        """Register one key. Returns (ok, reason)."""
        body = {
            "provider": node_id,
            "apiKey": key,
            "name": f"{C.NODE_PREFIX}-{hashlib.sha1(key.encode()).hexdigest()[:8]}",
            "priority": 1,
            "defaultModel": C.DEFAULT_MODEL,
            "providerSpecificData": {
                "prefix": C.NODE_PREFIX,
                "apiType": "chat",
                "baseUrl": C.PROVIDER_BASEURL,
                "nodeName": C.NODE_NAME,
            },
        }
        for attempt in range(3):
            status, payload = self._api("POST", "/api/providers", body)
            if status in (200, 201):
                return True, None
            message = str(payload.get("error") if isinstance(payload, dict) else payload).lower()
            if "conflict" in message or "already exists" in message:
                return True, "exists"
            if attempt == 2:
                return False, f"{status} {message[:80]}"
            time.sleep(2)
        return False, "unknown"


def read_key_file(path):
    """Deduplicated keys from a text file."""
    try:
        with open(path) as fh:
            return [line.strip() for line in fh if line.strip().startswith("sk-")]
    except FileNotFoundError:
        return []
