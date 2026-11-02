#!/usr/bin/env python3
"""Farm GonkaRouter accounts and collect their API keys.

Usage:
    python3 farm.py --target 25 --workers 6
    python3 farm.py --check-proxies
"""

import argparse
import json
import sys
import threading

from gonka import config as C
from gonka import net, signup


def farm(target, workers):
    pool = net.ProxyPool()
    print(f"proxies: {len(pool)}  target: {target}  workers: {workers}", flush=True)
    if len(pool) == 0:
        print("no proxies in GONKA_PROXY_FILE", flush=True)
        return 1

    lock = threading.Lock()
    done = 0
    tries = 0
    stop = threading.Event()

    def worker():
        nonlocal done, tries
        while not stop.is_set():
            with lock:
                if done >= target:
                    stop.set()
                    return
                tries += 1
                attempt = tries

            result = signup.create_account(pool)

            with lock:
                with open(C.RUN_LOG, "a") as fh:
                    fh.write(json.dumps(result) + "\n")
                if result.get("ok"):
                    done += 1
                    with open(C.KEYS_FILE, "a") as fh:
                        fh.write(result["first_key"] + "\n")
                    print(f"ok {done}/{target}  {result['first_key'][:14]}...  {result.get('email')}",
                          flush=True)
                else:
                    print(f"fail[{attempt}] {result.get('err')} "
                          f"send={result.get('send_status')}", flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"done ok={done}/{target} tries={tries}", flush=True)
    return 0 if done else 2


def check_proxies():
    pool = net.ProxyPool()
    alive = 0
    for proxy in list(pool.items):
        ip = net.check_proxy(proxy)
        if ip:
            alive += 1
            print(f"ok   {proxy} -> {ip}")
        else:
            print(f"dead {proxy}")
    print(f"alive {alive}/{len(pool)}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=10, help="accounts to create")
    parser.add_argument("--workers", type=int, default=C.WORKERS, help="parallel threads")
    parser.add_argument("--check-proxies", action="store_true", help="probe proxy file and exit")
    args = parser.parse_args()

    C.ensure_dirs()
    if args.check_proxies:
        return check_proxies()
    return farm(args.target, args.workers)


if __name__ == "__main__":
    sys.exit(main())
