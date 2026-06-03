#!/usr/bin/env bash
# Unified installer for scanner.
# Linux:   installs apt packages, sets up systemd services and helper commands.
# macOS:   installs Homebrew packages, sets up launchd LaunchAgents (no sudo).
# Windows (Git Bash): starts services in the background with nohup.
# Requirements on Windows: Python 3.12, Git, Redis — all in PATH.

set -e

# Guard against the common `curl | bash` mistake. That form pipes curl's stdout
# into bash's stdin, which means `read` prompts silently get empty strings.
# Process substitution `bash <(curl ...)` keeps stdin as the terminal.
if [ ! -t 0 ]; then
    echo "ERROR: stdin is not a terminal."
    echo "Run the installer with process substitution, not a pipe:"
    echo "  bash <(curl -Ls <URL>)"
    exit 1
fi

# ── Detect platform ───────────────────────────────────────────────────────────
case "$(uname -s)" in
    Linux*)              PLATFORM=linux  ;;
    Darwin*)             PLATFORM=macos  ;;
    MINGW*|CYGWIN*|MSYS*) PLATFORM=windows ;;
    *) echo "Unsupported OS: $(uname -s)"; exit 1 ;;
esac

# ── Variables ─────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/sinachaichi/scanner.git"
PROJECT_DIR="$HOME/scanner_project"
VENV_DIR="$PROJECT_DIR/.venv"
DJANGO_MODULE="config"
RANDOM_PORT=$(( RANDOM % 10000 + 30000 ))
DJANGO_SUPERUSER="admin"
DJANGO_SUPERPASS=$(openssl rand -hex 16)

# Best-effort LAN IP, per platform. `hostname -I` is Linux-only; macOS uses
# ipconfig. Prints nothing if it can't be determined (callers default to localhost).
detect_lan_ip() {
    case "$PLATFORM" in
        linux) hostname -I 2>/dev/null | awk '{print $1}' ;;
        macos) ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true ;;
        *)     echo "" ;;
    esac
}

# ── System packages ───────────────────────────────────────────────────────────
if [ "$PLATFORM" = "linux" ]; then
    echo "🔧 Installing system packages (apt)..."
    sudo apt update || true
    sudo apt install -y python3.12 python3.12-full python3.12-venv redis-server git curl openssl || true
    sudo systemctl enable --now redis-server
    # Redis is used only as a Celery broker — RDB persistence is not needed and
    # causes MISCONF errors when the disk is full or the dump dir is unwritable.
    redis-cli CONFIG SET save "" || true
    redis-cli CONFIG SET stop-writes-on-bgsave-error no || true
    PYTHON_BIN="python3.12"
elif [ "$PLATFORM" = "macos" ]; then
    echo "🔧 Installing system packages (Homebrew)..."
    if ! command -v brew >/dev/null 2>&1; then
        echo "ERROR: Homebrew is required on macOS. Install it from https://brew.sh and re-run."
        exit 1
    fi
    brew install python@3.12 redis git || true
    brew services start redis || true
    # Redis is used only as a Celery broker — disable RDB persistence to avoid
    # MISCONF errors when the disk is full or the dump dir is unwritable.
    redis-cli CONFIG SET save "" || true
    redis-cli CONFIG SET stop-writes-on-bgsave-error no || true
    PYTHON_BIN="$(brew --prefix python@3.12 2>/dev/null)/bin/python3.12"
    [ -x "$PYTHON_BIN" ] || PYTHON_BIN="python3.12"
else
    PYTHON_BIN="python"
    echo "ℹ️  Windows mode — ensure Python 3.12, Git, and Redis are installed and in PATH."
fi

# ── Clone ─────────────────────────────────────────────────────────────────────
if [ -d "$PROJECT_DIR" ]; then
    echo "⚠️  $PROJECT_DIR exists, removing..."
    rm -rf "$PROJECT_DIR"
fi

echo "🚀 Cloning project..."
git clone "$REPO_URL" "$PROJECT_DIR"
cd "$PROJECT_DIR"

# ── Virtualenv + dependencies ─────────────────────────────────────────────────
echo "🐍 Creating virtualenv..."
"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

echo "⬆️  Installing Python deps..."
pip install --upgrade pip
pip install -r requirements.txt

# ── Telegram credentials ──────────────────────────────────────────────────────
echo "🔑 Enter Telegram API ID (or 'no' to skip and use HTTP mirrors only):"
read -r TELEGRAM_API_ID

if [ "$TELEGRAM_API_ID" != "no" ]; then
    echo "🔑 Enter Telegram API HASH (input hidden):"
    read -rs TELEGRAM_API_HASH
    echo
else
    TELEGRAM_API_ID=""
    TELEGRAM_API_HASH=""
