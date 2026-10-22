#!/usr/bin/env python3
"""Hourly India gold outlook. Open http://127.0.0.1:8765"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from model import IST, build_outlook
from sources import gather

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "outlook.sqlite"
INDEX = ROOT / "static" / "index.html"
PORT = 8765

_state = {"next_update": None}
_state_lock = threading.Lock()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at TEXT NOT NULL,
                ok INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )


def save_snapshot(payload):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO snapshots (fetched_at, ok, payload) VALUES (?, ?, ?)",
            (payload.get("as_of") or "", 1 if payload.get("ok") else 0, json.dumps(payload)),
        )
        con.execute(
            """
            DELETE FROM snapshots
            WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT 960)
            """
        )


def latest_payload():
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT payload FROM snapshots WHERE ok = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        history_rows = con.execute(
            "SELECT payload FROM snapshots WHERE ok = 1 ORDER BY id DESC LIMIT 48"
        ).fetchall()
    if not row:
        bad = con.execute(
            "SELECT payload FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        with _state_lock:
            nxt = _state["next_update"]
        if bad:
            payload = json.loads(bad[0])
            payload["status"] = "error"
            payload["next_update"] = nxt
            return payload
        return {"ok": False, "status": "loading", "next_update": nxt}
    payload = json.loads(row[0])
    readings = []
    for (blob,) in reversed(history_rows):
        item = json.loads(blob)
        readings.append(
            {
                "as_of": item.get("as_of"),
                "now": (item.get("now") or {}).get("inr_10g_24k"),
                "ahead": (item.get("ahead") or {}).get("inr_10g_24k"),
            }
        )
    payload["readings"] = readings
    with _state_lock:
        payload["next_update"] = _state["next_update"]
    return payload


def _next_hour(now=None):
    now = now or datetime.now(IST)
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=20, microsecond=0)
    if (nxt - now).total_seconds() < 45:
        nxt += timedelta(hours=1)
    return nxt


def refresh():
    try:
        payload = build_outlook(gather())
    except Exception as exc:  # noqa: BLE001 — keep the last good snapshot
        payload = {
            "ok": False,
            "error": str(exc),
            "as_of": datetime.now(IST).isoformat(),
        }
    save_snapshot(payload)
    print(
        f"[{payload.get('as_of')}] ok={payload.get('ok')} "
        f"now={(payload.get('now') or {}).get('inr_10g_24k')}",
        flush=True,
    )
    return payload


def hourly_loop():
    while True:
        refresh()
        nxt = _next_hour()
        with _state_lock:
            _state["next_update"] = nxt.isoformat()
        delay = (nxt - datetime.now(IST)).total_seconds()
        time.sleep(max(30, delay))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = INDEX.read_bytes()
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/api/outlook":
            body = json.dumps(latest_payload()).encode()
            self._send(200, "application/json; charset=utf-8", body)
            return
        self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main():
    init_db()
    threading.Thread(target=hourly_loop, name="hourly", daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"India gold outlook at http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
