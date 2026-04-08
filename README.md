# PULSE (Polling Unlocks Live Seat Exposure)
A focused award-seat monitoring tool built around the Seats.aero [Partner API](https://docs.seats.aero/article/68-seatsaero-pro-api-access-limits-and-usage).

## Why This Exists

I'm a pro user of seats.aero. But based on real usage, Seats.aero alerts can be unreliable in two important ways:

- the number of alert notifications can be lower than the actual number of currently searchable flights
- there is often a delay between when availability appears in the Seats.aero database and when an alert is sent

This tool exists to reduce that gap. PULSE actively polls Seats.aero data with your criteria so you can detect availability changes earlier than the default broadcast alert timing. I built this tool with two core assumption:

- Seats.aero native alerts are sometimes unreliable and missing valid trips
- proactive polling can surface opportunities before broad alerts reach everyone

## Before You Start

1. This tool depends on the Seats.aero Partner API. API access is available to [Seats.aero](https://seats.aero/) Pro users, so each user needs their own paid access.
2. If you would like to have auto email alerts, you will need  a Google App Password. To generate a Google app-specific password, you'll have to enable 2-Step Verification in your Google account, then create an app password here via this link [Create and manage your app passwords.](https://myaccount.google.com/apppasswords)The  16-character password value should be placed into our .env file.

## Current Features

- Query criteria:
  - origin airports
  - destination airports
  - start/end date
  - cabin
  - direct-flights-only (fixed ON in current release)
  - max mileage cap
- Run an **off-cycle query** immediately when you do not want to wait. `Off-cycle Run` is manual and does not send email.
- Keep a **local run history** so you can compare snapshots.
- Configure a **next query** profile and reuse criteria quickly.
- Apply **program filters** and date sorting in results.
- Send HTML email alerts from your own SMTP setup.&#x20;
- Email dedupe rule: an email is sent only when the current run has a higher hit count and includes newly added results versus the previous comparable run (same params + max mileage). If results are fewer or unchanged, no email is sent.

> This app is intentionally opinionated and built around my personal workflow.
> For example: direct flights only, business-cabin focus, and no support for creating multiple query tasks at the same time.
> Since this project is open source, you can extend it based on the Seats.aero API documentation for your own needs.

## Quick Start

- Fork this repository to your own GitHub account (recommended for customization/contribution).
- Then clone your fork:

```bash
git clone https://github.com/<your-username>/PULSE.git
cd PULSE
```

- If you only want local usage and do not plan to contribute, direct clone is also fine:

```bash
git clone https://github.com/gitacoco/PULSE.git
cd PULSE
```

- Rename the `.env.example` file to `.env`

```bash
cp .env.example .env
```

- Fill in your values
- Run the app

```bash
python3 app.py
```

- Open the local url:
- `http://127.0.0.1:8787`

## Project Structure

- `app.py`: Python backend + API routes + Seats query + email sender
- `ui/index.html`: UI (HTML/CSS/JS)
- `data/store.json`: local persistence. The app auto-creates this file at startup.
- `.env`: runtime secrets/config (not committed)

## Runtime Environment

- This is **not** a hosted web app/SaaS.
- It is a **local-first monitoring service**:
  - a long-running Python process (`app.py`)
  - plus a browser UI served at `http://127.0.0.1:8787`
- To keep monitoring reliable, run it on an always-on machine.
- Recommended environment: a non-sleeping local machine such as a **Mac mini**.
- Locking the screen is fine, but system sleep will pause the process.

## Environment Variables

| Variable           |  Required | Description                               |
| ------------------ | --------: | ----------------------------------------- |
| `SEATS_API_KEY`    |       Yes | Seats.aero Partner API key                |
| `SMTP_HOST`        | For email | SMTP host, e.g. `smtp.gmail.com`          |
| `SMTP_PORT`        | For email | SMTP port (`587` TLS or `465` SSL)        |
| `SMTP_USER`        | For email | SMTP username                             |
| `SMTP_PASS`        | For email | SMTP password / app password              |
| `ALERT_TO_EMAIL`   | For email | Recipient address                         |
| `ALERT_FROM_EMAIL` | For email | Sender address                            |
| `SMTP_TLS`         |  Optional | `true/false` (default inferred from port) |
| `SMTP_SSL`         |  Optional | `true/false` (default inferred from port) |

## Safety Checklist

If you plan to publish the repo, be sure to:

- Confirm `.env` is ignored by git.
- Confirm `store.json`is ignored by git.

## Known Limitations

- Local JSON persistence only (`data/store.json`)
- No multi-user auth model
- Scheduling model is currently local-process oriented

## Refresh Model and Polling Rationale

Based on public comments from the Seats.aero founder and community discussions, the practical refresh threshold for Search / Explore / Alerts is often described as around 3 hours.

Important constraint:
- This project does **not** bypass Seats.aero data limits.
- We query the same Seats.aero **Cached Search** layer.

The key uncertainty is timing transparency:
- Seats.aero has not publicly documented exact refresh timestamps.
- It is unclear whether all programs refresh on one global 3-hour boundary, or in staggered batches.

That uncertainty creates room for polling:
- If updates are staggered by program/route/carrier, higher-frequency polling can get us closer to newly available data sooner.
- Public wording such as "continually updating award availability in the background" suggests ongoing background updates, not necessarily one synchronized global refresh moment.

From an engineering perspective, with 80k+ routes and many programs, a staggered queue-based refresh model is generally more plausible than a single synchronized full refresh cycle.

Even under a conservative alternative (queue crawling, but database publication on larger batch intervals), more frequent polling still helps us minimize delivery latency on our side. We can not get "fresher-than-source" data, but we can get closer to "first moment the updated data becomes available to query."

## License

This project is licensed under the MIT License.
See [LICENSE](./LICENSE) for details.
