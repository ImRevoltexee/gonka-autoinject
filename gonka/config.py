"""Runtime configuration. Everything is environment-driven; nothing is hardcoded."""

import os
from pathlib import Path


def _load_env_file(env_path: Path) -> None:
    if not env_path.is_file():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


_load_env_file(Path(__file__).resolve().parent.parent / ".env")


def _p(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# --- upstream ---------------------------------------------------------------
API = os.environ.get("GONKA_API", "https://api.gonkarouter.io")
ORIGIN = os.environ.get("GONKA_ORIGIN", "https://gonkarouter.io")
SITEKEY = os.environ.get("GONKA_SITEKEY", "0x4AAAAAAFcbQ4RGI69Sc6pz")
CHANNEL = os.environ.get("GONKA_CHANNEL", "gonka")
USER_AGENT = os.environ.get(
    "GONKA_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# --- local state ------------------------------------------------------------
DATA_DIR = _p("GONKA_DATA_DIR", "./data")
KEYS_FILE = _p("GONKA_KEYS_FILE", str(DATA_DIR / "keys.txt"))
RUN_LOG = _p("GONKA_RUN_LOG", str(DATA_DIR / "runs.jsonl"))
PROXY_FILE = _p("GONKA_PROXY_FILE", str(DATA_DIR / "proxies.txt"))

# --- dependencies -----------------------------------------------------------
SOLVER_URL = os.environ.get("GONKA_SOLVER_URL", "http://127.0.0.1:407")
SOLVER_TIMEOUT = _i("GONKA_SOLVER_TIMEOUT", 180)
MAIL_BASE = os.environ.get("GONKA_MAIL_BASE", "https://api.mail.tm")

# --- farm loop --------------------------------------------------------------
WORKERS = _i("GONKA_WORKERS", 6)
MAX_PROXY_ATTEMPTS = _i("GONKA_MAX_PROXY_ATTEMPTS", 5)
OTP_TIMEOUT = _i("GONKA_OTP_TIMEOUT", 200)

# --- 9Router ----------------------------------------------------------------
ROUTER_BASE = os.environ.get("ROUTER_BASE", "http://127.0.0.1:20128")
ROUTER_PASSWORD = os.environ.get("ROUTER_PASSWORD", "")
ROUTER_DB = _p("ROUTER_DB", "~/.9router/db/data.sqlite")
NODE_NAME = os.environ.get("ROUTER_NODE_NAME", "GONKA")
NODE_PREFIX = os.environ.get("ROUTER_NODE_PREFIX", "gonka")
PROVIDER_BASEURL = os.environ.get("ROUTER_PROVIDER_BASEURL", "https://api.gonkarouter.io/v1")
DEFAULT_MODEL = os.environ.get("ROUTER_DEFAULT_MODEL", "deepseek-ai/DeepSeek-V4-Flash-0731")
POLL_INTERVAL = _i("ROUTER_POLL_INTERVAL", 20)


def ensure_dirs() -> None:
    for p in (DATA_DIR, KEYS_FILE.parent, RUN_LOG.parent, PROXY_FILE.parent):
        p.mkdir(parents=True, exist_ok=True)
