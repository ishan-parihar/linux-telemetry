# App Telemetry

Production-grade application usage telemetry for Linux. Tracks window activity, browser history, KDE app directories, process launches, and desktop file executions with minimal system impact.

## Features

- **Window Tracking** — Active window detection via X11 (works for XWayland apps on Wayland)
- **Browser History** — Tracks Firefox, Chrome, Brave, Chromium, Vivaldi, Edge history (safe DB access with timeouts)
- **KDE Directory Tracking** — Monitors Dolphin, Konsole, Kate active paths via D-Bus
- **Process Tracking** — Records new process launches with cmdline, cwd, user
- **Desktop Launch Tracking** — Monitors `.desktop` file executions
- **Idle Detection** — Configurable idle threshold (default 5 min)
- **Data Export** — JSON/CSV export with filtering
- **CLI Analysis** — Rich terminal reports with tables

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    app-telemetry-daemon                      │
├──────────────┬────────────────┬──────────────┬──────────────┤
│  Window      │  Browser       │  KDE Dirs    │  Processes   │
│  Tracker     │  History       │  Tracker     │  Tracker     │
│  (X11)       │  (SQLite copy) │  (D-Bus)     │  (/proc)     │
└──────┬───────┴───────┬────────┴──────┬────────┴──────┬──────┘
       │               │               │               │
       └───────────────┼───────────────┼───────────────┘
                       ▼
              ┌────────────────┐
              │  SQLite (WAL)  │
              │  ~/.local/...  │
              └───────┬────────┘
                      │
              ┌───────┴────────┐
              ▼                ▼
       ┌────────────┐   ┌────────────┐
       │   CLI      │   │  External  │
       │  Reports   │   │  Tools     │
       └────────────┘   └────────────┘
```

## Installation

### Quick Install

```bash
git clone https://github.com/ishanp/app-telemetry.git
cd app-telemetry
./scripts/install.sh
```

### Manual Install

```bash
# Install dependencies
sudo apt install python3 xdotool x11-utils qdbus  # Debian/Ubuntu
sudo pacman -S python xdotool xorg-xprop qdbus    # Arch

# Install Python package
pip install --user tabulate

# Run installer
./scripts/install.sh
```

### From PyPI (future)

```bash
pipx install app-telemetry
```

## Usage

### CLI Commands

```bash
# Usage summary (last 7 days)
app-telemetry summary

# Top 10 apps, last 30 days
app-telemetry summary --days 30 --limit 10

# Timeline of app switches
app-telemetry timeline --limit 50

# Daily breakdown
app-telemetry daily --days 14

# Browser history
app-telemetry browser --days 7 --limit 20

# KDE directory tracking
app-telemetry kde --days 7

# Process events
app-telemetry processes --days 1

# Statistics
app-telemetry stats

# Export data
app-telemetry export --format json -o data.json
app-telemetry export --format csv -o data.csv

# Cleanup old data
app-telemetry cleanup --days 90
```

### Service Management

```bash
# Status
systemctl --user status app-telemetry

# Logs
journalctl --user -u app-telemetry -f

# Restart
systemctl --user restart app-telemetry

# Stop
systemctl --user stop app-telemetry
```

## Configuration

Config file: `~/.config/app-telemetry/config.json`

```json
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
```

| Option | Default | Description |
|--------|---------|-------------|
| `poll_interval` | 2.0 | Main loop sleep (seconds) |
| `idle_threshold` | 300 | Idle detection threshold (seconds) |
| `track_x11` | true | Enable X11 window tracking |
| `track_browser_history` | true | Enable browser history tracking |
| `track_kde_directories` | true | Enable KDE D-Bus tracking |
| `track_processes` | true | Enable process scanning |
| `browser_history_interval` | 300 | Browser history check interval (seconds) |
| `kde_directory_interval` | 30 | KDE directory check interval (seconds) |
| `process_scan_interval` | 60 | Process scan interval (seconds) |
| `retention_days` | 90 | Data retention (days) |
| `browser_db_copy_timeout` | 5.0 | Timeout for browser DB copy (seconds) |

## Data Schema

### `window_events`
| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | REAL | Unix timestamp |
| `app_name` | TEXT | Normalized app name |
| `window_title` | TEXT | Window title |
| `app_class` | TEXT | X11 WM_CLASS |
| `pid` | INTEGER | Process ID |
| `executable` | TEXT | Executable path |
| `duration_seconds` | REAL | Time spent in window |
| `is_idle` | BOOLEAN | User was idle |
| `source` | TEXT | Tracker source |

### `browser_history`
| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | REAL | Visit timestamp |
| `browser` | TEXT | Browser name |
| `url` | TEXT | Visited URL |
| `title` | TEXT | Page title |
| `visit_count` | INTEGER | Visit count |
| `transition_type` | TEXT | Transition type |

### `kde_directory_events`
| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | REAL | Event timestamp |
| `app` | TEXT | App name (dolphin/konsole/kate) |
| `tab_id` | TEXT | Tab/session identifier |
| `path` | TEXT | Directory/file path |
| `path_type` | TEXT | directory/file |
| `duration_seconds` | REAL | Time spent |

### `process_events`
| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | REAL | Launch timestamp |
| `pid` | INTEGER | Process ID |
| `ppid` | INTEGER | Parent PID |
| `executable` | TEXT | Executable name |
| `cmdline` | TEXT | Command line |
| `cwd` | TEXT | Working directory |
| `user` | TEXT | Username |

## Performance

- **CPU**: ~0.1% average (2s poll interval)
- **Memory**: ~5-10 MB RSS
- **Disk I/O**: Minimal (batched writes, WAL mode)
- **Browser Impact**: Safe DB copying with 5s timeout, no browser locks

## Security

- Runs as user (no root)
- Systemd hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`
- Read-only access to browser databases (copied, not opened directly)
- No network access
- File lock prevents multiple instances

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src tests

# Type check
mypy src
```

## License

MIT License - see LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a PR

## Related Projects

- [ActivityWatch](https://activitywatch.net/) — Cross-platform time tracking
- [RescueTime](https://www.rescuetime.com/) — Commercial productivity tracking
- [Wakatime](https://wakatime.com/) — Developer-focused time tracking
