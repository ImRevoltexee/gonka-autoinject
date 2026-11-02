# gonka-autoinject

Registration pipeline for GonkaRouter plus a daemon that pushes every collected
key into a [9Router](https://github.com/decolua/9router) instance as a single
`openai-compatible` provider node.

Two parts, usable separately:

- **farm** — creates accounts end to end (disposable inbox, Turnstile, email
  verification, first key reveal) and appends keys to a text file.
- **inject** — bulk-imports that file into 9Router, or watches it and imports
  new keys as they land.

Pure standard library. No pip install, no Playwright, no browser.

---

## How the registration flow works

```
mail.tm inbox  ──►  POST /auth/email/send    (proxy #n + fresh Turnstile token)
                          │
                          ▼
                   6-digit mail  ──►  POST /auth/email/verify   (same or next proxy)
                          │
                          ▼
                       JWT  ──►  GET /api/keys  ──►  POST /api/keys/{id}/reveal
                          │
                          ▼
                    sk-... key appended to data/keys.txt
```

Three details matter more than everything else in this repo:

1. **Turnstile tokens are single use.** Solved once and replayed after a failed
   send → `403 challenge_failed`, and the address is burned. `signup.py` solves
   again on every attempt for this reason.
2. **One account per proxy.** The token is accepted from any source IP, but the
   send endpoint rate-limits per IP. Rotating the proxy per attempt is what keeps
   the pipeline moving.
3. **Verification is the slow part.** Registration returns immediately; the mail
   takes 15–90 s. Keys are not provisioned the instant the JWT appears, so
   `reveal_key()` polls `/api/keys` with backoff instead of failing on the first
   empty list.

## Requirements

- Python 3.9+
- A local Turnstile solver listening on `http://127.0.0.1:407`, accepting
  `POST /turnstile {"url": ..., "sitekey": ...}` and answering `{"token": "..."}`
  (any solver with that contract works)
- A proxy list — one `host:port` per line, optionally `user:pass@host:port` per
  line if your client supports it (the built-in opener handles the IP:port form)
- 9Router running and reachable for the inject half

## Install

```bash
git clone https://github.com/<you>/gonka-autoinject.git
cd gonka-autoinject
cp .env.example .env
$EDITOR .env
mkdir -p data
set -a; . ./.env; set +a
```

Nothing is installed globally; run the scripts from the repository root so the
`gonka` package resolves.

## Configure

`gonka/config.py` reads everything from the environment. The values you will
actually touch:

| Variable | Default | Purpose |
|---|---|---|
| `GONKA_DATA_DIR` | `./data` | where keys, logs and the proxy list live |
| `GONKA_PROXY_FILE` | `$DATA_DIR/proxies.txt` | proxy pool |
| `GONKA_KEYS_FILE` | `$DATA_DIR/keys.txt` | append-only key output |
| `GONKA_SOLVER_URL` | `http://127.0.0.1:407` | Turnstile solver |
| `GONKA_WORKERS` | `6` | parallel registration threads |
| `ROUTER_BASE` | `http://127.0.0.1:20129` | 9Router HTTP API |
| `ROUTER_PASSWORD` | — | 9Router admin password (required by inject/watcher) |
| `ROUTER_POLL_INTERVAL` | `20` | watcher poll, seconds |

`ROUTER_DB` defaults to `~/.9router/db/data.sqlite` and is opened **read-only**,
only to see which keys the node already has. Nothing writes to it.

## Usage

Probe the proxy pool first — dead exits are the number one cause of
`send_failed`:

```bash
python3 farm.py --check-proxies
```

Farm:

```bash
python3 farm.py --target 25 --workers 6
```

Every attempt is appended to `data/runs.jsonl` (status codes, proxy used, error
class) whether it succeeds or not. Failures are cheap to diagnose from that file:

```bash
jq -r 'select(.ok==false) | "\(.err)\t\(.send_status)"' data/runs.jsonl | sort | uniq -c
```

Verify what you collected:

```bash
python3 inject.py --verify
```

That hits `/v1/models` with each key, prints live/dead, and writes
`data/verified.json`.

Push everything into 9Router:

```bash
python3 inject.py --all
```

The provider node is created on first run (`name=GONKA`, `type=openai-compatible`,
`baseUrl=https://api.gonkarouter.io/v1`). Re-running is safe: existing keys are
read from the router DB and skipped.

Watch mode — new keys are injected within one poll interval:

```bash
python3 watcher.py
```

## systemd

Two units, so farming and injection survive an SSH disconnect.

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gonka-watcher.service
```

Farm on demand rather than continuously — it burns proxies:

```bash
sudo systemctl start gonka-farm@25.service   # 25 accounts, then exits
```

Both units read `/root/gonka-autoinject/.env`; adjust `User`, `WorkingDirectory`
and the env path if you run as someone else.

## Upstream models

GonkaRouter exposes three models:

```
deepseek-ai/DeepSeek-V4-Flash-0731     fastest first token, good default
MiniMaxAI/MiniMax-M2.7                 best long-form and code output
zai-org/GLM-5.3-Flash                  spends tokens on a hidden reasoning field
```

Measured on a warm cache, first byte with `"stream": true`:

```
DeepSeek-V4-Flash    ~0.4 s first byte, ~2.7 s total
MiniMax-M2.7         ~0.9 s first byte, ~5.2 s total
GLM-5.3-Flash        ~0.7 s first byte, ~12.5 s total
```

Without streaming the same requests take 3–30 s, because the whole completion is
buffered before the response starts. If the client feels slow, it is almost
always missing `"stream": true` rather than the upstream being slow. Numbers in
the 60–90 ms range that show up in casual benchmarks are cache hits — compare
answer hashes before trusting them.

## Troubleshooting

**`send_failed` on every attempt.** Proxy pool exhausted or solver down. Check
`curl -s localhost:407/turnstile -d '{"url":"https://gonkarouter.io/","sitekey":"..."}'`
and re-run `--check-proxies`.

**`signup_limit`.** That exit IP already registered. It will not recover; the
proxy needs replacing.

**`no_otp`.** Mail never arrived, usually a domain the upstream silently drops.
Retrying with a new inbox is cheaper than debugging it.

**`verify_failed` with a code in hand.** The proxy died mid-handshake — the code
is still valid for a short window, which is why verification retries on the next
proxy instead of starting over.

**`no_key`.** Account exists but the key is still provisioning; the reveal loop
gives up after ~24 s. The JWT is not persisted, so that account is a loss.

**9Router login fails.** `ROUTER_PASSWORD` unset, or the instance is bound to a
different port. `curl -s $ROUTER_BASE/api/auth/status` should answer.

## Notes

- Keys live in plain text in `data/keys.txt`. That directory is in `.gitignore`;
  keep it that way.
- 9Router's `/api/providers` endpoint accepts duplicates with `409`; the code
  treats that as success and moves on.
- The node's `providerConnections` rows are read from SQLite because the HTTP API
  returns keys masked. That is the only reason the DB path is configurable.

## License

MIT
