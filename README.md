# RewardsTicket

A focused award-seat monitoring tool built around the Seats.aero Partner API.

## Why This Exists

I'm a pro user of seats.aero. But based on real usage, Seats.aero alerts can be unreliable in two important ways:

- the number of alert notifications can be lower than the actual number of currently searchable flights
- there is often a delay between when availability appears in the Seats.aero database and when an alert is sent

This tool exists to reduce that gap. RewardsTicket actively polls Seats.aero data with your criteria so you can detect availability changes earlier than the default broadcast alert timing. I built this tool with two core assumption:

- Seats.aero native alerts are sometimes unreliable and missing valid trips
- proactive polling can surface opportunities before broad alerts reach everyone

## Before You Start

1. Seats.aero API access is required. This tool depends on the Seats.aero Partner API. API access is available to Seats.aero Pro users, so each user needs their own paid access.
2. If you would like to have auto email alerts, you will need  a Google App Password. To generate a Google app-specific password, you'll have to enable 2-Step Verification in your Google account, then create an app password here via this link [Create and manage your app passwords.](https://myaccount.google.com/apppasswords)The  16-character password value should be placed into our .env file.

## What Problems It Solves

- Run an **off-cycle query** immediately when you do not want to wait.
- Keep a **local run history** so you can compare snapshots.
- Apply **program filters** and date sorting in results.
- Configure a **next query** profile and reuse criteria quickly.
- Send HTML email alerts from your own SMTP setup.

## Current Features

- Query criteria supp:
  - origin airports
  - destination airports
  - start/end date
  - cabin
  - direct-flights-only toggle
  - max mileage
- Past query snapshots
- Next query section (interval + re-apply)
- Program filter and date sorting in results
- HTML email alert&#x20;

>
> This app is intentionally opinionated and built around my personal workflow.
> For example: direct flights only, business-cabin focus, and no support for creating multiple query tasks at the same time.
> Since this project is open source, you can extend it based on the Seats.aero API documentation for your own needs.

## Important Behavior

- `Off-cycle Run` is manual and **does not send email by default**.
- Data is persisted in local file: `data/store.json`.

## Project Structure

- `app.py`: Python backend + API routes + Seats query + email sender
- `ui/index.html`: UI (HTML/CSS/JS)
- `data/store.json`: local persistence
- `.env`: runtime secrets/config (not committed)

## Quick Start

- Clone and enter the project

```bash
git clone <your-repo-url>
cd RewardsTicket
```

- Create `.env` from `.env.example`

```bash
cp .env.example .env
```

- Fill in your values
- Run the app

```bash
python3 app.py
```

- Open the local url

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
