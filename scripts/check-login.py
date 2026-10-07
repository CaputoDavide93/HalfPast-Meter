#!/usr/bin/env python3
"""Check a Bright login the way the integration signs in, without showing it.

Asks for the email and password (the password is not echoed), posts them to
Glowmarkt's /auth exactly as custom_components/bright_meter/api.py does, and
prints only the outcome: the HTTP status, whether Bright accepted it, and
whether stray spaces or capitals made the difference. Nothing is stored or
sent anywhere else.

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
