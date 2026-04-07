#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STORE_PATH = DATA_DIR / "store.json"
UI_PATH = BASE_DIR / "ui" / "index.html"


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Keep runtime behavior predictable: local .env should win for this app.
        os.environ[key] = value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if STORE_PATH.exists():
        return
    initial = {"past_queries": [], "upcoming_queries": []}
    STORE_PATH.write_text(json.dumps(initial, indent=2), encoding="utf-8")


def read_store() -> dict:
    ensure_store()
    raw = STORE_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    data.setdefault("past_queries", [])
    data.setdefault("upcoming_queries", [])
    return data


def write_store(data: dict) -> None:
    STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def normalize_next_query(data: dict) -> Optional[dict]:
    upcoming = data.get("upcoming_queries", [])
    if not upcoming:
        return None
    # Keep backward compatibility with existing list data; newest item wins.
    return upcoming[-1]


def build_search_url(params: dict) -> str:
    encoded = urllib.parse.urlencode(params)
    return f"https://seats.aero/partnerapi/search?{encoded}"


def run_seats_query(params: dict) -> dict:
    api_key = (os.environ.get("SEATS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("SEATS_API_KEY is missing. Put it in .env first.")

    url = build_search_url(params)
    req = urllib.request.Request(
        url,
        headers={
            "Partner-Authorization": api_key,
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = ""
        raise RuntimeError(f"Seats API HTTP {exc.code}: {detail or exc.reason}") from exc


def extract_hits(payload: dict, max_mileage: int) -> list:
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    hits = []
    for row in rows:
        if not row.get("JAvailable"):
            continue
        if not (row.get("JDirect") or row.get("JDirectRaw")):
            continue
        mileage = row.get("JMileageCostRaw") or 0
        if mileage > max_mileage:
            continue
        route = row.get("Route") or {}
        hits.append(
            {
                "date": row.get("Date"),
                "origin": route.get("OriginAirport"),
                "destination": route.get("DestinationAirport"),
                "source": row.get("Source") or route.get("Source"),
                "mileage": mileage,
                "seats": row.get("JRemainingSeats"),
            }
        )
    return hits


def default_params() -> dict:
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=14)
    return {
        "origin_airport": "SFO,SEA,LAX",
        "destination_airport": "TPE,ICN,NRT,HND,HKG,PVG",
        "start_date": today.isoformat(),
        "end_date": end.isoformat(),
        "cabin": "business",
        "only_direct_flights": "true",
        "order_by": "lowest_mileage",
        "take": "200",
    }


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
        return json.loads(body)

    def do_GET(self) -> None:
        if self.path == "/":
            if not UI_PATH.exists():
                self._send_html("<h1>Missing ui/index.html</h1>")
                return
            self._send_html(UI_PATH.read_text(encoding="utf-8"))
            return

        if self.path == "/api/state":
            data = read_store()
            self._send_json(
                {
                    "past_queries": data["past_queries"],
                    "upcoming_queries": data["upcoming_queries"],
                    "next_query": normalize_next_query(data),
                    "default_params": default_params(),
                }
            )
            return

        if self.path.startswith("/api/query/"):
            query_id = self.path.split("/api/query/")[-1]
            data = read_store()
            for entry in data["past_queries"]:
                if entry["id"] == query_id:
                    self._send_json(entry)
                    return
            self._send_json({"error": "Query not found"}, status=404)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:
        if self.path == "/api/query/run":
            try:
                payload = self._read_json_body()
                params = payload.get("params") or default_params()
                max_mileage = int(payload.get("max_mileage", 100000))
                api_response = run_seats_query(params)
                hits = extract_hits(api_response, max_mileage=max_mileage)
                rows = api_response.get("data", []) if isinstance(api_response, dict) else []

                record = {
                    "id": str(uuid.uuid4()),
                    "created_at": utc_now(),
                    "params": params,
                    "max_mileage": max_mileage,
                    "total_returned": len(rows),
                    "total_hits": len(hits),
                    "hits": hits,
                }
                data = read_store()
                data["past_queries"].insert(0, record)
                write_store(data)
                self._send_json(record)
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        if self.path == "/api/query/schedule":
            try:
                payload = self._read_json_body()
                name = payload.get("name") or "Hourly Search"
                interval_minutes = int(payload.get("interval_minutes", 60))
                params = payload.get("params") or default_params()

                upcoming = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "interval_minutes": interval_minutes,
                    "next_run_at": (datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)).isoformat(),
                    "params": params,
                    "created_at": utc_now(),
                }
                data = read_store()
                # Single next-query mode: always replace previous schedule.
                data["upcoming_queries"] = [upcoming]
                write_store(data)
                self._send_json(upcoming)
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        if self.path == "/api/query/schedule/delete":
            try:
                payload = self._read_json_body()
                schedule_id = (payload.get("id") or "").strip()
                if not schedule_id:
                    self._send_json({"error": "Missing schedule id"}, status=400)
                    return

                data = read_store()
                before = len(data["upcoming_queries"])
                data["upcoming_queries"] = [
                    q for q in data["upcoming_queries"] if q.get("id") != schedule_id
                ]
                after = len(data["upcoming_queries"])
                if before == after:
                    self._send_json({"error": "Upcoming query not found"}, status=404)
                    return

                write_store(data)
                self._send_json({"ok": True, "deleted_id": schedule_id})
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    ensure_store()
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("RewardsTicket GUI running at http://127.0.0.1:8787")
    server.serve_forever()


if __name__ == "__main__":
    main()
