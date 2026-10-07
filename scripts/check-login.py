#!/usr/bin/env python3
"""Check a Bright login the way the integration signs in, without showing it.

Asks for the email and password (the password is not echoed), posts them to
Glowmarkt's /auth exactly as custom_components/bright_meter/api.py does, and
prints only the outcome: the HTTP status, whether Bright accepted it, and
whether stray spaces or capitals made the difference. Then it makes the same
calls the setup makes (sites, meter streams, how far each stream reaches) and
prints each answer's HTTP status, by stream type only: no ids, no names.
Nothing is stored or sent anywhere else.

  python3 scripts/check-login.py
"""

from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.request

AUTH_URL = "https://api.glowmarkt.com/api/v0-1/auth"
APPLICATION_ID = "b0f1b774-a586-4f72-9edd-27ead8aa7a8d"  # the Bright app's public id


BASE = "https://api.glowmarkt.com/api/v0-1"
SUPPORTED = {
    "electricity.consumption",
    "electricity.consumption.cost",
    "electricity.export",
    "gas.consumption",
    "gas.consumption.cost",
}


def get(path: str, token: str) -> tuple[int, object]:
    request = urllib.request.Request(
        f"{BASE}{path}",
        headers={
            "Content-Type": "application/json",
            "applicationId": APPLICATION_ID,
            "token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read() or b"null")
        except ValueError:
            body = None
        return err.code, body


def token_for(username: str, password: str) -> str | None:
    request = urllib.request.Request(
        AUTH_URL,
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json", "applicationId": APPLICATION_ID},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"{}").get("token")


def discover(username: str, password: str) -> None:
    """The setup's calls, one line each: what was asked, what Bright answered."""
    token = token_for(username, password)
    if not token:
        print("No token returned.")
        return
    status, sites = get("/virtualentity", token)
    count = len(sites) if isinstance(sites, list) else "?"
    print(f"\nSites (GET /virtualentity): HTTP {status}, {count} site(s)")
    if not isinstance(sites, list):
        return
    for n, site in enumerate(sites, 1):
        status, doc = get(f"/virtualentity/{site.get('veId')}/resources", token)
        resources = (doc or {}).get("resources", []) if isinstance(doc, dict) else []
        print(f"  Site {n} streams: HTTP {status}, {len(resources)}")
        for res in resources:
            classifier = res.get("classifier") or "?"
            used = "used" if classifier in SUPPORTED else "ignored"
            active = "" if res.get("active", True) else ", inactive"
            status, body = get(f"/resource/{res.get('resourceId')}/last-time", token)
            reach = (body or {}).get("data", {}) if isinstance(body, dict) else {}
            stamp = reach.get("lastTs") if isinstance(reach, dict) else None
            detail = "has data" if stamp else "no data yet"
            if status >= 400:
                detail = f"error {body!r}"[:80]
            print(f"    {classifier} ({used}{active}): HTTP {status}, {detail}")


def sign_in(username: str, password: str) -> tuple[int, bool]:
    request = urllib.request.Request(
        AUTH_URL,
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json", "applicationId": APPLICATION_ID},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read() or b"{}")
            return response.status, bool(body.get("valid") and body.get("token"))
    except urllib.error.HTTPError as err:
        return err.code, False


def main() -> int:
    username = input("Bright email: ")
    password = getpass.getpass("Bright password (hidden): ")

    status, ok = sign_in(username, password)
    print(f"As typed: HTTP {status}, {'ACCEPTED' if ok else 'rejected'}")
    if ok:
        print("The login works. Enter it in Home Assistant exactly as typed here.")
        discover(username, password)
        return 0

    tried = {(username, password)}
    for label, candidate in [
        ("email without surrounding spaces", (username.strip(), password)),
        ("email in lower case", (username.strip().lower(), password)),
        ("password without surrounding spaces", (username.strip(), password.strip())),
    ]:
        if candidate in tried:
            continue
        tried.add(candidate)
        status, ok = sign_in(*candidate)
        print(f"With {label}: HTTP {status}, {'ACCEPTED' if ok else 'rejected'}")
        if ok:
            print(f"Found it: use the {label} in Home Assistant.")
            return 0

    print(
        "\nBright rejected every variant. Likely causes:\n"
        "- the account signs in with Apple or Google, so it has no Bright password:\n"
        "  set one in the Bright app (Settings, or Forgotten password on its sign-in)\n"
        "- the browser login was a different service (a supplier's site, say):\n"
        "  sign out of the Bright app and back in with the same details\n"
        "- too many attempts: wait a few minutes and try again"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
