"""Local Mi sensor dashboard: python server.py."""
import asyncio
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
MAC = os.getenv('MI_MAC', 'A4:C1:38:CC:2D:CB')
UUID = 'ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6'
DB = ROOT / 'data' / 'readings.sqlite3'
state = {'connected': False, 'message': 'Подключение к датчику…'}
lock = threading.Lock()
INTERVALS = (-1, 0, 60, 300, 600, 1800, 3600)
settings = {'interval': 0}


def decode(data):
    if len(data) != 5:
        raise ValueError('Ожидалось 5 байт')
    temperature, humidity, voltage = struct.unpack('<hBH', data)
    if humidity > 100 or not 1500 <= voltage <= 4000:
        raise ValueError('Некорректные показания')
    return {'temperature': temperature / 100, 'humidity': humidity,
            'voltage': voltage, 'battery': round(max(0, min(100, (voltage - 2100) / 10)))}


def init_db():
    DB.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(DB)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS readings (ts REAL PRIMARY KEY, temperature REAL, humidity INTEGER, voltage INTEGER, battery INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER)')
        row = db.execute("SELECT value FROM settings WHERE key = 'interval'").fetchone()
    with lock:
        settings['interval'] = row[0] if row and row[0] in INTERVALS else 0



def get_interval():
    with lock:
        return settings['interval']


def set_interval(value):
    if type(value) is not int or value not in INTERVALS:
        raise ValueError('Недопустимый интервал')
    with lock:
        with closing(sqlite3.connect(DB)) as db, db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('interval', ?)", (value,))
        settings['interval'] = value


def on_data(sender, data):
    try:
        reading = decode(data)
    except (ValueError, struct.error):
        return False
    with closing(sqlite3.connect(DB)) as db, db:
        db.execute('INSERT INTO readings VALUES (?, ?, ?, ?, ?)',
                   (time.time(), reading['temperature'], reading['humidity'], reading['voltage'], reading['battery']))
    with lock:
        state.update(connected=True, message='Датчик подключён')
    return True


async def collect():
    active = None
    try:
        while True:
            if get_interval() == -1:
                if active is not None:
                    active.cancel()
                    try:
                        await active
                    except asyncio.CancelledError:
                        pass
                    active = None
                with lock:
                    state.update(connected=False, sleeping=False, next_reading=None,
                                 message='Сбор приостановлен · Bluetooth отключён')
                await asyncio.sleep(0.25)
            else:
                if active is None:
                    active = asyncio.create_task(collect_active())
                done, _ = await asyncio.wait({active}, timeout=0.25)
                if done:
                    await active
                    return
    finally:
        if active is not None:
            active.cancel()
            try:
                await active
            except asyncio.CancelledError:
                pass


async def collect_active():
    try:
        from bleak import BleakClient
    except ImportError:
        with lock:
            state.update(message='Установите зависимости: pip install -r requirements.txt')
        return
    last_sample = None
    while True:
        interval = get_interval()
        if interval and last_sample is not None and time.monotonic() < last_sample + interval:
            remaining = last_sample + interval - time.monotonic()
            with lock:
                state.update(connected=False, sleeping=True,
                             message='Пауза между замерами · Bluetooth отключён',
                             next_reading=time.time() + remaining)
            await asyncio.sleep(min(1, remaining))
            continue
        with lock:
            state.update(connected=False, sleeping=False, next_reading=None,
                         message='Подключение к датчику…')
        try:
            async with BleakClient(MAC, timeout=60) as client:
                received = asyncio.Event()
                last_notification = time.monotonic()

                def receive(sender, data):
                    nonlocal last_sample, last_notification
                    if get_interval() == -1 or (get_interval() > 0 and received.is_set()):
                        return
                    if on_data(sender, data):
                        last_sample = last_notification = time.monotonic()
                        received.set()

                with lock:
                    state.update(connected=True, message='Ожидаем показания датчика')
                await client.start_notify(UUID, receive)
                while client.is_connected:
                    if get_interval() == -1 or (get_interval() > 0 and received.is_set()):
                        break
                    if time.monotonic() - last_notification > 30:
                        raise TimeoutError('Датчик не прислал показания за 30 секунд')
                    await asyncio.sleep(1)
                # Context manager disconnects before the scheduled pause.
            if get_interval() == -1 or (get_interval() > 0 and received.is_set()):
                continue
        except Exception as exc:
            print(f'Bluetooth: {type(exc).__name__}: {exc}', flush=True)
        with lock:
            state.update(connected=False, sleeping=False, next_reading=None,
                         message='Датчик недоступен. Повторное подключение через 10 секунд')
        await asyncio.sleep(10)


