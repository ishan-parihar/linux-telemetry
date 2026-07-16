#!/usr/bin/env bash
set -euo pipefail

# App Telemetry Installer
# Installs the daemon, CLI, systemd service, and config

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PREFIX="${PREFIX:-/usr/local}"
BINDIR="${BINDIR:-$PREFIX/bin}"
SYSTEMD_USER_DIR="${SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"

echo "📦 Installing app-telemetry..."

# Check dependencies
echo "🔍 Checking dependencies..."
for cmd in python3 xdotool xprop qdbus; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "⚠️  Warning: $cmd not found (may be needed for full functionality)"
    fi
done

# Check Python packages
python3 -c "import tabulate" 2>/dev/null || {
    echo "📥 Installing tabulate..."
    pip install --user tabulate
}

# Install binaries
echo "📝 Installing binaries to $BINDIR..."
install -d "$BINDIR"
install -m 755 "$PROJECT_ROOT/src/app_telemetry_daemon.py" "$BINDIR/app-telemetry-daemon"
install -m 755 "$PROJECT_ROOT/src/app_telemetry_cli.py" "$BINDIR/app-telemetry"

# Install systemd service
echo "⚙️  Installing systemd service..."
install -d "$SYSTEMD_USER_DIR"
install -m 644 "$PROJECT_ROOT/systemd/app-telemetry.service" "$SYSTEMD_USER_DIR/"

# Create default config
CONFIG_DIR="$HOME/.config/app-telemetry"
install -d "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
    echo "📄 Creating default config..."
    cat > "$CONFIG_DIR/config.json" << 'CONFIGEOF'
{
  "poll_interval": 2.0,
  "idle_threshold": 300,
  "track_x11": true,
  "track_browser_history": true,
  "track_kde_directories": true,
  "track_processes": true,
  "track_desktop_launches": true,
  "browser_history_interval": 300,
  "kde_directory_interval": 30,
  "process_scan_interval": 60,
  "retention_days": 90,
  "batch_size": 50,
  "flush_interval": 10.0,
  "browser_db_copy_timeout": 5.0
}
CONFIGEOF
fi

# Create data directory
install -d "$HOME/.local/share/app-telemetry"

# Reload systemd
systemctl --user daemon-reload

# Enable and start service
echo "🚀 Enabling and starting service..."
systemctl --user enable --now app-telemetry

echo ""
echo "✅ Installation complete!"
echo ""
echo "📋 Usage:"
echo "  app-telemetry summary       # Usage summary"
echo "  app-telemetry timeline      # App switch timeline"
echo "  app-telemetry daily         # Daily breakdown"
echo "  app-telemetry browser       # Browser history"
echo "  app-telemetry kde           # KDE directory tracking"
echo "  app-telemetry processes     # Process events"
echo "  app-telemetry stats         # Statistics"
echo "  app-telemetry export -o data.json  # Export data"
echo ""
echo "🔧 Service management:"
echo "  systemctl --user status app-telemetry"
echo "  journalctl --user -u app-telemetry -f"
echo "  systemctl --user restart app-telemetry"
echo ""
echo "📁 Config: $CONFIG_DIR/config.json"
echo "💾 Data:   ~/.local/share/app-telemetry/usage.db"
