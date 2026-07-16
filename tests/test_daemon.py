#!/usr/bin/env python3
"""Tests for app-telemetry daemon"""
import pytest
import tempfile
import sqlite3
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app_telemetry_daemon import AppTelemetry, normalize_app_name


class TestNormalizeAppName:
    def test_vscode_variants(self):
        assert normalize_app_name({'app_class': 'code'}) == 'vscode'
        assert normalize_app_name({'app_class': 'code-oss'}) == 'vscode'
        assert normalize_app_name({'executable': '/usr/bin/code'}) == 'vscode'
    
    def test_browsers(self):
        assert normalize_app_name({'app_class': 'brave'}) == 'brave'
        assert normalize_app_name({'app_class': 'brave-browser'}) == 'brave'
        assert normalize_app_name({'app_class': 'firefox'}) == 'firefox'
        assert normalize_app_name({'app_class': 'chrome'}) == 'chrome'
    
    def test_terminals(self):
        assert normalize_app_name({'app_class': 'konsole'}) == 'konsole'
        assert normalize_app_name({'app_class': 'alacritty'}) == 'alacritty'
        assert normalize_app_name({'app_class': 'kitty'}) == 'kitty'
    
    def test_unknown_fallback(self):
        assert normalize_app_name({'app_class': 'unknownapp'}) == 'unknownapp'
        assert normalize_app_name({'title': 'Some Window - UnknownApp'}) == 'unknownapp'


class TestDatabaseSchema:
    def test_tables_exist(self):
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            # Create a minimal daemon instance to test schema
            import app_telemetry_daemon as daemon_module
            original_db = daemon_module.DB_PATH
            daemon_module.DB_PATH = Path(db_path)
            
            try:
                telemetry = AppTelemetry()
                telemetry.init_db()
            finally:
                daemon_module.DB_PATH = original_db
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Check all tables exist
            tables = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {t[0] for t in tables}
            
            expected = {
                'window_events', 'browser_history', 'kde_directory_events',
                'process_events', 'desktop_launches', 'app_sessions'
            }
            assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"
            
            # Check indexes exist
            indexes = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
            index_names = {i[0] for i in indexes}
            
            expected_indexes = {
                'idx_window_ts', 'idx_window_app', 'idx_browser_ts',
                'idx_browser_browser', 'idx_browser_url', 'idx_kde_ts',
                'idx_kde_app', 'idx_kde_path', 'idx_process_ts',
                'idx_process_exe', 'idx_session_app', 'idx_session_date',
                'idx_desktop_ts'
            }
            assert expected_indexes.issubset(index_names), f"Missing indexes: {expected_indexes - index_names}"
            
            conn.close()
        finally:
            os.unlink(db_path)


class TestEventQueue:
    def test_queue_and_flush(self):
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import app_telemetry_daemon as daemon_module
            original_db = daemon_module.DB_PATH
            daemon_module.DB_PATH = Path(db_path)
            
            try:
                telemetry = AppTelemetry()
                telemetry.init_db()
                
                # Queue some events
                telemetry.queue_event('window_events', {
                    'timestamp': 1000.0,
                    'app_name': 'test',
                    'duration_seconds': 10.0,
                    'is_idle': 0
                })
                telemetry.queue_event('window_events', {
                    'timestamp': 2000.0,
                    'app_name': 'test2',
                    'duration_seconds': 5.0,
                    'is_idle': 1
                })
                
                # Flush
                telemetry.flush_events()
                
                # Verify
                conn = sqlite3.connect(db_path)
                rows = conn.execute("SELECT * FROM window_events").fetchall()
                conn.close()
                
                assert len(rows) == 2
                assert rows[0][2] == 'test'
                assert rows[1][2] == 'test2'
            finally:
                daemon_module.DB_PATH = original_db
        finally:
            os.unlink(db_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
