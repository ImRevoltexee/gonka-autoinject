# gonka-autoinject

[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](.)
[![License](https://img.shields.io/badge/license-MIT-black)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20systemd-lightgrey)](deploy/)
[![PRs](https://img.shields.io/badge/PRs-welcome-orange)](../../pulls)

Registration pipeline for **GonkaRouter** plus a daemon that pushes every key it
collects into a [9Router](https://github.com/decolua/9router) instance as a
single `openai-compatible` provider node.

Three moving parts:

| File | Job |
|---|---|
| `farm.py` | takes an account from empty inbox to a working `sk-` key |
| `inject.py` | bulk-imports the key file into 9Router, or verifies keys upstream |
| `watcher.py` | long-running daemon, injects new keys within one poll interval |

No `pip install`. No Playwright, no Selenium, no headless browser — standard
library only, ~700 lines you can read in one sitting.

```
$ python3 farm.py --target 1
proxies: 98  target: 1  workers: 2
ok 1/1  sk-Fowb3xTJajI...  yamhsinbtshk@fldubai.com

$ ROUTER_PASSWORD=... python3 inject.py --all
node=openai-compatible-chat-bed0657c  keys=1  already=0  pending=1
ok   sk-Fowb3xTJajI...
done added=1 failed=0 total_on_node=284
```

---

## Why

GonkaRouter gives every account a quota of model tokens. Doing that by hand is a
five-minute dance per account: pick a mailbox, wait for the code, solve the
challenge, verify, copy the key out of the dashboard. Multiply by fifty and it
stops being worth doing at all.

This repo collapses it into one command, then closes the loop — the key you just
made is already a provider in your router before you look back at the terminal.
The router side is idempotent, so running `inject.py --all` twice changes
nothing.

## Features

- **Full registration flow** — disposable inbox → Turnstile → mail code → JWT →
  key reveal, with the retry each step actually needs
- **Proxy rotation per attempt** — one account per exit IP; dead exits dropped
- **Single-use token handling** — Turnstile re-solved on every submit, because
  replaying a consumed token burns the address
- **Provisioning-aware reveal** — polls `/api/keys` with backoff instead of
  failing while the key is still being created
- **Idempotent injection** — existing connections read from the router DB
  (read-only), so re-runs skip what is already there
- **Key verification** — `--verify` probes `/v1/models` per key, writes a JSON
  report
- **Queryable logs** — every attempt lands in `data/runs.jsonl` with status code
  and error class
- **systemd units included** — survives SSH disconnects, restarts on crash
- **Zero hardcoded secrets** — all configuration through environment variables

## Quickstart

```bash
git clone https://github.com/ImRevoltexee/gonka-autoinject.git
cd gonka-autoinject
cp .env.example .env && $EDITOR .env

mkdir -p data
echo "127.0.0.1:8080" > data/proxies.txt   # proxies, one host:port per line

set -a; . ./.env; set +a

python3 farm.py --check-proxies            # prove the pool is alive
python3 farm.py --target 10 --workers 6    # 10 accounts
python3 inject.py --verify                 # which keys are live
python3 inject.py --all                    # push them into 9Router
python3 inject.py --refresh-status         # re-check keys and update 9Router status
python3 inject.py --fix-priority           # renumber connections 1..N
```

Needs a Turnstile solver listening on `127.0.0.1:407` that accepts
`POST /turnstile {"url","sitekey"}` and answers `{"token": "..."}`. Any solver
with that contract drops in.

## How it works

```
                       ┌──────────────────────────────┐
  data/proxies.txt ───►│  ProxyPool   round-robin     │
                       └──────────────┬───────────────┘
                                      │ one exit IP per attempt
                                      ▼
  mail.tm inbox ──► POST /auth/email/send   (fresh Turnstile token each time)
                          │
                          ▼
                   6-digit code ──► POST /auth/email/verify
                          │
                          ▼
                       JWT ──► GET /api/keys ──► POST /api/keys/{id}/reveal
                          │
                          ▼
                  data/keys.txt  (append-only)
                          │
                          ▼
        watcher.py ──► 9Router  POST /api/provider-nodes + /api/providers
```

Three details carry the whole pipeline:

1. **Turnstile tokens are single use.** Solved once and replayed after a failed
   send → `403 challenge_failed`, and the address is burned. `signup.py` solves
   again on every attempt.
2. **One account per proxy.** The token is accepted from any source IP, but the
   send endpoint rate-limits per IP. Rotation keeps throughput up.
3. **Verification is the slow part.** Registration returns instantly; the mail
   lands in 15–90 s, and the key is not provisioned the moment the JWT appears.
   Hence the polling loop instead of one optimistic request.

## Configuration

Everything lives in `gonka/config.py`, read from the environment. The values you
will actually touch:

| Variable | Default | Purpose |
|---|---|---|
| `GONKA_DATA_DIR` | `./data` | keys, logs, proxy list |
| `GONKA_PROXY_FILE` | `$DATA_DIR/proxies.txt` | proxy pool |
| `GONKA_KEYS_FILE` | `$DATA_DIR/keys.txt` | append-only key output |
| `GONKA_SOLVER_URL` | `http://127.0.0.1:407` | Turnstile solver |
| `GONKA_WORKERS` | `6` | parallel registrations |
| `GONKA_OTP_TIMEOUT` | `200` | seconds to wait for the mail |
| `ROUTER_BASE` | `http://127.0.0.1:20128` | 9Router HTTP API |
| `ROUTER_PASSWORD` | — | 9Router admin password |
| `ROUTER_POLL_INTERVAL` | `20` | watcher poll, seconds |

`ROUTER_DB` defaults to `~/.9router/db/data.sqlite` and is opened **read-only**,
only to see which keys the node already has. Nothing writes to it.

## Usage

Farm, then inspect the failures — they are cheap to diagnose:

```bash
jq -r 'select(.ok==false) | "\(.err)\t\(.send_status)"' data/runs.jsonl | sort | uniq -c
```

Watch mode keeps the router current while you farm:

```bash
python3 watcher.py
```

### systemd

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gonka-watcher.service
sudo systemctl start gonka-farm@25.service     # 25 accounts, then exits
```

Both units read `/root/gonka-autoinject/.env`. Adjust `User`, `WorkingDirectory`
and the env path if you run as someone else.

## Model notes

GonkaRouter exposes three models. First byte measured with `"stream": true` on a
warm connection:

| Model | First byte | Total | Notes |
|---|---|---|---|
| `deepseek-ai/DeepSeek-V4-Flash-0731` | ~0.4 s | ~2.7 s | fastest, good default |
| `MiniMaxAI/MiniMax-M2.7` | ~0.9 s | ~5.2 s | best long-form and code |
| `zai-org/GLM-5.3-Flash` | ~0.7 s | ~12.5 s | spends tokens on a hidden reasoning field |

Without `"stream": true` the same requests take 3–30 s, because the full
completion is buffered before the response starts. When a client feels slow it is
almost always missing the `stream` flag, not a slow upstream.

Benchmarks in the 60–90 ms range are cache hits — repeat the same prompt, compare
answer hashes, and identical output comes back almost instantly. Do not ship
those numbers as latency.

## Troubleshooting

**`send_failed` on every attempt** — proxy pool exhausted or the solver is down.
Post the solver directly, then re-run `--check-proxies`.

**`signup_limit`** — that exit IP already registered. It will not recover; swap
the proxy.

**`no_otp`** — the mail never arrived, usually a domain the upstream silently
drops. A new inbox is cheaper than debugging it.

**`verify_failed` with a code in hand** — the proxy died mid-handshake. The code
stays valid for a short window, so verification retries on the next proxy
instead of restarting the account.

**`no_key`** — the account exists but the key was still provisioning when the
reveal loop gave up (~24 s). The JWT is not persisted, so treat that account as
a loss.

**9Router login fails** — `ROUTER_PASSWORD` unset, or the instance is on another
port. `curl -s $ROUTER_BASE/api/auth/status` should answer.

## FAQ

**Do I need a browser?** No. The challenge is solved by a local solver over HTTP;
everything else is plain requests.

**Can I use a different solver?** Yes, if it speaks
`POST /turnstile {"url","sitekey"} → {"token"}`.

**Does it touch my router database?** Read only, to list existing keys. Writes
go through the 9Router HTTP API.

**How many accounts per IP?** One. The send endpoint limits per IP, so pool size
is your real ceiling.

**Why not asyncio?** Threads plus `urllib` keep it dependency-free, and the
bottleneck is network wait, not the interpreter.

## Notes

- Keys sit in plain text in `data/keys.txt`; that directory is gitignored. Keep
  it that way.
- 9Router answers duplicate connections with `409`; the code treats that as
  success and moves on.
- New connections are validated against `/v1/models` before being created, so
  the dashboard shows them as **active** instead of **unknown**. Re-run
  `python3 inject.py --refresh-status` to re-check keys that are already there.
- New connections get the next free `priority` (`max + 1`), so the dashboard no
  longer shows every row as `#1`. `python3 inject.py --fix-priority` renumbers
  existing connections to a unique `1..N` sequence (oldest first).
- Connection rows are read from SQLite because the HTTP API returns keys masked.
  That is the only reason the DB path is configurable.
- The **API Key field in 9Router's edit dialog is always blank**: 9Router strips
  keys from every API response on purpose, so the dashboard cannot display them.
  The key is still stored (see `data/keys.txt` or the SQLite `data` column) and
  routes normally. Use `python3 inject.py --verify` if you want proof a key works.

## Roadmap

- [ ] concurrent key verification with per-key backoff
- [ ] pluggable mail backends (mail.tm only today)
- [ ] Prometheus metrics endpoint for the watcher
- [ ] Docker image

## Contributing

Issues and PRs welcome. Keep it standard-library-only unless there is a strong
reason — the no-dependencies property is the point.

## License

MIT — see [LICENSE](LICENSE).

---

Automate services you are allowed to automate, and keep `data/` out of git.
