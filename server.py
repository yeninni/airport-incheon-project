#!/usr/bin/env python3
"""Lightweight local server for the airport dashboard.

Serves static files from the repository root and exposes a live parking proxy:
  GET /api/parking/live
  GET /api/health
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
API_URL = "https://apis.data.go.kr/B551177/StatusOfParking/getTrackingParking"
DEFAULT_PORT = int(os.getenv("PORT", "8000"))
DEFAULT_POLL_INTERVAL = int(os.getenv("PARKING_CACHE_SECONDS", "30"))
API_KEY = os.getenv("AIRPORT_API_KEY") or os.getenv("AIRPORT_PARKING_API_KEY")
KST = timezone(timedelta(hours=9))


def _to_iso8601(datetm: str) -> str | None:
    raw = (datetm or "").split(".")[0]
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=KST).isoformat()


def _classify_terminal(floor: str) -> str:
    if floor.startswith("T1"):
        return "T1"
    if floor.startswith("T2"):
        return "T2"
    return "Other"


def _classify_area_type(floor: str) -> str:
    if "예약" in floor:
        return "reservation"
    if "단기" in floor:
        return "short-term"
    if "화물" in floor:
        return "cargo"
    if "장기" in floor:
        return "long-term"
    return "parking"


def _severity(rate: float) -> str:
    if rate >= 95:
        return "critical"
    if rate >= 85:
        return "high"
    if rate >= 70:
        return "medium"
    return "low"


def _fetch_remote_payload(api_key: str) -> dict[str, Any]:
    params = {
        "serviceKey": api_key,
        "numOfRows": "50",
        "pageNo": "1",
        "type": "json",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def _normalize_items(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("response", {}).get("body", {})
    header = payload.get("response", {}).get("header", {})
    result_code = header.get("resultCode")
    if result_code != "00":
        raise RuntimeError(f"Airport API returned {result_code}: {header.get('resultMsg', 'unknown error')}")

    raw_items = body.get("items") or []
    areas: list[dict[str, Any]] = []
    terminal_totals: dict[str, dict[str, Any]] = {}

    for item in raw_items:
        floor = str(item.get("floor", "")).strip()
        occupied = int(item.get("parking", 0))
        capacity = int(item.get("parkingarea", 0))
        available = max(capacity - occupied, 0)
        occupancy_rate = round((occupied / capacity) * 100, 1) if capacity else 0.0
        terminal = _classify_terminal(floor)
        area_type = _classify_area_type(floor)
        source_updated_at = _to_iso8601(str(item.get("datetm", "")))

        normalized = {
            "name": floor,
            "terminal": terminal,
            "type": area_type,
            "occupied": occupied,
            "capacity": capacity,
            "available": available,
            "occupancyRate": occupancy_rate,
            "severity": _severity(occupancy_rate),
            "sourceUpdatedAt": source_updated_at,
        }
        areas.append(normalized)

        if terminal not in terminal_totals:
            terminal_totals[terminal] = {
                "terminal": terminal,
                "occupied": 0,
                "capacity": 0,
                "available": 0,
            }
        terminal_totals[terminal]["occupied"] += occupied
        terminal_totals[terminal]["capacity"] += capacity
        terminal_totals[terminal]["available"] += available

    total_occupied = sum(area["occupied"] for area in areas)
    total_capacity = sum(area["capacity"] for area in areas)
    total_available = max(total_capacity - total_occupied, 0)
    total_rate = round((total_occupied / total_capacity) * 100, 1) if total_capacity else 0.0
    sorted_areas = sorted(areas, key=lambda area: area["occupancyRate"], reverse=True)
    hotspot = sorted_areas[0] if sorted_areas else None

    terminals = []
    for terminal in sorted(terminal_totals):
        info = terminal_totals[terminal]
        rate = round((info["occupied"] / info["capacity"]) * 100, 1) if info["capacity"] else 0.0
        terminals.append({
            **info,
            "occupancyRate": rate,
            "severity": _severity(rate),
        })

    source_updated_at = max(
        (area["sourceUpdatedAt"] for area in areas if area["sourceUpdatedAt"]),
        default=None,
    )

    return {
        "summary": {
            "totalOccupied": total_occupied,
            "totalCapacity": total_capacity,
            "totalAvailable": total_available,
            "occupancyRate": total_rate,
            "hotspot": hotspot["name"] if hotspot else None,
            "hotspotRate": hotspot["occupancyRate"] if hotspot else None,
            "congestedZones": sum(1 for area in areas if area["occupancyRate"] >= 85),
        },
        "terminals": terminals,
        "areas": sorted_areas,
        "sourceUpdatedAt": source_updated_at,
    }


@dataclass
class CacheState:
    body: dict[str, Any] | None = None
    fetched_at: float = 0.0
    error: str | None = None


class ParkingService:
    def __init__(self, api_key: str | None, ttl_seconds: int = DEFAULT_POLL_INTERVAL) -> None:
        self.api_key = api_key
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cache = CacheState()

    def _refresh(self) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("AIRPORT_API_KEY environment variable is not set.")

        payload = _fetch_remote_payload(self.api_key)
        normalized = _normalize_items(payload)
        now = datetime.now().astimezone().isoformat()
        return {
            "status": "ok",
            "fetchedAt": now,
            **normalized,
        }

    def get_live_summary(self) -> dict[str, Any]:
        with self._lock:
            cache_is_fresh = (
                self._cache.body is not None and
                (time.time() - self._cache.fetched_at) < self.ttl_seconds
            )
            if cache_is_fresh:
                return self._cache.body

            try:
                fresh = self._refresh()
            except Exception as exc:  # noqa: BLE001
                self._cache.error = str(exc)
                if self._cache.body is not None:
                    cached = dict(self._cache.body)
                    cached["status"] = "stale"
                    cached["error"] = self._cache.error
                    return cached
                raise

            self._cache.body = fresh
            self._cache.fetched_at = time.time()
            self._cache.error = None
            return fresh


parking_service = ParkingService(API_KEY)


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/health":
            self._send_json(HTTPStatus.OK, {
                "status": "ok",
                "hasAirportApiKey": bool(API_KEY),
            })
            return

        if parsed.path == "/api/parking/live":
            try:
                payload = parking_service.get_live_summary()
                self._send_json(HTTPStatus.OK, payload)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                self._send_json(HTTPStatus.BAD_GATEWAY, {
                    "status": "error",
                    "message": "Airport parking API request failed.",
                    "details": body,
                })
            except Exception as exc:  # noqa: BLE001
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "status": "error",
                    "message": str(exc),
                })
            return

        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[server] {self.address_string()} - {format % args}")

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", DEFAULT_PORT), DashboardHandler)
    print(f"Serving dashboard at http://127.0.0.1:{DEFAULT_PORT}")
    print("API endpoints:")
    print(f"  http://127.0.0.1:{DEFAULT_PORT}/api/health")
    print(f"  http://127.0.0.1:{DEFAULT_PORT}/api/parking/live")
    server.serve_forever()


if __name__ == "__main__":
    main()
