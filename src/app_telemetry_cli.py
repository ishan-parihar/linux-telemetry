#!/usr/bin/env python3
"""
App Usage Telemetry CLI
Query and analyze app usage data.
"""
import sqlite3
import json
import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from tabulate import tabulate

DB_PATH = Path.home() / ".local/share/app-telemetry/usage.db"

def get_db():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Is the daemon running?")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def cmd_summary(args):
    """Show usage summary"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    query = f"""
        SELECT 
            app_name,
            SUM(duration_seconds) as total_seconds,
            COUNT(*) as window_count,
            MIN(datetime(timestamp, 'unixepoch')) as first_seen,
            MAX(datetime(timestamp, 'unixepoch')) as last_seen
        FROM window_events
        {where}
        AND is_idle = 0
        GROUP BY app_name
        ORDER BY total_seconds DESC
        LIMIT ?
    """
    params = params + (args.limit,)
    
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        print("No data found.")
        return
    
    headers = ["App", "Total Time", "Windows", "First Seen", "Last Seen"]
    table = []
    for row in rows:
        app, secs, count, first, last = row
        hours = secs / 3600
        if hours >= 1:
            time_str = f"{hours:.1f}h"
        else:
            time_str = f"{secs/60:.0f}m"
        table.append([app, time_str, count, first, last])
    
    print(f"\n📊 App Usage Summary (last {args.days or 'all'} days, top {args.limit})")
    print(tabulate(table, headers=headers, tablefmt="rounded_grid"))
    
    total_query = f"SELECT SUM(duration_seconds) FROM window_events {where} AND is_idle = 0"
    total_secs = conn.execute(total_query, params[:-1]).fetchone()[0] or 0
    print(f"\n⏱️  Total Active Time: {total_secs/3600:.1f} hours")

def cmd_timeline(args):
    """Show timeline of app usage"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    query = f"""
        SELECT 
            datetime(timestamp, 'unixepoch') as time,
            app_name,
            duration_seconds,
            window_title,
            is_idle
        FROM window_events
        {where}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params = params + (args.limit,)
    
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        print("No data found.")
        return
    
    headers = ["Time", "App", "Duration", "Window Title", "Idle"]
    table = []
    for row in rows:
        time_str, app, secs, title, idle = row
        dur = f"{secs:.0f}s" if secs < 60 else f"{secs/60:.1f}m"
        table.append([time_str, app, dur, title[:60] if title else "", "💤" if idle else ""])
    
    print(f"\n📅 Timeline (last {args.days or 'all'} days, latest {args.limit})")
    print(tabulate(table, headers=headers, tablefmt="rounded_grid"))

