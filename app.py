#!/usr/bin/env python3
import smtplib
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STORE_PATH = DATA_DIR / "store.json"
UI_PATH = BASE_DIR / "ui" / "index.html"
STORE_LOCK = threading.RLock()
RATE_LIMIT_LOCK = threading.RLock()
SCHEDULER_POLL_SECONDS = 20
LAST_RATE_LIMIT = None


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


def _read_store_unlocked() -> dict:
    ensure_store()
    raw = STORE_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    data.setdefault("past_queries", [])
    data.setdefault("upcoming_queries", [])
    return data


def _write_store_unlocked(data: dict) -> None:
    STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_store() -> dict:
    with STORE_LOCK:
        return _read_store_unlocked()


def write_store(data: dict) -> None:
    with STORE_LOCK:
        _write_store_unlocked(data)


def normalize_next_query(data: dict) -> Optional[dict]:
    upcoming = data.get("upcoming_queries", [])
    if not upcoming:
        return None
    # Keep backward compatibility with existing list data; newest item wins.
    current = upcoming[-1]
    if "enabled" not in current:
        current["enabled"] = True
    return current


def build_search_url(params: dict) -> str:
    encoded = urllib.parse.urlencode(params)
    return f"https://seats.aero/partnerapi/search?{encoded}"


def _extract_airlines(row: dict) -> str:
    for key in [
        "JDirectAirlinesRaw",
        "JDirectAirlines",
        "JAirlinesRaw",
        "JAirlines",
        "WDirectAirlinesRaw",
        "WDirectAirlines",
        "WAirlinesRaw",
        "WAirlines",
        "YDirectAirlinesRaw",
        "YDirectAirlines",
        "YAirlinesRaw",
        "YAirlines",
    ]:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_flight_numbers(row: dict) -> str:
    trips = row.get("AvailabilityTrips")
    if not isinstance(trips, list) or not trips:
        return ""

    found = []

    def walk(node):
        if isinstance(node, dict):
            flight_no = (
                node.get("FlightNumber")
                or node.get("flightNumber")
                or node.get("flight_number")
                or node.get("flightNo")
                or node.get("flight_no")
            )
            carrier = (
                node.get("MarketingAirline")
                or node.get("OperatingAirline")
                or node.get("Airline")
                or node.get("airline")
                or node.get("Carrier")
                or node.get("carrier")
            )
            if flight_no:
                if carrier and isinstance(carrier, str):
                    found.append(f"{carrier}{flight_no}")
                else:
                    found.append(str(flight_no))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(trips)
    uniq = []
    seen = set()
    for item in found:
        norm = item.strip()
        if norm and norm not in seen:
            seen.add(norm)
            uniq.append(norm)
    return ", ".join(uniq[:4])


