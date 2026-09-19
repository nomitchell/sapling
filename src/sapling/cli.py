"""Launch the local services, research runtime, workers and browser interface."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def stop(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    parser = argparse.ArgumentParser(description="Sapling — local autonomous research")
    parser.add_argument(
        "--native", action="store_true", help="Run with local SQLite and host execution; Docker is optional"
    )
    parser.add_argument("--dev", action="store_true", help="Use the Next development server")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--no-services", action="store_true", help="Use already-running PostgreSQL/search services"
    )
    parser.add_argument(
        "--api-only", action="store_true", help="Start the research runtime without the web server"
    )
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    os.chdir(project)
    from dotenv import load_dotenv

    load_dotenv(project / ".env")
    processes = []
    if args.native:
        (project / ".sapling").mkdir(exist_ok=True)
        os.environ["SAPLING_DATABASE_URL"] = "sqlite:///" + (project / ".sapling/native.sqlite3").as_posix()
        os.environ["SAPLING_EXECUTION_BACKEND"] = "process"
    if not args.no_services and not args.native:
        docker = shutil.which("docker")
        if not docker or subprocess.run([docker, "info"], capture_output=True).returncode:
            parser.exit(1, "Docker Desktop is not running. Start Docker Desktop, then run sapling again.\n")
        subprocess.run([docker, "compose", "up", "-d", "--wait"], check=True)
    if not args.api_only:
        if not (project / "web" / "node_modules").exists():
            parser.exit(1, "Install the web dependencies first: cd web && npm install\n")
        if not args.dev and not (project / "web" / ".next" / "BUILD_ID").exists():
            parser.exit(1, "Build the interface first: cd web && npm run build (or use sapling --dev).\n")
    try:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "sapling.api:app", "--host", "127.0.0.1", "--port", "8000"],
                cwd=project,
                creationflags=flags,
            )
        )
        if not args.api_only:
            node = shutil.which("node")
            if not node:
                parser.exit(1, "Node.js is required for the web interface.\n")
            processes.append(
                subprocess.Popen(
                    [
                        node,
                        str(project / "web/node_modules/next/dist/bin/next"),
                        "dev" if args.dev else "start",
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        "3000",
                    ],
                    cwd=project / "web",
                    creationflags=flags,
                )
            )
        url = "http://127.0.0.1:8000/docs" if args.api_only else "http://127.0.0.1:3000"
        for _ in range(120):
            if any(p.poll() is not None for p in processes):
                parser.exit(1, "A Sapling service exited. Check the log above.\n")
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.5)
        else:
            parser.exit(1, "Sapling did not become ready within 60 seconds.\n")
        print(f"Sapling is running at {url}. Press Ctrl+C to stop the runtime.")
        if not args.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Sapling…")
    finally:
        for process in reversed(processes):
            stop(process)


if __name__ == "__main__":
    main()