def cmd_daily(args):
    """Show daily breakdown"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    query = f"""
        SELECT 
            date(timestamp, 'unixepoch') as day,
            app_name,
            SUM(duration_seconds) as total_seconds
        FROM window_events
        {where}
        AND is_idle = 0
        GROUP BY day, app_name
        ORDER BY day DESC, total_seconds DESC
    """
    
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        print("No data found.")
        return
    
    from collections import defaultdict
    daily = defaultdict(list)
    for day, app, secs in rows:
        daily[day].append((app, secs))
    
    print(f"\n📆 Daily Breakdown (last {args.days or 'all'} days)")
    for day in sorted(daily.keys(), reverse=True):
        apps = daily[day]
        total = sum(s for _, s in apps)
        print(f"\n  {day} — {total/3600:.1f}h total")
        for app, secs in apps[:10]:
            print(f"    {app}: {secs/3600:.1f}h ({secs/total*100:.0f}%)")

def cmd_apps(args):
    """List all tracked apps"""
    conn = get_db()
    
    rows = conn.execute("""
        SELECT DISTINCT app_name, COUNT(*) as count, SUM(duration_seconds) as total_secs
        FROM window_events
        WHERE is_idle = 0
        GROUP BY app_name
        ORDER BY total_secs DESC
    """).fetchall()
    
    if not rows:
        print("No apps tracked yet.")
        return
    
    headers = ["App", "Sessions", "Total Time"]
    table = [[app, count, f"{secs/3600:.1f}h"] for app, count, secs in rows]
    print(tabulate(table, headers=headers, tablefmt="rounded_grid"))

def cmd_idle(args):
    """Show idle time analysis"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    active = conn.execute(f"SELECT SUM(duration_seconds) FROM window_events {where} AND is_idle = 0", params).fetchone()[0] or 0
    idle = conn.execute(f"SELECT SUM(duration_seconds) FROM window_events {where} AND is_idle = 1", params).fetchone()[0] or 0
    total = active + idle
    
    print(f"\n💤 Idle Analysis (last {args.days or 'all'} days)")
    print(f"  Active:   {active/3600:.1f}h ({active/total*100:.1f}%)")
    print(f"  Idle:     {idle/3600:.1f}h ({idle/total*100:.1f}%)")
    print(f"  Total:    {total/3600:.1f}h")
    
    rows = conn.execute(f"""
        SELECT app_name, SUM(duration_seconds) as idle_secs
        FROM window_events
        {where}
        AND is_idle = 1
        GROUP BY app_name
        ORDER BY idle_secs DESC
        LIMIT 10
    """, params).fetchall()
    
    if rows:
        print("\n  Top apps during idle:")
        for app, secs in rows:
            print(f"    {app}: {secs/60:.0f}m")

def cmd_browser(args):
    """Show browser history"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    query = f"""
        SELECT 
            datetime(timestamp, 'unixepoch') as time,
            browser,
            url,
            title,
            visit_count
        FROM browser_history
        {where}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params = params + (args.limit,)
    
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        print("No browser history data.")
        return
    
    headers = ["Time", "Browser", "URL", "Title", "Visits"]
    table = []
    for row in rows:
        time_str, browser, url, title, visits = row
        table.append([time_str, browser, url[:80], title[:40] if title else "", visits])
    
    print(f"\n🌐 Browser History (last {args.days or 'all'} days, latest {args.limit})")
    print(tabulate(table, headers=headers, tablefmt="rounded_grid"))

def cmd_kde(args):
    """Show KDE directory tracking"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    query = f"""
        SELECT 
            datetime(timestamp, 'unixepoch') as time,
            app,
            path,
            path_type,
            duration_seconds
        FROM kde_directory_events
        {where}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params = params + (args.limit,)
    
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        print("No KDE directory data.")
        return
    
    headers = ["Time", "App", "Path", "Type", "Duration"]
    table = []
    for row in rows:
        time_str, app, path, ptype, dur = row
        table.append([time_str, app, path[:60], ptype, f"{dur:.0f}s"])
    
    print(f"\n📁 KDE Directory Tracking (last {args.days or 'all'} days, latest {args.limit})")
    print(tabulate(table, headers=headers, tablefmt="rounded_grid"))

def cmd_processes(args):
    """Show process events"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    query = f"""
        SELECT 
            datetime(timestamp, 'unixepoch') as time,
            pid,
            executable,
            cmdline,
            cwd
        FROM process_events
        {where}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params = params + (args.limit,)
    
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        print("No process data.")
        return
    
    headers = ["Time", "PID", "Executable", "Cmdline", "CWD"]
    table = []
    for row in rows:
        time_str, pid, exe, cmd, cwd = row
        table.append([time_str, pid, exe, cmd[:60] if cmd else "", cwd[:40] if cwd else ""])
    
    print(f"\n⚙️  Process Events (last {args.days or 'all'} days, latest {args.limit})")
    print(tabulate(table, headers=headers, tablefmt="rounded_grid"))

