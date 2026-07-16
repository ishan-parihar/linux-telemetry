#!/usr/bin/env python3
"""
Enhanced App Usage Telemetry Daemon
- Window tracking (X11 for XWayland apps)
- Browser history tracking (Firefox, Chrome, Brave, etc.) - with safe DB access
- KDE app directory tracking (Dolphin, Konsole, Kate, etc.)
- Process tracking
- Desktop launch tracking
"""
import sqlite3
import time
import json
import subprocess
import os
import signal
import sys
import glob
import threading
from datetime import datetime
from pathlib import Path
from threading import Lock
import atexit
import shutil
import tempfile
import fcntl

DB_PATH = Path.home() / ".local/share/app-telemetry/usage.db"
CONFIG_PATH = Path.home() / ".config/app-telemetry/config.json"
LOG_PATH = Path.home() / ".local/share/app-telemetry/daemon.log"
LOCK_PATH = Path.home() / ".local/share/app-telemetry/daemon.lock"

DEFAULT_CONFIG = {
    "poll_interval": 2.0,
    "idle_threshold": 300,
    "track_x11": True,
    "track_browser_history": True,
    "track_kde_directories": True,
    "track_processes": True,
    "track_desktop_launches": True,
    "browser_history_interval": 300,  # 5 minutes - reduced frequency
    "kde_directory_interval": 30,
    "process_scan_interval": 60,
    "retention_days": 90,
    "batch_size": 50,
    "flush_interval": 10.0,
    "browser_db_copy_timeout": 5.0,  # timeout for DB copy
}

class FileLock:
    """Simple file-based lock to prevent multiple daemon instances"""
    def __init__(self, path):
        self.path = path
        self.fd = None
    
    def acquire(self):
        try:
            self.fd = open(self.path, 'w')
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd.write(str(os.getpid()))
            self.fd.flush()
            return True
        except (IOError, OSError):
            return False
    
    def release(self):
        if self.fd:
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
            self.fd.close()
            self.fd = None
            try:
                self.path.unlink()
            except:
                pass

