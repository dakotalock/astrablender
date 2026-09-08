#!/usr/bin/env python3
"""Create local deployment settings without exposing a password in process arguments."""
import argparse
import getpass
import hashlib
import os
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]


def valid_domain(value):
    value = value.strip().lower()
    if len(value) > 253 or "." not in value or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError("Enter a DNS hostname, such as blender.example.org, without https:// or a path.")
    return value


def write_config(domain, username, password, path):
    domain = valid_domain(domain)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", username):
        raise ValueError("Use letters, numbers, underscores or hyphens for the username.")
    if len(password) < 16 or len(password) > 1024:
        raise ValueError("Use a password or passphrase of 16–1024 characters.")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    text = (f"WORKSTATION_DOMAIN={domain}\nLOGIN_USER={username}\n"
            f"PASSWORD_HASH=scrypt:{salt}:{digest}\nSESSION_KEY={secrets.token_hex(32)}\n")
    # Exclusive creation: never silently rotate credentials or overwrite settings.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", help="DNS hostname pointing to the server")
    parser.add_argument("--username", default="studio")
    args = parser.parse_args()
    if (ROOT / ".env").exists():
        parser.exit(1, ".env already exists; keeping the current credentials.\n")
    domain = args.domain or input("Workstation hostname: ")
    password = getpass.getpass("Choose a workstation password (at least 16 characters): ")
    if password != getpass.getpass("Repeat password: "):
        parser.exit(1, "Passwords did not match. No settings were written.\n")
    try:
        write_config(domain, args.username, password, ROOT / ".env")
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    print("Settings saved. No server has been created and nothing has been deployed.")
    print("On the selected Docker host: docker compose up -d --build")
