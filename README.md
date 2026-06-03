# Scanner

**Problem:** Internet censorship in Iran uses Deep Packet Inspection (DPI) at the ISP level to detect and block proxy protocols in real time. A server that worked yesterday may be blocked today. This project continuously harvests V2Ray/XRAY proxy configurations from public Telegram channels and GitHub mirrors, tests each one under real Iranian network conditions, and serves only the verified working configs as a subscription URL compatible with V2Ray/XRAY clients.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Sources                              │
│   Telegram Channels        GitHub / Public HTTP Mirrors     │
└─────────────┬──────────────────────────┬────────────────────┘
              │                          │
              └──────────┬───────────────┘
                         ▼
                   scanner/sources.py
              (raw text → proxy URI strings)
                         │
                         ▼
                   scanner/parser.py
              (URI strings → ParsedConfig objects)
                         │
                         ▼
                   TCP Probe  ≤ 1050 ms?
                   scanner/probe.py
                    /          \
                 PASS          FAIL → discard
                   │
                   ▼
             xray-core test
         curl 100 KB through SOCKS5
                   │
              /─────────\
           PASS         FAIL → discard
              │
              ▼
         ProbeResult(is_working=True, speed_kbps=N)
              │
              ▼
         scanner/engine.py  →  SQLite (Django ORM)
              │
              ▼
    GET /api/confs/
    plain text, one link per line
    (V2Ray / XRAY client compatible)
```

---

## Technical Flow

1. **Fetch** (`scanner/sources.py`)
   Telethon reads the last 500 messages from each configured Telegram channel (today + yesterday). HTTP mirrors are fetched with `requests`. All text is scanned with regex patterns for `vless://`, `vmess://`, `trojan://`, `ss://` URIs.

2. **Parse** (`scanner/parser.py`)
   Each raw URI is decoded into a frozen `ParsedConfig` dataclass — host, port, user_id, protocol, remark, and method (ss only). vmess uses base64 + JSON. ss handles both encoded and plain-auth forms. Malformed links return `None` and are silently dropped.

3. **TCP Probe** (`scanner/probe.py`)
   A fast TCP connect (2 s timeout) to `(host, port)` filters dead nodes. Latency ≥ 1050 ms counts as failure. This eliminates the majority of candidates before the expensive xray test.

4. **Deep Test** (`scanner/probe.py`)
   xray-core is launched with the config on a random local SOCKS5 port. `curl` downloads 100 KB through Cloudflare's speed endpoint (edge-cached, resistant to country-level filtering). Speed = `100 KB / seconds`. The xray process is killed via process-group signal and the temp config file deleted in a `finally` block — guaranteed regardless of outcome.

5. **Rank & Persist** (`scanner/engine.py`)
   Working `ProbeResult` objects are filtered and ranked by `filter_and_rank()`. Nodes are bulk-upserted to SQLite. Existing nodes are re-tested on every run; failures are pruned.

6. **Serve** (`scanner/views.py`)
   `GET /api/confs/` returns all `is_working=True` nodes as newline-separated links, ready to paste into V2RayNG, Clash, or any XRAY-compatible client.

---

## Installation

### Option A — Automated (recommended)

