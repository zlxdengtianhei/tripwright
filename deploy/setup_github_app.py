"""Print a ten-minute, one-use GitHub App setup link; never print any application credential.

    .venv/bin/python deploy/setup_github_app.py --url https://trip.example.org [--ssh my-server]

Same arguments and token sources as check_https.py. The user, in their own GitHub account, opens the link and
creates the app.
"""
import httpx2

from check_https import remote_token, target

arguments = target()
with httpx2.Client(timeout=20) as client:
    response = client.post(arguments.url + "/setup/github-app/session",
                           headers={"Authorization": "Bearer " + remote_token(arguments)})
if response.status_code != 200:
    raise SystemExit(f"Cannot create GitHub setup link (HTTP {response.status_code}); check remote configuration")
print(response.json()["url"])
