"""Credential-free diagnostics for the disposable private recovery worker.

Never starts the ordering application, runs SQL, or prints environment values.
"""
import json
import signal
import socket
import subprocess
import threading


def probe():
    result = {"mode": "CREDENTIAL_FREE_PRIVATE_PROBE", "application_started": False,
              "sql_executed": False, "database_credentials_required": False}
    for executable in ("python", "psql", "pg_dump", "pg_restore"):
        completed = subprocess.run([executable, "--version"], check=True,
                                   capture_output=True, text=True, timeout=10)
        result[executable] = completed.stdout.strip()
    try:
        with socket.create_connection(("dpg-db4rjgo473hc738soueg-a", 5432), timeout=5):
            result["private_postgres_tcp_reachable"] = True
    except OSError:
        result["private_postgres_tcp_reachable"] = False
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    probe()
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    print("PRIVATE_CHECK_SHELL_READY: no database credentials; no application startup", flush=True)
    while not stopped.wait(300):
        print("PRIVATE_CHECK_IDLE: operator must suspend this temporary worker after use", flush=True)
