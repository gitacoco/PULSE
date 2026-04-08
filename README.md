# RewardsTicket

A focused award-seat monitoring tool built around the Seats.aero Partner API.

## Why This Exists

I'm a pro user of seats.aero. But based on real usage, Seats.aero alerts can be unreliable in two important ways:

- the number of alert notifications can be lower than the actual number of currently searchable flights
- there is often a delay between when availability appears in the Seats.aero database and when an alert is sent

This tool exists to reduce that gap. RewardsTicket actively polls Seats.aero data with your criteria so you can detect availability changes earlier than the default broadcast alert timing. I built this tool with two core assumption:

- Seats.aero native alerts are sometimes unreliable/unstable for time-sensitive booking workflows
- proactive polling can surface opportunities before broad alerts reach everyone

## Before You Start

1. Seats.aero API access is required.

- This tool depends on the Seats.aero Partner API.
- API access is available to Seats.aero Pro users, so each user needs their own paid access.

1. Gmail auto-email requires a Google App Password.

- To use automatic email sending from this tool, generate a Google app-specific password.
- Prerequisite: enable 2-Step Verification in your Google account:
  - `Google Account > Security & sign-in > 2-Step Verification`
- Then create a 16-character app password here:
  - [Create and manage your app passwords](https://myaccount.google.com/apppasswords)
- Put that 16-character value into:
  - `.env` -> `SMTP_PASS=...`

1. Query model and assumption.

- This tool uses the Seats.aero API to poll their [Cached Search](https://developers.seats.aero/reference/cached-search) more frequently.
- Working assumption: this polling approach is more reliable for time-sensitive workflows than relying only on native alert delivery timing.

## What Problems It Solves

- Run an **off-cycle query** immediately when you do not want to wait.
- Keep a **local run history** so you can compare snapshots.
- Apply **program filters** and date sorting in results.
- Configure a **next query** profile and reuse criteria quickly.
- Send HTML email alerts from your own SMTP setup.

## Current Features

- Query criteria:
  - origin airports
  - destination airports
  - start/end date
  - cabin
  - direct-flights-only toggle
  - max mileage
- Past query snapshots (with delete)
- Next query section (interval + re-apply)
- Program filter and date sorting in results
- HTML email alert template

## Important Behavior

- `Off-cycle Run` is manual and **does not send email by default**.
- Email is sent only when backend receives `send_email=true`.
- Data is persisted in local file: `data/store.json`.

## Project Structure

- `app.py`: Python backend + API routes + Seats query + email sender
- `ui/index.html`: UI (HTML/CSS/JS)
- `data/store.json`: local persistence
- `.env`: runtime secrets/config (not committed)

## Quick Start

1. Clone and enter the project

```bash
git clone <your-repo-url>
cd RewardsTicket
```

1. Create `.env` from `.env.example`

```bash
cp .env.example .env
```

1. Fill in your values (especially `SEATS_API_KEY` and SMTP vars)
2. Run the app

```bash
python3 app.py
```

1. Open:

- `http://127.0.0.1:8787`

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

## Open-Source Safety Checklist

Before publishing this repo:

- Rotate any existing API/SMTP credentials.
- Confirm `.env` is ignored by git.
- Commit only `.env.example`, never real `.env`.
- Consider rewriting git history if secrets were ever committed.

## Known Limitations

- Local JSON persistence only (`data/store.json`)
- No multi-user auth model
- Scheduling model is currently local-process oriented

## Roadmap Ideas

- Deduplicated alert emails
- Optional database backend (SQLite/Postgres)
- Docker packaging
- Improved scheduler/worker deployment mode

## License

Use your preferred license (MIT is a common choice).
