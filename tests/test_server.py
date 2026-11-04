import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server


class SensorTests(unittest.TestCase):
    def test_signed_temperature_and_battery_bounds(self):
        self.assertEqual(server.decode(struct.pack('<hBH', -425, 48, 2900)),
                         dict(temperature=-4.25, humidity=48, voltage=2900, battery=80))
        self.assertEqual(server.decode(struct.pack('<hBH', 2300, 50, 3200))['battery'], 100)
        with self.assertRaises(ValueError):
            server.decode(b'bad')

    def test_persist_and_read_history(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB', Path(temp) / 'readings.sqlite3'):
            server.init_db()
            self.assertIsNone(server.snapshot(24)['latest'])
            server.on_data(None, struct.pack('<hBH', 2345, 60, 2800))
            result = server.snapshot(24)
            self.assertEqual(result['latest']['temperature'], 23.45)
            self.assertEqual(len(result['history']), 1)
            self.assertEqual(result['stats']['min'], 23.45)
            server.init_db()
            self.assertEqual(server.snapshot(24)['latest']['humidity'], 60)

    def test_chart_points_preserve_actual_measurements_for_all_metrics(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB', Path(temp) / 'readings.sqlite3'), patch.object(server.time, 'time', return_value=100005):
            server.init_db()
            with sqlite3.connect(server.DB) as db:
                db.executemany('INSERT INTO readings VALUES (?, ?, ?, ?, ?)', [
                    (100001, 23.12, 41, 2901, 80), (100002, 23.34, 43, 2903, 80)])
            result = server.snapshot(1)
            self.assertEqual(result['history'], [dict(ts=100002, temperature=23.34, humidity=43, voltage=2903)])
            self.assertEqual(result['metric_stats']['humidity'], dict(min=41, max=43, avg=42))
            self.assertEqual(result['metric_stats']['voltage'], dict(min=2901, max=2903, avg=2902))


if __name__ == '__main__':
    unittest.main()


class ShutdownTests(unittest.TestCase):
    def test_ctrl_c_waits_for_bluetooth_cleanup(self):
        import asyncio
        import threading
        from unittest.mock import MagicMock
        entered = threading.Event()
        disconnected = threading.Event()

        async def collect():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                disconnected.set()

        def interrupt():
            self.assertTrue(entered.wait(2))
            raise KeyboardInterrupt()

        http = MagicMock()
        http.serve_forever.side_effect = interrupt
        with patch.object(server, 'init_db'), patch.object(server, 'collect', collect), \
                patch.object(server, 'ThreadingHTTPServer', return_value=http):
            server.run_server()
        self.assertTrue(disconnected.is_set())
        http.server_close.assert_called_once()


class IntervalTests(unittest.TestCase):
    def test_interval_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB', Path(temp) / 'readings.sqlite3'):
            server.init_db()
            server.set_interval(300)
            server.init_db()
            self.assertEqual(server.snapshot(1)['interval'], 300)
            for value in (-2, 1, True, '300', 2.5):
                with self.assertRaises(ValueError):
                    server.set_interval(value)
            self.assertEqual(server.get_interval(), 300)
            for value in (3600, -1):
                server.set_interval(value)
                server.init_db()
                self.assertEqual(server.get_interval(), value)

    def test_single_sample_disconnects_before_pause(self):
        import asyncio
        import sys
        import types
        events = []
        class Client:
            is_connected = True
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                events.append('connect')
                return self
            async def __aexit__(self, *args):
                events.append('disconnect')
            async def start_notify(self, uuid, callback):
                callback(None, b'bad')
                callback(None, struct.pack('<hBH', 2312, 45, 2900))
                callback(None, struct.pack('<hBH', 2500, 50, 2800))
        async def pause(delay):
            self.assertEqual(events, ['connect', 'disconnect'])
            self.assertTrue(server.state['sleeping'])
            raise asyncio.CancelledError()
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB', Path(temp) / 'readings.sqlite3'):
            server.init_db()
            server.set_interval(300)
            with patch.dict(sys.modules, bleak=types.SimpleNamespace(BleakClient=Client)), patch.object(server.asyncio, 'sleep', pause):
                with self.assertRaises(asyncio.CancelledError):
                    asyncio.run(server.collect())
            result = server.snapshot(1)
            self.assertEqual(result['latest']['temperature'], 23.12)
            self.assertEqual(len(result['history']), 1)

class PauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_cancels_connection_and_resume_restarts(self):
        import asyncio
        events = []
        async def active():
            events.append('connect')
            try:
                await asyncio.Event().wait()
            finally:
                events.append('disconnect')
        with patch.object(server, 'settings', {'interval': 0}), patch.object(server, 'collect_active', active):
            task = asyncio.create_task(server.collect())
            try:
                await asyncio.sleep(.05)
                server.settings['interval'] = -1
                await asyncio.sleep(.35)
                self.assertEqual(events, ['connect', 'disconnect'])
                self.assertFalse(server.state['connected'])
                await asyncio.sleep(.3)
                self.assertEqual(len(events), 2)
                server.settings['interval'] = 3600
                await asyncio.sleep(.3)
                self.assertEqual(events, ['connect', 'disconnect', 'connect'])
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

class HistoryWindowTests(unittest.TestCase):
    def test_window_excludes_newer_readings_from_graph_and_stats(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB', Path(temp)/'readings.sqlite3'), patch.object(server.time, 'time', return_value=200000):
            server.init_db()
            with sqlite3.connect(server.DB) as db:
                db.executemany('INSERT INTO readings VALUES (?, ?, ?, ?, ?)', [(100000,20,40,2900,80),(101000,21,41,2910,81),(190000,30,60,3000,90)])
            result=server.snapshot(1, 102000)
            self.assertEqual([p['ts'] for p in result['history']], [100000,101000])
            self.assertEqual(result['stats']['max'],21)
            self.assertEqual(result['latest']['temperature'],30)
            self.assertEqual(result['earliest'],100000)
            self.assertEqual(result['window'],dict(start=98400,end=102000))
            self.assertEqual(server.snapshot(1,300000)['window']['end'],200000)

class ChartBoundaryTests(unittest.TestCase):
    def test_plot_keeps_neighbors_without_affecting_window_statistics(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DB', Path(temp)/'db'), patch.object(server.time, 'time', return_value=200000):
            server.init_db()
            with sqlite3.connect(server.DB) as db:
                db.executemany('INSERT INTO readings VALUES (?,?,?,?,?)', [(98000,20,40,2900,80),(100000,21,41,2910,81),(103000,22,42,2920,82)])
            for end in (102000, 102001):
                result = server.snapshot(1, end)
                self.assertLess(result['plot_history'][0]['ts'], result['window']['start'])
                self.assertGreater(result['plot_history'][-1]['ts'], end)
                self.assertEqual(result['stats']['min'], 21)
                self.assertEqual(result['stats']['max'], 21)