def snapshot(hours, end=None):
    with closing(sqlite3.connect(DB)) as db, db:
        db.row_factory = sqlite3.Row
        latest = db.execute('SELECT * FROM readings ORDER BY ts DESC LIMIT 1').fetchone()
        # Keep actual measurements (latest in each bucket) for exact tooltips.
        now = time.time()
        earliest = db.execute('SELECT MIN(ts) FROM readings').fetchone()[0]
        end = now if end is None else min(now, max(earliest or end, end))
        cutoff = end - hours * 3600
        width = hours * 3600 / 360
        history = db.execute("""SELECT ts, temperature, humidity, voltage FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY CAST(ts / ? AS INTEGER)
            ORDER BY ts DESC) AS rank FROM readings WHERE ts >= ? AND ts <= ?
        ) WHERE rank = 1 ORDER BY ts""", (width, cutoff, end)).fetchall()
        # Prefetch a window on either side, plus the adjacent endpoints. Keep
        # bucket representatives independent of the viewport to avoid flicker.
        buffer_start, buffer_end = cutoff - hours * 3600, end + hours * 3600
        plot_history = db.execute("""WITH sampled AS (
            SELECT ts, temperature, humidity, voltage, ROW_NUMBER() OVER (
                PARTITION BY CAST(ts / ? AS INTEGER) ORDER BY ts DESC) AS rank
            FROM readings WHERE ts >= ? AND ts <= ?
        ) SELECT ts, temperature, humidity, voltage FROM sampled WHERE rank = 1
        UNION SELECT ts, temperature, humidity, voltage FROM readings WHERE ts =
            (SELECT MAX(ts) FROM readings WHERE ts < ?)
        UNION SELECT ts, temperature, humidity, voltage FROM readings WHERE ts =
            (SELECT MIN(ts) FROM readings WHERE ts > ?)
        ORDER BY ts""", (width, math.floor(buffer_start / width) * width,
                        (math.floor(buffer_end / width) + 1) * width,
                        buffer_start, buffer_end)).fetchall()
        metric_stats = {}
        for metric in ('temperature', 'humidity', 'voltage'):
            row = db.execute(f'SELECT MIN({metric}), MAX({metric}), AVG({metric}) FROM readings WHERE ts >= ? AND ts <= ?', (cutoff, end)).fetchone()
            metric_stats[metric] = dict(zip(['min', 'max', 'avg'], row))
    with lock:
        status = {**state, 'interval': settings['interval']}
    return {**status, 'server_time': now, 'window': {'start': cutoff, 'end': end}, 'earliest': earliest, 'mac': MAC, 'latest': dict(latest) if latest else None,
            'plot_history': [dict(row) for row in plot_history], 'history': [dict(row) for row in history], 'stats': metric_stats['temperature'], 'metric_stats': metric_stats}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'web'), **kwargs)

    def do_POST(self):
        if self.path != '/api/settings':
            self.send_error(404)
            return
        # Require a same-origin JSON request (no cross-site form changes).
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            set_interval(payload['interval'])
        except (ValueError, KeyError, TypeError):
            self.send_error(400, 'Invalid interval')
            return
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/api/readings':
            try:
                query = parse_qs(url.query)
                hours = int(query.get('hours', ['24'])[0])
                end = float(query['end'][0]) if 'end' in query else None
                if end is not None and (not math.isfinite(end) or end < 0):
                    raise ValueError()
                if hours not in (1, 6, 24, 168):
                    raise ValueError()
            except ValueError:
                self.send_error(400, 'Invalid range')
                return
            payload = json.dumps(snapshot(hours, end)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            super().do_GET()


def run_server():
    init_db()
    port = int(os.getenv('PORT', '8000'))
    # Bind before starting Bluetooth so a duplicate server cannot claim the sensor.
    http = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    loop = asyncio.new_event_loop()
    collector = loop.create_task(collect())

    def run_collector():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(collector)
        except asyncio.CancelledError:
            pass
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    worker = threading.Thread(target=run_collector, daemon=True)
    worker.start()
    print(f'Mi dashboard: http://localhost:{port}', flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        print('\nОстановка сервера и отключение Bluetooth…', flush=True)
    finally:
        http.server_close()
        if not loop.is_closed():
            loop.call_soon_threadsafe(collector.cancel)
        # Allow BleakClient.__aexit__ to disconnect before exiting the process.
        worker.join(timeout=15)
        if worker.is_alive():
            print('Bluetooth не успел отключиться. При необходимости: '
                  f'bluetoothctl disconnect {MAC}', flush=True)


if __name__ == '__main__':
    run_server()