fi

# ── Generate secrets ──────────────────────────────────────────────────────────
echo "⚙️  Generating secrets and creating .env..."
DJANGO_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
SERVER_IP=$(detect_lan_ip)

# ── Write .env ────────────────────────────────────────────────────────────────
# SECURITY: this file contains credentials — never commit it to version control.
cat > .env <<EOF
DEBUG=False
SECRET_KEY=$DJANGO_SECRET_KEY
ALLOWED_HOSTS=localhost,127.0.0.1${SERVER_IP:+,$SERVER_IP}
TELEGRAM_API_ID=$TELEGRAM_API_ID
TELEGRAM_API_HASH=$TELEGRAM_API_HASH
XRAY_PATH=./xray
CELERY_BROKER_URL=redis://localhost:6379/0
FIELD_ENCRYPTION_KEY=$FERNET_KEY
RANDOM_PORT=$RANDOM_PORT
EOF
# Holds SECRET_KEY, FIELD_ENCRYPTION_KEY and Telegram creds — restrict to owner.
chmod 600 .env

# ── Django setup ──────────────────────────────────────────────────────────────
touch db.sqlite3
chmod 600 db.sqlite3  # may hold decrypted node data — restrict to owner

echo "📦 Running migrations..."
python manage.py migrate

# Set the password via DJANGO_SUPERUSER_PASSWORD (honored by --noinput) rather
# than `shell -c`, so the plaintext never appears in `ps`/argv or shell history.
echo "👤 Creating superuser..."
DJANGO_SUPERUSER_PASSWORD="$DJANGO_SUPERPASS" \
    python manage.py createsuperuser --noinput \
    --username "$DJANGO_SUPERUSER" --email admin@example.com

echo "📦 Collecting static files..."
python manage.py collectstatic --noinput

# ── Download xray binary ──────────────────────────────────────────────────────
echo "⬇️  Downloading xray-core binary..."
XRAY_VERSION=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest \
    | python -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
case "$PLATFORM" in
    linux)
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64)  XRAY_ZIP="Xray-linux-64.zip" ;;
            aarch64) XRAY_ZIP="Xray-linux-arm64-v8a.zip" ;;
            armv7l)  XRAY_ZIP="Xray-linux-arm32-v7a.zip" ;;
            *) echo "⚠️  Unsupported Linux arch: $ARCH — place xray binary manually at $PROJECT_DIR/xray"; XRAY_ZIP="" ;;
        esac ;;
    macos)
        ARCH=$(uname -m)
        case "$ARCH" in
            arm64)  XRAY_ZIP="Xray-macos-arm64-v8a.zip" ;;
            x86_64) XRAY_ZIP="Xray-macos-64.zip" ;;
            *) echo "⚠️  Unsupported macOS arch: $ARCH — place xray binary manually at $PROJECT_DIR/xray"; XRAY_ZIP="" ;;
        esac ;;
    windows)
        XRAY_ZIP="Xray-windows-64.zip" ;;
esac

if [ -n "$XRAY_ZIP" ]; then
    curl -fsSL "https://github.com/XTLS/Xray-core/releases/download/$XRAY_VERSION/$XRAY_ZIP" \
        -o /tmp/xray_download.zip
    unzip -o /tmp/xray_download.zip xray -d "$PROJECT_DIR" 2>/dev/null \
        || unzip -o /tmp/xray_download.zip xray.exe -d "$PROJECT_DIR" 2>/dev/null
    chmod +x "$PROJECT_DIR/xray" 2>/dev/null || true
    rm -f /tmp/xray_download.zip
    echo "✅ xray $XRAY_VERSION installed"
fi

mkdir -p logs

# ── Service setup: Linux (systemd) ────────────────────────────────────────────
if [ "$PLATFORM" = "linux" ]; then
    echo "🚀 Installing systemd services..."

    sudo bash -c "cat > /etc/systemd/system/scanner-gunicorn.service" <<EOF
[Unit]
Description=Scanner Gunicorn Service
After=network.target

[Service]
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=DJANGO_SETTINGS_MODULE=$DJANGO_MODULE.settings
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/gunicorn $DJANGO_MODULE.wsgi:application --bind 0.0.0.0:$RANDOM_PORT --log-level debug --timeout 600
StandardOutput=append:$PROJECT_DIR/logs_web.out
StandardError=append:$PROJECT_DIR/logs_web.out
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    sudo bash -c "cat > /etc/systemd/system/scanner-celery.service" <<EOF
[Unit]
Description=Scanner Celery Worker Service
After=network.target

