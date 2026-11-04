"""9Router client: authenticate, ensure the provider node, register keys."""

import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.request

from . import config as C
from . import net


class Router:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.cli_token = self._resolve_cli_token()
        self._priority_lock = threading.Lock()

    def _resolve_cli_token(self):
        explicit = os.environ.get("ROUTER_CLI_TOKEN")
        if explicit:
            return explicit.strip()

        db_path = Path(C.ROUTER_DB)
        candidates = [
            db_path.parent.parent,
            Path.home() / ".9router",
            Path("/root/.9router"),
        ]
        for base in candidates:
            machine_file = base / "machine-id"
            secret_file = base / "auth" / "cli-secret"
            if machine_file.is_file() and secret_file.is_file():
                try:
                    raw = machine_file.read_text().strip()
                    secret = secret_file.read_text().strip()
                    if raw and secret:
                        return hashlib.sha256(f"{raw}9r-cli-auth{secret}".encode()).hexdigest()[:16]
                except Exception:
                    pass
        return None

    def _api(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.cli_token:
            headers["x-9r-cli-token"] = self.cli_token
        req = urllib.request.Request(C.ROUTER_BASE + path, data=data, method=method,
                                     headers=headers)
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
        if self.cli_token:
            status, payload = self._api("GET", "/api/provider-nodes")
            if status == 200 and isinstance(payload, dict):
                return True
        if not C.ROUTER_PASSWORD:
            return False
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

    def connections(self, node_id):
        """Connections on the node, straight from the router DB (read-only).

        Returns a list of dicts ordered by priority (then creation time), each
        with ``id``, ``name``, ``priority`` and ``key``.
        """
        try:
            conn = sqlite3.connect(f"file:{C.ROUTER_DB}?mode=ro", uri=True, timeout=10)
            rows = conn.execute(
                "select id, name, priority, data from providerConnections "
                "where provider=? order by priority asc, createdAt asc, id asc",
                (node_id,)).fetchall()
            conn.close()
        except Exception:
            return []
        out = []
        for connection_id, name, priority, blob in rows:
            if not blob:
                continue
            try:
                key = json.loads(blob).get("apiKey")
            except ValueError:
                continue
            if key:
                out.append({"id": connection_id, "name": name or connection_id,
                            "priority": priority, "key": key})
        return out

    def registered_keys(self, node_id):
        """Keys already registered on the node."""
        return {item["key"] for item in self.connections(node_id)}

    def validate_key(self, key, timeout=20):
        """Ask the upstream /models endpoint about a key.

        Returns one of the 9Router testStatus values: ``active`` (key works),
        ``error`` (key rejected) or ``unknown`` (network/upstream problem).
        """
        status, _ = net.get("/v1/models", key, timeout=timeout)
        if status == 200:
            return "active"
        if status in (401, 403):
            return "error"
        return "unknown"

    def set_status(self, connection_id, status):
        """Update only the testStatus of an existing connection."""
        code, _ = self._api("PUT", f"/api/providers/{connection_id}", {"testStatus": status})
        return code == 200

    def renumber_priorities(self, node_id):
        """Give every connection a unique 1..N priority, oldest first.

        9Router renumbers the whole node after every priority update and breaks
        ties by ``updatedAt`` descending. Walking the list newest-first therefore
        leaves the oldest connection at priority 1. Returns (total, updated).
        """
        rows = self.connections(node_id)
        updated = 0
        for item in reversed(rows):
            code, _ = self._api("PUT", f"/api/providers/{item['id']}", {"priority": 1})
            if code == 200:
                updated += 1
        return len(rows), updated

    def next_priority(self, node_id):
        """Next free priority for the node (9Router stores 1 = highest)."""
        try:
            conn = sqlite3.connect(f"file:{C.ROUTER_DB}?mode=ro", uri=True, timeout=10)
            row = conn.execute(
                "select max(priority) from providerConnections where provider=?",
                (node_id,)).fetchone()
            conn.close()
            current = row[0] if row else None
        except Exception:
            current = None
        return (current or 0) + 1

    def add_key(self, node_id, key, validate=True):
        """Register one key. Returns (ok, reason).

        9Router's POST handler defaults ``priority`` to 1, which is why every
        connection used to share priority 1. Send ``max + 1`` instead. The lock
        keeps concurrent injects from picking the same number.
        """
        test_status = self.validate_key(key) if validate else "unknown"
        for attempt in range(3):
            with self._priority_lock:
                body = {
                    "provider": node_id,
                    "apiKey": key,
                    "name": f"{C.NODE_PREFIX}-{hashlib.sha1(key.encode()).hexdigest()[:8]}",
                    "priority": self.next_priority(node_id),
                    "defaultModel": C.DEFAULT_MODEL,
                    "testStatus": test_status,
                    "providerSpecificData": {
                        "prefix": C.NODE_PREFIX,
                        "apiType": "chat",
                        "baseUrl": C.PROVIDER_BASEURL,
                        "nodeName": C.NODE_NAME,
                    },
                }
                status, payload = self._api("POST", "/api/providers", body)
            if status in (200, 201):
                return True, (None if test_status == "active" else f"testStatus={test_status}")
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
