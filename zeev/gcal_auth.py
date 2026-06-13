#!/usr/bin/env python3
"""One-time Google Calendar OAuth flow. Run this once to create gcal_token.json."""
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CREDS = BASE_DIR / "data" / "gcal_credentials.json"
TOKEN = BASE_DIR / "data" / "gcal_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

if not CREDS.exists():
    print(f"ERROR: credentials file not found at {CREDS}")
    print("Download it from Google Cloud Console (OAuth2 Desktop app) and place it there.")
    raise SystemExit(1)

# Find a free port
with socket.socket() as s:
    s.bind(('', 0))
    PORT = s.getsockname()[1]

print(f"\nOn your LAPTOP, open a new terminal and run:")
print(f"  ssh -L {PORT}:localhost:{PORT} ragnar@$(hostname -I | awk '{{print $1}}')")
print(f"\n(Pi IP is probably: {socket.gethostbyname(socket.gethostname())})")
input("\nPress Enter once the tunnel is open...")

from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(str(CREDS), SCOPES)
creds = flow.run_local_server(port=PORT, open_browser=False)
TOKEN.write_text(creds.to_json())
print(f"\nSuccess! Token saved to {TOKEN}")

# Quick test
from googleapiclient.discovery import build
import datetime as dt
svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
now = dt.datetime.now(dt.timezone.utc)
end = now + dt.timedelta(days=1)
result = svc.events().list(
    calendarId="primary",
    timeMin=now.isoformat(),
    timeMax=end.isoformat(),
    singleEvents=True,
    orderBy="startTime",
    maxResults=5,
).execute()
events = result.get("items", [])
print(f"\nToday's events ({len(events)} found):")
for e in events:
    start = e["start"].get("dateTime", e["start"].get("date", ""))
    print(f"  - {start}: {e.get('summary', '(no title)')}")
