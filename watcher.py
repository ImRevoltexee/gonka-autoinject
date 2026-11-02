#!/usr/bin/env python3
"""Daemon: watch the key file and register new keys as they appear.

    python3 watcher.py

Designed for systemd. Logs to stdout, one line per event.
"""

import json
import os
import sys
import time

from gonka import config as C
from gonka import router


def log(message):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", flush=True)


def main():
    C.ensure_dirs()
    if not C.ROUTER_PASSWORD:
        log("ROUTER_PASSWORD is not set")
        return 2

    log(f"watcher start pid={os.getpid()} poll={C.POLL_INTERVAL}s keys={C.KEYS_FILE}")

    client = router.Router()
    node = None
    known = set()

    while True:
        try:
            if node is None:
                if not client.login():
                    log("login failed, retrying")
                    time.sleep(30)
                    continue
                node = client.node_id()
                if not node:
                    log("node unavailable, retrying")
                    time.sleep(30)
                    continue
                known = client.registered_keys(node)
                log(f"node={node} existing={len(known)}")

            keys = list(dict.fromkeys(router.read_key_file(C.KEYS_FILE)))
            fresh = [key for key in keys if key not in known]

            if fresh:
                log(f"new keys detected: {len(fresh)}")
                for key in fresh:
                    ok, reason = client.add_key(node, key)
                    if ok:
                        known.add(key)
                        log(f"  inject {key[:14]}... {reason or 'ok'}")
                    else:
                        log(f"  fail   {key[:14]}... {reason}")

                known = client.registered_keys(node)
                log(f"connections on node: {len(known)}")

        except KeyboardInterrupt:
            log("stopped")
            return 0
        except Exception as err:
            log(f"cycle error {type(err).__name__}: {str(err)[:100]}")
            node = None
            time.sleep(15)

        time.sleep(C.POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