One command installs everything from scratch — supports Linux, macOS, and Windows (Git Bash). Requires `sudo` on Linux (apt + systemd) and [Homebrew](https://brew.sh) on macOS.

```bash
bash <(curl -Ls https://raw.githubusercontent.com/sinachaichi/scanner/main/install.sh)
```

> **Note:** use `bash <(curl ...)` — not `curl ... | bash`. The pipe form breaks the
> interactive Telegram credential prompts.

The script will:
1. Install system packages and Redis — apt on Linux, Homebrew on macOS, or verify requirements on Windows (Git Bash)
2. Clone the repo to `~/scanner_project`
3. Create a virtualenv and install Python dependencies
4. Prompt for Telegram API credentials (press Enter or type `no` to skip and use HTTP mirrors only; the API hash is read with hidden input)
5. Generate a fresh `SECRET_KEY` and `FIELD_ENCRYPTION_KEY`, write `.env` (chmod `600`)
6. Run migrations and create an admin superuser (password set via `DJANGO_SUPERUSER_PASSWORD`, never exposed in the process list)
7. **Linux:** register three systemd services (Gunicorn, Celery worker, Celery beat) and install global helper commands
8. **macOS:** register three launchd LaunchAgents (no sudo needed; auto-start at login, restart on crash)
9. **Windows:** start services in the background with `nohup`
10. Print the admin panel URL and generated password

---

### Option B — Manual

**Prerequisites:** Python 3.12, Redis, `curl`, Git.

```bash
git clone https://github.com/sinachaichi/scanner.git
cd scanner
```

**1. Virtualenv and dependencies**

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
```

**2. xray binary**

The `xray` binary is not included in the repo — download the correct build for your platform from the [xray-core releases](https://github.com/XTLS/Xray-core/releases/latest) and place it in the project root. Quick one-liner per platform:

```bash
# macOS (Apple Silicon)
curl -fsSL -o /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-macos-arm64-v8a.zip && unzip -o /tmp/xray.zip xray

# macOS (Intel)
curl -fsSL -o /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-macos-64.zip && unzip -o /tmp/xray.zip xray

# Linux x86_64
curl -fsSL -o /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip && unzip -o /tmp/xray.zip xray

chmod +x xray
```

Or set `XRAY_PATH` in `.env` to point to an existing xray binary elsewhere on your system.

**3. Environment**

```bash
cp .env.example .env
```

Edit `.env` and fill in at minimum `SECRET_KEY` and `FIELD_ENCRYPTION_KEY` (see [Configuration](#configuration-env) below). The app will refuse to start without these two.

**4. Database**

```bash
python manage.py migrate
python manage.py createsuperuser
```

**5. Start services**

```bash
redis-server &
# Redis uses RDB persistence by default; disable it since Celery data is ephemeral
redis-cli CONFIG SET save "" && redis-cli CONFIG SET stop-writes-on-bgsave-error no
celery -A config worker -Q scanner_queue --loglevel=info &
celery -A config beat --loglevel=info &
python manage.py runserver 0.0.0.0:8000
```

---

## First Run

After installation (either method):

1. Open the admin panel at `http://<server-ip>:<port>/admin/` and log in.
2. Under **Mirrors**, add one or more public config mirror URLs and mark them active.
   *(Optional)* Under **Channels**, add Telegram channel usernames (e.g. `@someproxychannel`) if you configured Telegram credentials.
3. Trigger a scan in one of two ways:
   - **Admin action:** select mirrors or channels → choose *"Scan and update nodes"* from the action dropdown.
   - **Management command:** `python manage.py run_full_scan` (or `scanner-manage run_full_scan` on Linux after the automated install).
   Scans take 10–60 minutes depending on how many nodes are found.
4. Once the scan completes, working nodes appear under **Nodes**.
5. Copy the subscription URL: `http://<server-ip>:<port>/api/confs/`
6. Paste it into your V2Ray/XRAY client (V2RayNG, Nekoray, Clash Meta, etc.) as a subscription link.

---

## Configuration (.env)

Copy `.env.example` to `.env` and fill in all values.
**Never commit `.env` to version control.**

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | **Yes** | Django secret key. Generate: `openssl rand -hex 32` |
| `FIELD_ENCRYPTION_KEY` | **Yes** | Fernet key for encrypting GitHub tokens at rest. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `TELEGRAM_API_ID` | No | From [my.telegram.org](https://my.telegram.org) → API development tools. Omit to use HTTP mirrors only. |
| `TELEGRAM_API_HASH` | No | Same source as API ID. Treat like a password — never share. |
| `DEBUG` | No | `False` in production (default). |
| `ALLOWED_HOSTS` | No | Comma-separated hostnames. Default: `localhost`. |
| `XRAY_PATH` | No | Path to xray binary. Default: `./xray`. |
| `CELERY_BROKER_URL` | No | Redis URL. Default: `redis://redis:6379/0`. |
| `RANDOM_PORT` | No | Port for Gunicorn / CSRF origin. Default: `8000`. |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`. Default: `INFO`. |

---

## Tech Stack

| Component | Choice | Why |
|---|---|---|
| **Web framework** | Django 5 | Admin UI manages channels/mirrors/nodes without custom frontend; ORM handles SQLite cleanly |
| **Task queue** | Celery + Redis | Scans take 20–60 min; Celery handles scheduling, retries, and result storage |
| **Database** | SQLite | Single-server deployment with no concurrent writers — zero ops overhead |
| **Proxy engine** | xray-core | Supports VLESS/VMess/Trojan/SS including REALITY and XTLS extensions |
| **Telegram client** | Telethon | Async MTProto — reads public channels without bot API rate limits |
| **Field encryption** | django-encrypted-model-fields | GitHub tokens stored encrypted at rest (Fernet symmetric encryption) |

---

## Security

**Credentials are environment-only.** Telegram `api_id`/`api_hash` and Django `SECRET_KEY` are loaded exclusively from environment variables. The application refuses to start if `SECRET_KEY` or `FIELD_ENCRYPTION_KEY` are absent. There are no hardcoded fallback values.

**GitHub tokens are encrypted at rest.** `Subscription.token` uses `EncryptedCharField` — the value stored in SQLite is Fernet-encrypted. The encryption key lives in `.env` and must never be committed.

**Temp files use the OS temp directory.** xray config files are written with `tempfile.mkstemp()` and deleted in a `finally` block, not left in the working directory.

**Files that must never be committed:**
- `.env` — credentials
- `db.sqlite3` — may contain decrypted node data
- `session_name.session` — Telegram session file (equivalent to a logged-in account)

---

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests mock all network calls and subprocess invocations — no real xray binary, Telegram connection, or internet access required.

```
tests/
  test_parser.py   — ParsedConfig parsing, edge cases, all four protocol decoders
  test_probe.py    — TcpProbe.ping/is_reachable, build_xray_config, XraySpeedTester.test
  test_scanner.py  — end-to-end pipeline orchestration and node re-testing
  test_filter.py   — filter_and_rank ordering and filtering logic
  test_sources.py  — MirrorSource fetch/parse + error handling, Telegram guard branches
```

### Coverage

`pytest` runs with coverage enabled and **fails below 80%** (see `pyproject.toml`).
The gate is scoped to the modules that hold real algorithmic logic — parsing,
probing, ranking, the scan pipeline, and source fetching. The thin Django
integration layers (ORM persistence, the GitHub HTTP push, views, Celery task
wrappers, the composition root) are excluded from the gate: their behaviour is
the framework's, not ours, so they're better covered by integration tests than
by unit-coverage on glue.

---

## Admin Interface

The Django admin at `/admin/` provides:
- **Channels** — add/remove Telegram channel usernames, trigger per-channel scans
- **Mirrors** — add/remove HTTP mirror URLs, trigger per-mirror scans
- **Nodes** — view working configs with speed/latency columns, filter by protocol
- **Subscriptions** — configure GitHub push targets (token stored encrypted)

### Linux helper commands (automated install only)

```
scanner-logs [web|celery|beat]   tail service logs
scanner-restart                  restart all three services
scanner-stop                     stop all three services
scanner-manage <cmd>             python manage.py <cmd>
scanner-celery <args>            celery <args>
scanner-celery-task              interactively pick and trigger a task
```