[Service]
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=DJANGO_SETTINGS_MODULE=$DJANGO_MODULE.settings
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/celery -A $DJANGO_MODULE worker --loglevel=info
StandardOutput=append:$PROJECT_DIR/logs_celery.out
StandardError=append:$PROJECT_DIR/logs_celery.out
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    sudo bash -c "cat > /etc/systemd/system/scanner-celery-beat.service" <<EOF
[Unit]
Description=Scanner Celery Beat Service
After=network.target

[Service]
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=DJANGO_SETTINGS_MODULE=$DJANGO_MODULE.settings
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/celery -A $DJANGO_MODULE beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
StandardOutput=append:$PROJECT_DIR/logs_beat.out
StandardError=append:$PROJECT_DIR/logs_beat.out
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable scanner-gunicorn scanner-celery scanner-celery-beat
    sudo systemctl restart scanner-gunicorn scanner-celery scanner-celery-beat

    # Persist project path + port so helper commands can find them
    sudo bash -c "echo '$PROJECT_DIR' > /usr/local/scanner_project_path"
    sudo bash -c "echo '$RANDOM_PORT' > /usr/local/scanner_project_port"

    # ── Helper commands ───────────────────────────────────────────────────────
    sudo bash -c "cat > /usr/local/bin/scanner-logs" <<'EOF'
#!/bin/bash
dir=$(cat /usr/local/scanner_project_path)
case "$1" in
  web)    tail -f "$dir/logs_web.out" ;;
  celery) tail -f "$dir/logs_celery.out" ;;
  beat)   tail -f "$dir/logs_beat.out" ;;
  *)      echo "Usage: scanner-logs [web|celery|beat]" ;;
esac
EOF

    sudo bash -c "cat > /usr/local/bin/scanner-restart" <<'EOF'
#!/bin/bash
sudo systemctl restart scanner-gunicorn scanner-celery scanner-celery-beat
echo "✅ Services restarted"
EOF

    sudo bash -c "cat > /usr/local/bin/scanner-stop" <<'EOF'
#!/bin/bash
sudo systemctl stop scanner-gunicorn scanner-celery scanner-celery-beat
echo "✅ Services stopped"
EOF

    sudo bash -c "cat > /usr/local/bin/scanner-manage" <<'EOF'
#!/bin/bash
dir=$(cat /usr/local/scanner_project_path)
venv="$dir/.venv"
cd "$dir" || exit
source "$venv/bin/activate"
python manage.py "$@"
EOF

    sudo bash -c "cat > /usr/local/bin/scanner-celery" <<'EOF'
#!/bin/bash
dir=$(cat /usr/local/scanner_project_path)
venv="$dir/.venv"
cd "$dir" || exit
source "$venv/bin/activate"
celery "$@"
EOF

    sudo bash -c "cat > /usr/local/bin/scanner-celery-task" <<'EOF'
#!/bin/bash
dir=$(cat /usr/local/scanner_project_path)
venv="$dir/.venv"
cd "$dir" || exit
source "$venv/bin/activate"
echo "Fetching available Celery tasks..."
celery -A config inspect registered | grep -oE "'[^']+'" | tr -d "'" | sort | uniq > /tmp/scanner_tasks_list.txt
if [ ! -s /tmp/scanner_tasks_list.txt ]; then
  echo "No tasks found or worker not running."
  exit 1
fi
echo "Available tasks:"
nl -w2 -s'. ' /tmp/scanner_tasks_list.txt
read -p "Select task number to run: " tasknum
task=$(sed -n "${tasknum}p" /tmp/scanner_tasks_list.txt)
if [ -z "$task" ]; then
  echo "Invalid selection."
  exit 1
fi
read -p "Enter arguments as a Python list (e.g. ['arg1', 2]) or leave blank: " args
if [ -z "$args" ]; then
  celery -A config call "$task"
else
  celery -A config call "$task" --args "$args"
fi
EOF

    sudo chmod +x /usr/local/bin/scanner-{logs,restart,stop,manage,celery,celery-task}