def cmd_export(args):
    """Export data to JSON/CSV"""
    conn = get_db()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).timestamp()
        where = "WHERE timestamp >= ?"
        params = (since,)
    else:
        where = ""
        params = ()
    
    tables = args.tables.split(',') if args.tables else ['window_events', 'browser_history', 'kde_directory_events', 'process_events']
    
    data = {}
    for table in tables:
        query = f"SELECT * FROM {table} {where} ORDER BY timestamp"
        rows = conn.execute(query, params).fetchall()
        data[table] = [dict(row) for row in rows]
    
    if args.format == 'json':
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"Exported to {args.output}")
        else:
            print(json.dumps(data, indent=2, default=str))
    else:
        import csv
        if args.output:
            with open(args.output, 'w', newline='') as f:
                writer = csv.writer(f)
                for table, rows in data.items():
                    if rows:
                        writer.writerow([f"# {table}"])
                        writer.writerow(rows[0].keys())
                        for row in rows:
                            writer.writerow(row.values())
            print(f"Exported to {args.output}")
        else:
            writer = csv.writer(sys.stdout)
            for table, rows in data.items():
                if rows:
                    writer.writerow([f"# {table}"])
                    writer.writerow(rows[0].keys())
                    for row in rows:
                        writer.writerow(row.values())

def cmd_cleanup(args):
    """Clean old data"""
    conn = get_db()
    
    if args.days:
        cutoff = (datetime.now() - timedelta(days=args.days)).timestamp()
        tables = ['window_events', 'browser_history', 'kde_directory_events', 'process_events', 'desktop_launches']
        total = 0
        for table in tables:
            deleted = conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,)).rowcount
            total += deleted
        conn.commit()
        print(f"Deleted {total} old records (older than {args.days} days)")
    else:
        print("Specify --days to clean data older than N days")

def cmd_stats(args):
    """Show database statistics"""
    conn = get_db()
    
    tables = ['window_events', 'browser_history', 'kde_directory_events', 'process_events', 'desktop_launches', 'app_sessions']
    
    print("\n📈 Database Statistics")
    print("=" * 50)
    
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count:,} records")
    
    # Date range
    first = conn.execute("SELECT MIN(timestamp) FROM window_events").fetchone()[0]
    last = conn.execute("SELECT MAX(timestamp) FROM window_events").fetchone()[0]
    if first and last:
        print(f"\n  Date range: {datetime.fromtimestamp(first).date()} to {datetime.fromtimestamp(last).date()}")
    
    # DB size
    size = DB_PATH.stat().st_size
    print(f"  DB size: {size/1024/1024:.1f} MB")

def main():
    parser = argparse.ArgumentParser(description="App Usage Telemetry CLI")
    parser.add_argument("--days", type=int, help="Limit to last N days")
    parser.add_argument("--limit", type=int, default=20, help="Limit results")
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    p = subparsers.add_parser("summary", help="Show usage summary")
    p.set_defaults(func=cmd_summary)
    
    p = subparsers.add_parser("timeline", help="Show timeline of app switches")
    p.set_defaults(func=cmd_timeline)
    
    p = subparsers.add_parser("daily", help="Show daily breakdown")
    p.set_defaults(func=cmd_daily)
    
    p = subparsers.add_parser("apps", help="List all tracked apps")
    p.set_defaults(func=cmd_apps)
    
    p = subparsers.add_parser("idle", help="Show idle time analysis")
    p.set_defaults(func=cmd_idle)
    
    p = subparsers.add_parser("browser", help="Show browser history")
    p.set_defaults(func=cmd_browser)
    
    p = subparsers.add_parser("kde", help="Show KDE directory tracking")
    p.set_defaults(func=cmd_kde)
    
    p = subparsers.add_parser("processes", help="Show process events")
    p.set_defaults(func=cmd_processes)
    
    p = subparsers.add_parser("export", help="Export data to JSON/CSV")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("-o", "--output", help="Output file")
    p.add_argument("--tables", help="Comma-separated list of tables to export")
    p.set_defaults(func=cmd_export)
    
    p = subparsers.add_parser("cleanup", help="Clean old data")
    p.add_argument("--days", type=int, required=True, help="Delete data older than N days")
    p.set_defaults(func=cmd_cleanup)
    
    p = subparsers.add_parser("stats", help="Show database statistics")
    p.set_defaults(func=cmd_stats)
    
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
