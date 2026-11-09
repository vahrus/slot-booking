#!/usr/bin/env python3
"""Give /app/data to the slotly user, then run the server without root.

The container starts as root so a Docker named volume mounted at /app/data
can be assigned to uid 1000. Modes are left unchanged: this does not chmod 777.
Uvicorn then runs as slotly.
"""

import os
import pwd
import sys

DATA_DIR = "/app/data"
APP_USER = "slotly"


def command_with_trusted_proxy(argv: list[str]) -> list[str]:
    """Trust X-Forwarded-Proto only for proxies named by FORWARDED_ALLOW_IPS.

    An empty or missing variable leaves Uvicorn's default, 127.0.0.1 and ::1.
    The value ``*`` is not added here.
    """
    allowed = os.environ.get("FORWARDED_ALLOW_IPS", "").strip()
    if not allowed or "--forwarded-allow-ips" in argv:
        return argv
    names = [os.path.basename(part) for part in argv[:3]]
    if "uvicorn" not in names:
        return argv
    return [*argv, "--proxy-headers", "--forwarded-allow-ips", allowed]


def _give_data_directory(account: pwd.struct_passwd) -> None:
    os.makedirs(DATA_DIR, mode=0o755, exist_ok=True)
    for current, dir_names, file_names in os.walk(DATA_DIR):
        os.chown(current, account.pw_uid, account.pw_gid)
        for name in [*dir_names, *file_names]:
            os.chown(os.path.join(current, name), account.pw_uid, account.pw_gid)


def drop_privileges() -> None:
    if os.geteuid() != 0:
        return
    account = pwd.getpwnam(APP_USER)
    _give_data_directory(account)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.environ["HOME"] = account.pw_dir


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: entrypoint.py <command> [args...]")
    command = command_with_trusted_proxy(sys.argv[1:])
    drop_privileges()
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