# ── Service setup: macOS (launchd LaunchAgents) ──────────────────────────────
elif [ "$PLATFORM" = "macos" ]; then
    echo "🚀 Installing launchd services..."
    AGENTS="$HOME/Library/LaunchAgents"
    mkdir -p "$AGENTS"
    # LaunchAgents start with a minimal PATH; point it at the venv, Homebrew and
    # the system binaries so gunicorn/celery and their subprocesses resolve.
    SVC_PATH="$VENV_DIR/bin:$(brew --prefix)/bin:/usr/bin:/bin:/usr/sbin:/sbin"

    cat > "$AGENTS/com.scanner.gunicorn.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.scanner.gunicorn</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/gunicorn</string>
        <string>$DJANGO_MODULE.wsgi:application</string>
        <string>--bind</string>
        <string>0.0.0.0:$RANDOM_PORT</string>
        <string>--timeout</string>
        <string>600</string>
        <string>--access-logfile</string>
        <string>$PROJECT_DIR/logs_web.out</string>
        <string>--error-logfile</string>
        <string>$PROJECT_DIR/logs_web.out</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DJANGO_SETTINGS_MODULE</key><string>$DJANGO_MODULE.settings</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
        <key>PATH</key><string>$SVC_PATH</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

    cat > "$AGENTS/com.scanner.celery.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.scanner.celery</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/celery</string>
        <string>-A</string>
        <string>$DJANGO_MODULE</string>
        <string>worker</string>
        <string>--loglevel=info</string>
        <string>--logfile</string>
        <string>$PROJECT_DIR/logs_celery.out</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DJANGO_SETTINGS_MODULE</key><string>$DJANGO_MODULE.settings</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
        <key>PATH</key><string>$SVC_PATH</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

    cat > "$AGENTS/com.scanner.celery-beat.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.scanner.celery-beat</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/celery</string>
        <string>-A</string>
        <string>$DJANGO_MODULE</string>
        <string>beat</string>
        <string>--loglevel=info</string>
        <string>--logfile</string>
        <string>$PROJECT_DIR/logs_beat.out</string>
        <string>--scheduler</string>
        <string>django_celery_beat.schedulers:DatabaseScheduler</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DJANGO_SETTINGS_MODULE</key><string>$DJANGO_MODULE.settings</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
        <key>PATH</key><string>$SVC_PATH</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

    # (Re)load each agent. unload first so re-running the installer is idempotent.
    for label in com.scanner.gunicorn com.scanner.celery com.scanner.celery-beat; do
        launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
        launchctl load -w "$AGENTS/$label.plist" || \
            echo "⚠️  Could not load $label — start it later with: launchctl load -w $AGENTS/$label.plist"
    done

# ── Service setup: Windows (nohup) ───────────────────────────────────────────
else
    echo "🚀 Starting services in background..."
    nohup python manage.py runserver "0.0.0.0:$RANDOM_PORT" > logs_web.out 2>&1 &
    nohup celery -A "$DJANGO_MODULE" worker --loglevel=info > logs_celery.out 2>&1 &
    nohup celery -A "$DJANGO_MODULE" beat --loglevel=info \
        --scheduler django_celery_beat.schedulers:DatabaseScheduler > logs_beat.out 2>&1 &
    echo "ℹ️  For production on Windows, replace runserver with Waitress or Daphne."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
# Fall back to localhost so the printed URL is always reachable, even when the
# LAN IP can't be detected (e.g. no active interface).
DISPLAY_HOST="${SERVER_IP:-localhost}"
echo ""
echo "✅ Installation complete!"
echo "🌐 Admin panel:    http://$DISPLAY_HOST:$RANDOM_PORT/admin/"
echo "   (or http://localhost:$RANDOM_PORT/admin/)"
echo "👤 Admin username: $DJANGO_SUPERUSER"
echo "🔑 Admin password: $DJANGO_SUPERPASS"
echo ""

if [ "$PLATFORM" = "linux" ]; then
    echo "💬 Helper commands:"
    echo "  scanner-logs [web|celery|beat]   → tail logs"
    echo "  scanner-restart                  → restart all services"
    echo "  scanner-stop                     → stop all services"
    echo "  scanner-manage <cmd>             → python manage.py <cmd>"
    echo "  scanner-celery <args>            → celery <args>"
    echo "  scanner-celery-task              → pick and trigger a task interactively"
    echo ""
    echo "  sudo systemctl status scanner-gunicorn"
    echo "  sudo systemctl status scanner-celery"
    echo "  sudo systemctl status scanner-celery-beat"
elif [ "$PLATFORM" = "macos" ]; then
    echo "💬 Manage services (launchd — <svc> is gunicorn, celery, or celery-beat):"
    echo "  launchctl list | grep com.scanner                                  → status"
    echo "  launchctl unload ~/Library/LaunchAgents/com.scanner.<svc>.plist    → stop"
    echo "  launchctl load -w ~/Library/LaunchAgents/com.scanner.<svc>.plist   → start"
    echo "  brew services list                                                 → redis status"
    echo ""
    echo "  tail -F $PROJECT_DIR/logs_web.out     → web server"
    echo "  tail -F $PROJECT_DIR/logs_celery.out  → celery worker"
    echo "  tail -F $PROJECT_DIR/logs_beat.out    → celery beat"
else
    echo "💬 Logs:"
    echo "  tail -f logs_web.out     → web server"
    echo "  tail -f logs_celery.out  → celery worker"
    echo "  tail -f logs_beat.out    → celery beat"
fi