class AppTelemetry:
    def __init__(self):
        self.lock = FileLock(LOCK_PATH)
        if not self.lock.acquire():
            print("Another instance is already running", file=sys.stderr)
            sys.exit(1)
        
        self.db_lock = Lock()
        self.pending_events = []
        self.running = False
        self.last_active_window = None
        self.last_active_time = time.time()
        self.idle = False
        self.config = self.load_config()
        self.init_db()
        self.setup_signal_handlers()
        self.browser_history_last_check = {}
        self.kde_tabs_last_state = {}
        self.last_browser_check = 0
        self.last_kde_check = 0
        self.last_process_scan = 0
        self.known_pids = set()
        
    def load_config(self):
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG
    
    def init_db(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS window_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    window_title TEXT,
                    app_name TEXT,
                    app_class TEXT,
                    pid INTEGER,
                    executable TEXT,
                    duration_seconds REAL,
                    is_idle BOOLEAN DEFAULT 0,
                    source TEXT DEFAULT 'window_tracker'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS browser_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    browser TEXT,
                    url TEXT,
                    title TEXT,
                    visit_count INTEGER DEFAULT 1,
                    transition_type TEXT,
                    source TEXT DEFAULT 'browser_history'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kde_directory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    app TEXT,
                    tab_id TEXT,
                    path TEXT,
                    path_type TEXT,
                    duration_seconds REAL,
                    source TEXT DEFAULT 'kde_tracker'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS process_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    pid INTEGER,
                    ppid INTEGER,
                    executable TEXT,
                    cmdline TEXT,
                    cwd TEXT,
                    user TEXT,
                    source TEXT DEFAULT 'process_tracker'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS desktop_launches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    desktop_file TEXT,
                    exec_command TEXT,
                    app_name TEXT,
                    source TEXT DEFAULT 'desktop_tracker'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_name TEXT NOT NULL,
                    executable TEXT,
                    start_time REAL NOT NULL,
                    end_time REAL,
                    total_duration REAL,
                    active_duration REAL,
                    idle_duration REAL,
                    window_count INTEGER DEFAULT 0,
                    date DATE
                )
            """)
            for idx in [
                "CREATE INDEX IF NOT EXISTS idx_window_ts ON window_events(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_window_app ON window_events(app_name)",
                "CREATE INDEX IF NOT EXISTS idx_browser_ts ON browser_history(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_browser_browser ON browser_history(browser)",
                "CREATE INDEX IF NOT EXISTS idx_browser_url ON browser_history(url)",
                "CREATE INDEX IF NOT EXISTS idx_kde_ts ON kde_directory_events(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_kde_app ON kde_directory_events(app)",
                "CREATE INDEX IF NOT EXISTS idx_kde_path ON kde_directory_events(path)",
                "CREATE INDEX IF NOT EXISTS idx_process_ts ON process_events(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_process_exe ON process_events(executable)",
                "CREATE INDEX IF NOT EXISTS idx_session_app ON app_sessions(app_name)",
                "CREATE INDEX IF NOT EXISTS idx_session_date ON app_sessions(date)",
                "CREATE INDEX IF NOT EXISTS idx_desktop_ts ON desktop_launches(timestamp)",
            ]:
                conn.execute(idx)
    
    def setup_signal_handlers(self):
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        atexit.register(self.shutdown)
    
    def shutdown(self, *args):
        self.running = False
        self.flush_events()
        self.finalize_current_session()
        self.lock.release()
        sys.exit(0)
    
    # ========== WINDOW TRACKING (X11 for XWayland apps) ==========
    
    def get_active_window_x11(self):
        try:
            result = subprocess.run(['xdotool', 'getactivewindow'], capture_output=True, text=True, timeout=1)
            if result.returncode != 0:
                return None
            win_id = result.stdout.strip()
            if not win_id:
                return None
            
            title_result = subprocess.run(['xdotool', 'getwindowname', win_id], capture_output=True, text=True, timeout=1)
            title = title_result.stdout.strip() if title_result.returncode == 0 else ""
            
            class_result = subprocess.run(['xprop', '-id', win_id, 'WM_CLASS', '_NET_WM_PID'], capture_output=True, text=True, timeout=1)
            app_class = ""
            pid = None
            for line in class_result.stdout.split('\n'):
                line = line.strip()
                if 'WM_CLASS' in line and '=' in line:
                    parts = line.split('=', 1)[1].strip()
                    if parts.startswith('"'):
                        parts = parts[1:]
                        if '", "' in parts:
                            app_class = parts.split('", "')[1].rstrip('"')
                        elif parts.endswith('"'):
                            app_class = parts[:-1]
                elif '_NET_WM_PID' in line and '=' in line:
                    try:
                        pid = int(line.split('=')[1].strip())
                    except:
                        pass
            
            executable = ""
            if pid:
                try:
                    executable = os.readlink(f'/proc/{pid}/exe')
                except:
                    pass
            
            return {'title': title, 'app_class': app_class, 'pid': pid, 'executable': executable, 'source': 'x11'}
        except Exception as e:
            self.log(f"X11 window error: {e}")
            return None
    
    def get_active_window(self):
        if self.config.get('track_x11', True):
            if os.environ.get('DISPLAY'):
                result = self.get_active_window_x11()
                if result:
                    return result
        return None
    
    def normalize_app_name(self, window_info):
        title = window_info.get('title', '')
        app_class = window_info.get('app_class', '')
        executable = window_info.get('executable', '')
        
        if app_class:
            name = app_class.lower()
        elif executable:
            name = os.path.basename(executable).lower()
        elif title:
            parts = title.split(' - ')
            name = parts[-1].lower() if len(parts) > 1 else title.lower()[:50]
        else:
            name = "unknown"
        
        name_map = {
            'code': 'vscode', 'code-oss': 'vscode', 'cursor': 'cursor', 'trae': 'trae', 'zed': 'zed',
            'brave': 'brave', 'brave-browser': 'brave', 'firefox': 'firefox', 'chrome': 'chrome', 'chromium': 'chromium',
            'terminal': 'terminal', 'konsole': 'konsole', 'alacritty': 'alacritty', 'kitty': 'kitty', 'wezterm': 'wezterm',
            'spotify': 'spotify', 'discord': 'discord', 'vesktop': 'vesktop', 'telegram': 'telegram', 'slack': 'slack',
            'steam': 'steam', 'lutris': 'lutris', 'bottles': 'bottles', 'heroic': 'heroic',
            'obsidian': 'obsidian', 'notion': 'notion', 'postman': 'postman', 'insomnia': 'insomnia',
            'docker': 'docker', 'virt-manager': 'virt-manager', 'gimp': 'gimp', 'krita': 'krita', 'blender': 'blender',
            'vlc': 'vlc', 'mpv': 'mpv', 'dolphin': 'dolphin', 'kate': 'kate', 'kwrite': 'kwrite',
        }
        for key, val in name_map.items():
            if key in name:
                return val
        return name
    
    def track_window(self):
        window_info = self.get_active_window()
        now = time.time()
        
        if window_info:
            app_name = self.normalize_app_name(window_info)
            
            if self.last_active_window != app_name:
                if self.last_active_window:
                    duration = now - self.last_active_time
                    self.queue_event('window_events', {
                        'timestamp': self.last_active_time,
                        'window_title': window_info.get('title', ''),
                        'app_name': self.last_active_window,
                        'app_class': window_info.get('app_class', ''),
                        'pid': window_info.get('pid'),
                        'executable': window_info.get('executable', ''),
                        'duration_seconds': duration,
                        'is_idle': self.idle,
                        'source': window_info.get('source', 'unknown')
                    })
                self.last_active_window = app_name
                self.last_active_time = now
                self.idle = False
            elif not self.idle and (now - self.last_active_time) > self.config.get('idle_threshold', 300):
                self.idle = True
                self.log(f"User idle detected, current app: {app_name}")
    
    # ========== BROWSER HISTORY TRACKING (Safe, non-blocking) ==========
    
    def get_browser_history_paths(self):
        paths = []
        for profile in glob.glob(str(Path.home() / '.mozilla/firefox/*.default*/places.sqlite')):
            paths.append(('firefox', profile))
        brave_hist = Path.home() / '.config/BraveSoftware/Brave-Browser/Default/History'
        if brave_hist.exists():
            paths.append(('brave', str(brave_hist)))
        chrome_hist = Path.home() / '.config/google-chrome/Default/History'
        if chrome_hist.exists():
            paths.append(('chrome', str(chrome_hist)))
        chromium_hist = Path.home() / '.config/chromium/Default/History'
        if chromium_hist.exists():
            paths.append(('chromium', str(chromium_hist)))
        vivaldi_hist = Path.home() / '.config/vivaldi/Default/History'
        if vivaldi_hist.exists():
            paths.append(('vivaldi', str(vivaldi_hist)))
        edge_hist = Path.home() / '.config/microsoft-edge/Default/History'
        if edge_hist.exists():
            paths.append(('edge', str(edge_hist)))
        return paths
    
    def extract_browser_history(self, browser, db_path):
        """Safely extract browser history with timeout and retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
                    tmp_path = tmp.name
                
                # Use cp with timeout to avoid blocking on locked DB
                cp_result = subprocess.run(
                    ['cp', db_path, tmp_path],
                    capture_output=True,
                    timeout=self.config.get('browser_db_copy_timeout', 5.0)
                )
                if cp_result.returncode != 0:
                    raise RuntimeError(f"cp failed: {cp_result.stderr.decode()}")
                
                conn = sqlite3.connect(f'file:{tmp_path}?mode=ro', uri=True)
                cursor = conn.cursor()
                
                if browser == 'firefox':
                    cursor.execute("""
                        SELECT url, title, visit_count, last_visit_date/1000000 as last_visit
                        FROM moz_places
                        WHERE last_visit_date > 0
                        ORDER BY last_visit_date DESC
                        LIMIT 100
                    """)
                else:
                    cursor.execute("""
                        SELECT url, title, visit_count, (last_visit_time/1000000)-11644473600 as last_visit
                        FROM urls
                        WHERE last_visit_time > 0
                        ORDER BY last_visit_time DESC
                        LIMIT 100
                    """)
                
                rows = cursor.fetchall()
                conn.close()
                os.unlink(tmp_path)
                return rows
                
            except subprocess.TimeoutExpired:
                self.log(f"Browser DB copy timeout ({browser}), attempt {attempt+1}/{max_retries}")
                if tmp_path and os.path.exists(tmp_path):
                    try: os.unlink(tmp_path)
                    except: pass
                time.sleep(0.5 * (attempt + 1))
            except Exception as e:
                self.log(f"Browser history error ({browser}): {e}")
                if tmp_path and os.path.exists(tmp_path):
                    try: os.unlink(tmp_path)
                    except: pass
                break
        return []
    
    def track_browser_history(self):
        if not self.config.get('track_browser_history', True):
            return
        
        now = time.time()
        if now - self.last_browser_check < self.config.get('browser_history_interval', 300):
            return
        self.last_browser_check = now
        
        for browser, db_path in self.get_browser_history_paths():
            last_check = self.browser_history_last_check.get(browser, 0)
            rows = self.extract_browser_history(browser, db_path)
            
            new_entries = 0
            for url, title, visit_count, last_visit in rows:
                if last_visit > last_check:
                    self.queue_event('browser_history', {
                        'timestamp': last_visit,
                        'browser': browser,
                        'url': url,
                        'title': title or '',
                        'visit_count': visit_count or 1,
                        'transition_type': 'link',
                        'source': 'browser_history'
                    })
                    new_entries += 1
            
            if rows:
                self.browser_history_last_check[browser] = max(r[3] for r in rows)
                if new_entries:
                    self.log(f"Browser history: {new_entries} new entries from {browser}")
    
    # ========== KDE DIRECTORY TRACKING ==========
    
    def get_kde_app_tabs(self):
        tabs = []
        env = os.environ.copy()
        env['DBUS_SESSION_BUS_ADDRESS'] = f"unix:path=/run/user/{os.getuid()}/bus"
        
        # Dolphin
        try:
            result = subprocess.run(['qdbus', 'org.kde.dolphin', '/dolphin', 'org.kde.dolphin.DolphinView.currentUrl'], 
                                  capture_output=True, text=True, timeout=2, env=env)
            if result.returncode == 0 and result.stdout.strip():
                url = result.stdout.strip()
                if url.startswith('file://'):
                    tabs.append(('dolphin', 'main', url[7:], 'directory'))
        except:
            pass
        
        # Konsole
        try:
            result = subprocess.run(['qdbus', 'org.kde.konsole', '/Konsole', 'org.kde.konsole.Window.currentSession'], 
                                  capture_output=True, text=True, timeout=2, env=env)
            if result.returncode == 0 and result.stdout.strip():
                session = result.stdout.strip()
                cwd_result = subprocess.run(['qdbus', 'org.kde.konsole', session, 'org.kde.konsole.Session.currentDirectory'], 
                                          capture_output=True, text=True, timeout=2, env=env)
                if cwd_result.returncode == 0 and cwd_result.stdout.strip():
                    tabs.append(('konsole', session, cwd_result.stdout.strip(), 'directory'))
        except:
            pass
        
        # Kate
        try:
            result = subprocess.run(['qdbus', 'org.kde.kate', '/kate', 'org.kde.kate.KateDocumentManager.activeDocument'], 
                                  capture_output=True, text=True, timeout=2, env=env)
            if result.returncode == 0 and result.stdout.strip():
                doc = result.stdout.strip()
                url_result = subprocess.run(['qdbus', 'org.kde.kate', doc, 'org.kde.kate.KateDocument.url'], 
                                          capture_output=True, text=True, timeout=2, env=env)
                if url_result.returncode == 0 and url_result.stdout.strip():
                    url = url_result.stdout.strip()
                    if url.startswith('file://'):
                        tabs.append(('kate', doc, url[7:], 'file'))
        except:
            pass
        
        return tabs
    
    def track_kde_directories(self):
        if not self.config.get('track_kde_directories', True):
            return
        
        now = time.time()
        if now - self.last_kde_check < self.config.get('kde_directory_interval', 30):
            return
        self.last_kde_check = now
        
        current_tabs = self.get_kde_app_tabs()
        current_keys = set()
        
        for app, tab_id, path, path_type in current_tabs:
            key = f"{app}:{tab_id}:{path}"
            current_keys.add(key)
            
            if key not in self.kde_tabs_last_state:
                self.kde_tabs_last_state[key] = now
            else:
                duration = now - self.kde_tabs_last_state[key]
                self.queue_event('kde_directory_events', {
                    'timestamp': self.kde_tabs_last_state[key],
                    'app': app,
                    'tab_id': tab_id,
                    'path': path,
                    'path_type': path_type,
                    'duration_seconds': duration,
                    'source': 'kde_tracker'
                })
                self.kde_tabs_last_state[key] = now
        
        for key in list(self.kde_tabs_last_state.keys()):
            if key not in current_keys:
                app, tab_id, path = key.split(':', 2)
                duration = now - self.kde_tabs_last_state[key]
                self.queue_event('kde_directory_events', {
                    'timestamp': self.kde_tabs_last_state[key],
                    'app': app,
                    'tab_id': tab_id,
                    'path': path,
                    'path_type': 'directory',
                    'duration_seconds': duration,
                    'source': 'kde_tracker'
                })
                del self.kde_tabs_last_state[key]
    
    # ========== PROCESS TRACKING ==========
    
    def track_processes(self):
        if not self.config.get('track_processes', True):
            return
        
        now = time.time()
        if now - self.last_process_scan < self.config.get('process_scan_interval', 60):
            return
        self.last_process_scan = now
        
        try:
            current_pids = set()
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                current_pids.add(pid)
                
                if pid in self.known_pids:
                    continue
                
                try:
                    with open(proc_dir / 'stat') as f:
                        stat = f.read().split()
                    ppid = int(stat[3]) if len(stat) > 3 else 0
                    
                    with open(proc_dir / 'comm') as f:
                        comm = f.read().strip()
                    
                    cmdline = ""
                    try:
                        with open(proc_dir / 'cmdline') as f:
                            cmdline = f.read().replace('\x00', ' ').strip()
                    except:
                        pass
                    
                    cwd = ""
                    try:
                        cwd = os.readlink(proc_dir / 'cwd')
                    except:
                        pass
                    
                    user = ""
                    try:
                        import pwd
                        stat_info = proc_dir.stat()
                        user = pwd.getpwuid(stat_info.st_uid).pw_name
                    except:
                        pass
                    
                    self.queue_event('process_events', {
                        'timestamp': now,
                        'pid': pid,
                        'ppid': ppid,
                        'executable': comm,
                        'cmdline': cmdline,
                        'cwd': cwd,
                        'user': user,
                        'source': 'process_tracker'
                    })
                except:
                    pass
            
            self.known_pids = current_pids
        except Exception as e:
            self.log(f"Process tracking error: {e}")
    
    # ========== DESKTOP LAUNCH TRACKING ==========
    
    def track_desktop_launches(self):
        if not self.config.get('track_desktop_launches', True):
            return
        
        # Monitor .desktop file launches via systemd journal or dbus
        # This is a placeholder - would need integration with desktop environment
        pass
    
    # ========== EVENT QUEUE ==========
    
    def queue_event(self, table, data):
        with self.db_lock:
            self.pending_events.append((table, data))
            if len(self.pending_events) >= self.config.get('batch_size', 50):
                self.flush_events()
    
    def flush_events(self):
        with self.db_lock:
            if not self.pending_events:
                return
            events = self.pending_events[:]
            self.pending_events.clear()
        
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                for table, data in events:
                    cols = ', '.join(data.keys())
                    placeholders = ', '.join(['?'] * len(data))
                    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(data.values()))
                conn.commit()
        except Exception as e:
            self.log(f"DB flush error: {e}")
            with self.db_lock:
                self.pending_events.extend(events)
    
    def finalize_current_session(self):
        if self.last_active_window:
            now = time.time()
            duration = now - self.last_active_time
            self.queue_event('window_events', {
                'timestamp': self.last_active_time,
                'window_title': '',
                'app_name': self.last_active_window,
                'app_class': '',
                'pid': None,
                'executable': '',
                'duration_seconds': duration,
                'is_idle': self.idle,
                'source': 'finalize'
            })
            self.flush_events()
    
    def log(self, msg):
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    
    def run(self):
        self.running = True
        self.log("Enhanced app telemetry daemon started")
        last_flush = time.time()
        
        while self.running:
            try:
                self.track_window()
                self.track_browser_history()
                self.track_kde_directories()
                self.track_processes()
                
                if time.time() - last_flush > self.config.get('flush_interval', 10):
                    self.flush_events()
                    last_flush = time.time()
                
                time.sleep(self.config.get('poll_interval', 2.0))
            except Exception as e:
                self.log(f"Main loop error: {e}")
                time.sleep(5)
        
        self.log("Enhanced app telemetry daemon stopped")

def main():
    daemon = AppTelemetry()
    daemon.run()

if __name__ == '__main__':
    main()
