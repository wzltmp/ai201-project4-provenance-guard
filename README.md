# Provenance Guard

A pluggable backend service that helps creative-sharing platforms protect **attribution and trust** by classifying submitted content (writing, music, art) as human-made vs. AI-generated, scoring confidence in that classification, surfacing a transparency label to audiences, and giving creators a path to appeal misclassifications.

> The goal is **transparency and creator recourse**, not policing creativity.

## Status

🚧 Early setup. Core service, transparency labels, rate limiting, audit log, and appeals are in progress.

## Stack

| Component | Tool |
|---|---|
| API framework | Flask |
| Detection signal 1 | Groq (`llama-3.3-70b-versatile`) |
| Detection signal 2 | Stylometric heuristics (pure Python) |
| Rate limiting | Flask-Limiter |
| Audit log | SQLite (built-in) |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
pip install -r requirements.txt

cp .env.example .env               # then add your Groq key
# get a free key at https://console.groq.com/keys
```

Run:

```bash
flask --app app run   # (coming soon)
```

## License

MIT
