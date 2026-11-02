"""Loopback-only, single-worker launcher with race-free port selection."""
import argparse
import os
from pathlib import Path
import socket
import threading

import uvicorn


def bind_local_port(port=8000, attempts=20):
    for candidate in range(port, min(port + attempts, 65536)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", candidate))
            sock.listen(128)
            return sock
        except OSError:
            sock.close()
    raise OSError(f"No available localhost port in {port}–{min(port + attempts - 1, 65535)}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--stop", action="store_true", help="Request graceful shutdown on --port")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    # Keep launcher control state beside the selected database, including in tests.
    configured_db = os.getenv("PROOFKIT_DB", str(Path(__file__).parent / "data" / "jobs.sqlite3"))
    if not configured_db.strip() or configured_db == ":memory:":
        parser.error("PROOFKIT_DB must name a persistent SQLite file.")
    db_path = Path(configured_db)
    control_dir = db_path.resolve().parent / "runtime"
    control_dir.mkdir(parents=True, exist_ok=True)
    if args.stop:
        (control_dir / f"stop-{args.port}").touch()
        print(f"Shutdown requested for localhost:{args.port} (if launched with run_demo.py).")
        return
    try:
        sock = bind_local_port(args.port)
    except OSError as exc:
        parser.exit(1, f"{exc}\n")
    with sock:
        port = sock.getsockname()[1]
        stop_file = control_dir / f"stop-{port}"
        stop_file.unlink(missing_ok=True)
        print(f"SignalDesk: http://127.0.0.1:{port} · Ctrl+C to stop", flush=True)
        config = uvicorn.Config("app:create_app", factory=True, workers=1,
                                timeout_graceful_shutdown=40, access_log=False)
        server = uvicorn.Server(config)
        finished = threading.Event()

        def watch_stop_request():
            while not finished.wait(0.2):
                if stop_file.exists():
                    server.should_exit = True
                    return

        threading.Thread(target=watch_stop_request, daemon=True).start()
        try:
            server.run(sockets=[sock])
        except KeyboardInterrupt:
            pass
        finally:
            finished.set()
            stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
