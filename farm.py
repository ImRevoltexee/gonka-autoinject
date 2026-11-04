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
import time

from gonka import config as C
from gonka import net, signup


ERROR_HINTS = {
    "no_turnstile": "Turnstile solver returned no token; check that the solver is reachable.",
    "challenge_failed": "The API refused the Turnstile token; solve and submit from the same exit IP, or slow down.",
    "signup_limit": "The send endpoint rate-limited this proxy; rotate IPs or lower --workers.",
    "send_failed": "No proxy accepted the verification email request.",
    "no_otp": "No verification email/code arrived before the timeout.",
    "verify_failed": "The API rejected the verification code or never returned a token.",
    "no_key": "Account verified, but the API key was still not ready after polling.",
}

_print_lock = threading.Lock()


def log(level, message, tag="main"):
    """Print one timestamped line; safe to call from multiple worker threads."""
    level = level.upper()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with _print_lock:
        print(f"{stamp} {level:<5} [{tag}] {message}", flush=True)


def _fmt_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {seconds}s"


def _error_hint(err):
    if not err:
        return "unknown failure (the attempt returned no error code)"
    if err.startswith("tempmail:"):
        return "Could not create a temporary inbox (mail.tm may be down or rate-limiting)."
    if err.startswith("exception:"):
        return "Unexpected exception; see the message for details."
    return ERROR_HINTS.get(err, "Unexpected error code; inspect data/runs.jsonl for details.")


def _append(path, line, tag="main"):
    """Append a line to a file, logging (not raising) on failure."""
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except OSError as err:
        log("ERROR", f"cannot write {path}: {err}", tag)
        return False


def _result_details(result):
    """Compact diagnostics for one attempt."""
    parts = []
    email = result.get("email")
    if email:
        parts.append(f"email={email}")
    for field in ("send_status", "verify_status"):
        value = result.get(field)
        if value is not None:
            parts.append(f"{field}={value}")
    proxy = result.get("proxy")
    if proxy:
        parts.append(f"proxy={proxy}")
    for field in ("send_resp", "verify_resp"):
        value = result.get(field)
        if value:
            parts.append(f"{field}={value}")
    return " | ".join(parts)


def farm(target, workers):
    started_at = time.monotonic()
    pool = net.ProxyPool()

    log("INFO", f"target={target} workers={workers} proxies={len(pool)}")
    log("INFO", f"keys file: {C.KEYS_FILE}")
    log("INFO", f"run log:   {C.RUN_LOG}")

    if target < 1:
        log("ERROR", f"target must be at least 1 (got {target})")
        return 1
    if workers < 1:
        log("ERROR", f"workers must be at least 1 (got {workers})")
        return 1
    if len(pool) == 0:
        log("ERROR", f"no proxies found in {C.PROXY_FILE}")
        log("ERROR", "add one host:port per line, then test with: python3 farm.py --check-proxies")
        return 1

    lock = threading.Lock()
    done = 0
    tries = 0
    stop = threading.Event()

    def worker(tag):
        nonlocal done, tries
        while not stop.is_set():
            with lock:
                if done >= target:
                    stop.set()
                    return
                tries += 1
                attempt = tries
                progress = f"{done}/{target}"

            attempt_started = time.monotonic()
            log("INFO", f"attempt {attempt} started | success so far {progress}", tag)

            def on_stage(message, attempt=attempt):
                log("INFO", f"attempt {attempt} | {message}", tag)

            try:
                result = signup.create_account(pool, on_stage=on_stage)
            except Exception as err:  # create_account promises not to raise; stay safe.
                result = {
                    "ts": int(time.time()),
                    "ok": False,
                    "err": f"exception: {type(err).__name__}: {err}",
                }
                log("ERROR", f"attempt {attempt} | unexpected exception: "
                             f"{type(err).__name__}: {err}", tag)

            if not isinstance(result, dict):
                result = {
                    "ts": int(time.time()),
                    "ok": False,
                    "err": f"invalid_result: {type(result).__name__}",
                }

            elapsed = time.monotonic() - attempt_started
            _append(C.RUN_LOG, json.dumps(result, ensure_ascii=False), tag)

            with lock:
                if result.get("ok"):
                    done += 1
                    key = result.get("first_key") or ""
                    saved = _append(C.KEYS_FILE, key, tag) if key else False
                    log("OK", (
                        f"attempt {attempt} | account created in {_fmt_duration(elapsed)} "
                        f"| progress {done}/{target} | key {key[:14]}... "
                        f"| {_result_details(result)}"
                    ), tag)
                    if not saved:
                        log("ERROR", f"attempt {attempt} | key was not saved to {C.KEYS_FILE}", tag)
                    continue

                log("ERROR", (
                    f"attempt {attempt} | failed after {_fmt_duration(elapsed)} "
                    f"| reason={result.get('err') or 'unknown'} "
                    f"| hint: {_error_hint(result.get('err'))}"
                ), tag)
                details = _result_details(result)
                if details:
                    log("ERROR", f"attempt {attempt} | details: {details}", tag)

    threads = []
    for index in range(workers):
        tag = f"w{index + 1}"
        thread = threading.Thread(target=worker, args=(tag,), name=tag, daemon=True)
        threads.append(thread)
        log("INFO", "worker started", tag)
        thread.start()

    interrupted = False
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        interrupted = True
        stop.set()
        log("WARN", "interrupted by user; stopping (in-flight requests end with the process)")

    elapsed = time.monotonic() - started_at
    failed = max(0, tries - done)
    rate = done / elapsed * 60 if elapsed > 0 else 0.0
    summary = "interrupted" if interrupted else "complete"

    log("INFO", (
        f"summary ({summary}): accounts={done}/{target} attempts={tries} "
        f"failed={failed} time={_fmt_duration(elapsed)} rate={rate:.1f}/min"
    ))
    if done < target:
        log("WARN", f"{target - done} account(s) short of target; "
                    f"review errors above or {C.RUN_LOG}")
    elif done > target:
        log("INFO", f"{done - target} extra account(s) came from attempts already in flight")
    if done:
        log("INFO", f"keys appended to {C.KEYS_FILE}")

    if interrupted:
        return 130
    return 0 if done else 2


def check_proxies():
    pool = net.ProxyPool()
    total = len(pool)
    if total == 0:
        log("ERROR", f"no proxies found in {C.PROXY_FILE}")
        return 1

    log("INFO", f"checking {total} proxies from {C.PROXY_FILE}")
    started_at = time.monotonic()
    alive = 0
    dead = 0

    for index, proxy in enumerate(list(pool.items), 1):
        ip = net.check_proxy(proxy)
        if ip:
            alive += 1
            log("OK", f"[{index}/{total}] {proxy} -> {ip}")
        else:
            dead += 1
            log("ERROR", f"[{index}/{total}] {proxy} -> unreachable (timeout or bad proxy)")

    elapsed = time.monotonic() - started_at
    log("INFO", f"proxy check: alive={alive}/{total} dead={dead} time={_fmt_duration(elapsed)}")
    if alive == 0:
        log("ERROR", "no usable proxies; the farm cannot start until at least one is alive")
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=10, help="accounts to create")
    parser.add_argument("--workers", type=int, default=C.WORKERS, help="parallel threads")
    parser.add_argument("--check-proxies", action="store_true", help="probe proxy file and exit")
    args = parser.parse_args()

    try:
        C.ensure_dirs()
    except OSError as err:
        log("ERROR", f"cannot create data directories: {err}")
        return 1

    if args.check_proxies:
        return check_proxies()
    return farm(args.target, args.workers)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("WARN", "interrupted by user")
        sys.exit(130)