def run_seats_query(params: dict) -> dict:
    api_key = (os.environ.get("SEATS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("SEATS_API_KEY is missing. Put it in .env first.")

    # Enable trip-level fields so we can surface flight numbers when available.
    params = dict(params)
    params.setdefault("include_trips", "true")
    params.setdefault("minify_trips", "true")
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
            headers = {k.lower(): v for k, v in response.headers.items()}
            limit_raw = headers.get("x-ratelimit-limit")
            remaining_raw = headers.get("x-ratelimit-remaining")
            reset_raw = headers.get("x-ratelimit-reset")
            try:
                limit = int(limit_raw) if limit_raw is not None else None
                remaining = int(remaining_raw) if remaining_raw is not None else None
                reset_seconds = int(reset_raw) if reset_raw is not None else None
                reset_at = (
                    (datetime.now(timezone.utc) + timedelta(seconds=reset_seconds)).isoformat()
                    if reset_seconds is not None
                    else None
                )
                if limit is not None and remaining is not None:
                    set_last_rate_limit(
                        {
                            "limit": limit,
                            "remaining": remaining,
                            "reset_seconds": reset_seconds,
                            "reset_at": reset_at,
                            "updated_at": utc_now(),
                        }
                    )
            except Exception:
                # Non-fatal: keep query flow even if rate-limit header parse fails.
                pass
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
        airlines = _extract_airlines(row)
        flight_numbers = _extract_flight_numbers(row)
        hits.append(
            {
                "date": row.get("Date"),
                "origin": route.get("OriginAirport"),
                "destination": route.get("DestinationAirport"),
                "source": row.get("Source") or route.get("Source"),
                "airlines": airlines,
                "flight_numbers": flight_numbers,
                "mileage": mileage,
                "seats": row.get("JRemainingSeats"),
            }
        )
    return hits


def _build_query_record(params: dict, max_mileage: int) -> dict:
    api_response = run_seats_query(params)
    hits = extract_hits(api_response, max_mileage=max_mileage)
    rows = api_response.get("data", []) if isinstance(api_response, dict) else []
    return {
        "id": str(uuid.uuid4()),
        "created_at": utc_now(),
        "params": params,
        "max_mileage": max_mileage,
        "total_returned": len(rows),
        "total_hits": len(hits),
        "hits": hits,
    }


def set_last_rate_limit(rate_limit: dict) -> None:
    global LAST_RATE_LIMIT
    with RATE_LIMIT_LOCK:
        LAST_RATE_LIMIT = dict(rate_limit) if rate_limit else None


def get_last_rate_limit() -> Optional[dict]:
    with RATE_LIMIT_LOCK:
        return dict(LAST_RATE_LIMIT) if LAST_RATE_LIMIT else None


def _parse_iso_utc(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def process_due_schedule_once() -> None:
    now = datetime.now(timezone.utc)
    schedule = None
    schedule_id = None
    params = None
    max_mileage = 100000
    next_run_at = None

    with STORE_LOCK:
        data = _read_store_unlocked()
        current = normalize_next_query(data)
        if not current:
            return
        if not bool(current.get("enabled", True)):
            return
        due_at = _parse_iso_utc(str(current.get("next_run_at") or ""))
        if due_at is None:
            due_at = now
        if due_at > now:
            return

        schedule_id = current.get("id")
        interval_minutes = max(1, int(current.get("interval_minutes", 60)))
        next_run_at = (now + timedelta(minutes=interval_minutes)).isoformat()
        params = dict(current.get("params") or default_params())
        max_mileage = int(current.get("max_mileage", 100000))

        for idx, entry in enumerate(data.get("upcoming_queries", [])):
            if entry.get("id") == schedule_id:
                data["upcoming_queries"][idx]["next_run_at"] = next_run_at
                data["upcoming_queries"][idx]["last_run_started_at"] = now.isoformat()
                data["upcoming_queries"][idx]["updated_at"] = utc_now()
                schedule = data["upcoming_queries"][idx]
                break
        _write_store_unlocked(data)

    if not schedule_id or params is None:
        return

    try:
        record = _build_query_record(params, max_mileage=max_mileage)
        if record["total_hits"] > 0:
            try:
                send_alert_email(record)
                record["email_sent"] = True
            except Exception as exc:
                record["email_error"] = str(exc)

        with STORE_LOCK:
            data = _read_store_unlocked()
            data["past_queries"].insert(0, record)
            for idx, entry in enumerate(data.get("upcoming_queries", [])):
                if entry.get("id") == schedule_id:
                    data["upcoming_queries"][idx]["last_run_at"] = utc_now()
                    data["upcoming_queries"][idx].pop("last_error", None)
                    data["upcoming_queries"][idx].pop("last_error_at", None)
                    break
            _write_store_unlocked(data)
    except Exception as exc:
        with STORE_LOCK:
            data = _read_store_unlocked()
            for idx, entry in enumerate(data.get("upcoming_queries", [])):
                if entry.get("id") == schedule_id:
                    data["upcoming_queries"][idx]["last_error"] = str(exc)
                    data["upcoming_queries"][idx]["last_error_at"] = utc_now()
                    break
            _write_store_unlocked(data)


def scheduler_worker(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            process_due_schedule_once()
        except Exception as exc:
            print(f"[scheduler] error: {exc}")
        stop_event.wait(SCHEDULER_POLL_SECONDS)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def send_alert_email(record: dict) -> None:
    smtp_host = (os.environ.get("SMTP_HOST") or "").strip()
    smtp_port_raw = (os.environ.get("SMTP_PORT") or "").strip()
    smtp_user = (os.environ.get("SMTP_USER") or "").strip()
    smtp_pass = (os.environ.get("SMTP_PASS") or "").strip()
    to_email = (os.environ.get("ALERT_TO_EMAIL") or "").strip()
    from_email = (os.environ.get("ALERT_FROM_EMAIL") or smtp_user).strip()

    if not smtp_host or not smtp_port_raw or not to_email or not from_email:
        raise RuntimeError("Missing SMTP/alert env vars")

    smtp_port = int(smtp_port_raw)
    use_ssl = _env_bool("SMTP_SSL", smtp_port == 465)
    use_tls = _env_bool("SMTP_TLS", smtp_port == 587)

    def human_time(raw: str) -> str:
        if not raw:
            return "-"
        try:
            dt = datetime.fromisoformat(raw)
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return raw

    def esc(value) -> str:
        text = "" if value is None else str(value)
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    hits = record.get("hits", [])
    subject = f"RewardsTicket Alert: {record.get('total_hits', 0)} matching flights"
    created_human = human_time(record.get("created_at", ""))
    lines = [
        f"Created: {created_human}",
        f"Total returned: {record.get('total_returned', 0)}",
        f"Matching hits: {record.get('total_hits', 0)}",
        "",
        "Top matches:",
    ]
    html_rows = []
    for idx, h in enumerate(hits[:20], 1):
        date_value = h.get("date", "-")
        program = h.get("source", "-")
        airlines_or_fn = h.get("airlines", "-") or h.get("flight_numbers", "-")
        origin = h.get("origin", "-")
        destination = h.get("destination", "-")
        mileage = h.get("mileage", "-")
        seats = h.get("seats", "-")
        lines.append(
            f"{idx}. {date_value} | {program} | {airlines_or_fn} | {origin} -> {destination} | {mileage} | seats={seats}"
        )
        html_rows.append(
            "<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;'>{esc(date_value)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;'>{esc(program)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;'>{esc(airlines_or_fn)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;'>{esc(origin)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;'>{esc(destination)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;'>{esc(mileage)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;'>{esc(seats)}</td>"
            "</tr>"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content("\n".join(lines))
    msg.add_alternative(
        f"""\
<html>
  <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111827;background:#f9fafb;padding:20px;">
    <div style="max-width:780px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
      <div style="padding:14px 16px;border-bottom:1px solid #e5e7eb;background:#111827;color:#ffffff;">
        <div style="font-size:16px;font-weight:600;">RewardsTicket Alert</div>
        <div style="font-size:12px;opacity:0.85;margin-top:4px;">{esc(record.get('total_hits', 0))} matching flights found</div>
      </div>
      <div style="padding:14px 16px;">
        <div style="font-size:13px;color:#4b5563;margin-bottom:10px;">
          <strong>Created:</strong> {esc(created_human)}<br/>
          <strong>Total returned:</strong> {esc(record.get('total_returned', 0))}<br/>
          <strong>Matching hits:</strong> {esc(record.get('total_hits', 0))}
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead>
            <tr style="background:#f3f4f6;color:#374151;text-align:left;">
              <th style="padding:8px;">Date</th>
              <th style="padding:8px;">Program</th>
              <th style="padding:8px;">Airlines</th>
              <th style="padding:8px;">From</th>
              <th style="padding:8px;">To</th>
              <th style="padding:8px;text-align:right;">Mileage</th>
              <th style="padding:8px;text-align:right;">Seats</th>
            </tr>
          </thead>
          <tbody>
            {''.join(html_rows) if html_rows else "<tr><td colspan='7' style='padding:10px;color:#6b7280;'>No rows</td></tr>"}
          </tbody>
        </table>
      </div>
    </div>
  </body>
</html>
""",
        subtype="html",
    )

    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)


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
                    "api_limits": get_last_rate_limit(),
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
                # Off-cycle runs from UI should not email unless explicitly requested.
                send_email = bool(payload.get("send_email", False))
                record = _build_query_record(params, max_mileage=max_mileage)
                with STORE_LOCK:
                    data = _read_store_unlocked()
                    data["past_queries"].insert(0, record)
                    _write_store_unlocked(data)

                email_sent = False
                email_error = None
                if send_email and record["total_hits"] > 0:
                    try:
                        send_alert_email(record)
                        email_sent = True
                    except Exception as exc:
                        email_error = str(exc)

                if email_sent:
                    record["email_sent"] = True
                if email_error:
                    record["email_error"] = email_error
                record["api_limits"] = get_last_rate_limit()
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
                max_mileage = int(payload.get("max_mileage", 100000))
                enabled = bool(payload.get("enabled", True))

                upcoming = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "interval_minutes": interval_minutes,
                    "next_run_at": (datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)).isoformat(),
                    "params": params,
                    "max_mileage": max_mileage,
                    "enabled": enabled,
                    "created_at": utc_now(),
                }
                with STORE_LOCK:
                    data = _read_store_unlocked()
                    # Single next-query mode: always replace previous schedule.
                    data["upcoming_queries"] = [upcoming]
                    _write_store_unlocked(data)
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

                with STORE_LOCK:
                    data = _read_store_unlocked()
                before = len(data["upcoming_queries"])
                data["upcoming_queries"] = [
                    q for q in data["upcoming_queries"] if q.get("id") != schedule_id
                ]
                after = len(data["upcoming_queries"])
                if before == after:
                    self._send_json({"error": "Upcoming query not found"}, status=404)
                    return

                with STORE_LOCK:
                    _write_store_unlocked(data)
                self._send_json({"ok": True, "deleted_id": schedule_id})
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        if self.path == "/api/query/schedule/toggle":
            try:
                payload = self._read_json_body()
                enabled = bool(payload.get("enabled", True))
                with STORE_LOCK:
                    data = _read_store_unlocked()
                current = normalize_next_query(data)
                if not current:
                    self._send_json({"error": "No next query configured"}, status=404)
                    return

                now = datetime.now(timezone.utc)
                for idx, q in enumerate(data.get("upcoming_queries", [])):
                    if q.get("id") == current.get("id"):
                        data["upcoming_queries"][idx]["enabled"] = enabled
                        if enabled:
                            interval_minutes = max(1, int(q.get("interval_minutes", 60)))
                            data["upcoming_queries"][idx]["next_run_at"] = (
                                now + timedelta(minutes=interval_minutes)
                            ).isoformat()
                        data["upcoming_queries"][idx]["updated_at"] = utc_now()
                        current = data["upcoming_queries"][idx]
                        break

                with STORE_LOCK:
                    _write_store_unlocked(data)
                self._send_json(current)
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        if self.path == "/api/query/delete":
            try:
                payload = self._read_json_body()
                query_id = (payload.get("id") or "").strip()
                if not query_id:
                    self._send_json({"error": "Missing query id"}, status=400)
                    return

                with STORE_LOCK:
                    data = _read_store_unlocked()
                before = len(data["past_queries"])
                data["past_queries"] = [
                    q for q in data["past_queries"] if q.get("id") != query_id
                ]
                after = len(data["past_queries"])
                if before == after:
                    self._send_json({"error": "Past query not found"}, status=404)
                    return

                with STORE_LOCK:
                    _write_store_unlocked(data)
                self._send_json({"ok": True, "deleted_id": query_id})
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        if self.path == "/api/query/clear":
            try:
                with STORE_LOCK:
                    data = _read_store_unlocked()
                    removed_count = len(data.get("past_queries", []))
                    data["past_queries"] = []
                    _write_store_unlocked(data)
                self._send_json({"ok": True, "removed_count": removed_count})
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        if self.path == "/api/query/schedule/update_params":
            try:
                payload = self._read_json_body()
                params = payload.get("params")
                if not isinstance(params, dict):
                    self._send_json({"error": "Missing params object"}, status=400)
                    return

                with STORE_LOCK:
                    data = _read_store_unlocked()
                current = normalize_next_query(data)
                if not current:
                    self._send_json({"error": "No next query configured"}, status=404)
                    return

                for idx, q in enumerate(data.get("upcoming_queries", [])):
                    if q.get("id") == current.get("id"):
                        data["upcoming_queries"][idx]["params"] = params
                        data["upcoming_queries"][idx]["updated_at"] = utc_now()
                        current = data["upcoming_queries"][idx]
                        break

                with STORE_LOCK:
                    _write_store_unlocked(data)
                self._send_json(current)
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    ensure_store()
    stop_event = threading.Event()
    threading.Thread(target=scheduler_worker, args=(stop_event,), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("RewardsTicket GUI running at http://127.0.0.1:8787")
    server.serve_forever()


if __name__ == "__main__":
    main()
