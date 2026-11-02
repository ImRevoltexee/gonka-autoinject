#!/usr/bin/env python3
"""Bulk-inject collected keys into 9Router, or verify them against the upstream API.

Usage:
    python3 inject.py --all
    python3 inject.py --verify
"""

import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from gonka import config as C
from gonka import net, router


def inject_all(workers=8):
    keys = list(dict.fromkeys(router.read_key_file(C.KEYS_FILE)))
    if not keys:
        print(f"no keys in {C.KEYS_FILE}", flush=True)
        return 2

    client = router.Router()
    if not client.login():
        print("9Router login failed — check ROUTER_BASE and ROUTER_PASSWORD", flush=True)
        return 2

    node = client.node_id()
    if not node:
        print("could not resolve or create the provider node", flush=True)
        return 2

    known = client.registered_keys(node)
    pending = [key for key in keys if key not in known]
    print(f"node={node}  keys={len(keys)}  already={len(keys) - len(pending)}  "
          f"pending={len(pending)}", flush=True)

    def push(key):
        ok, reason = client.add_key(node, key)
        return key, ok, reason

    added = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, ok, reason in pool.map(push, pending):
            if ok:
                added += 1
                print(f"ok   {key[:14]}... {reason or ''}", flush=True)
            else:
                failed += 1
                print(f"fail {key[:14]}... {reason}", flush=True)

    print(f"done added={added} failed={failed} total_on_node={len(client.registered_keys(node))}",
          flush=True)
    return 0 if failed == 0 else 1


def verify_keys(workers=10):
    keys = list(dict.fromkeys(router.read_key_file(C.KEYS_FILE)))
    if not keys:
        print(f"no keys in {C.KEYS_FILE}", flush=True)
        return 2

    def probe(key):
        status, payload = net.get("/v1/models", key, timeout=20)
        models = []
        if isinstance(payload, dict):
            models = [m.get("id") for m in payload.get("data", [])]
        return key, status, models

    live = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, status, models in pool.map(probe, keys):
            if status == 200 and models:
                live.append({"key": key, "models": models})
                print(f"live {key[:14]}... {len(models)} models", flush=True)
            else:
                print(f"dead {key[:14]}... {status}", flush=True)

    report = C.DATA_DIR / "verified.json"
    report.write_text(json.dumps(live, indent=2))
    print(f"live {len(live)}/{len(keys)}  report: {report}", flush=True)
    return 0 if live else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="inject every key from the key file")
    parser.add_argument("--verify", action="store_true", help="probe keys against /v1/models")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    C.ensure_dirs()
    if args.verify:
        return verify_keys(args.workers)
    if args.all:
        return inject_all(args.workers)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
