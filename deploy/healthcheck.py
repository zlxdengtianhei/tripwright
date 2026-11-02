"""Container health: the application answers /healthz (liveness; tenant problems show on /readyz and in the log)."""
import json
import os
from urllib.request import urlopen

port = os.environ.get("PORT") or "8000"
with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
    if response.status != 200 or json.load(response).get("status") != "ok":
        raise SystemExit(f"Unexpected /healthz answer: {response.status}")
