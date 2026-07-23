"""Exercise the real serve-mcp process while a real relay call is slow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--worktree", required=True, type=Path)
    return parser.parse_args()


def timestamp(started: float) -> str:
    wall = datetime.now().astimezone().strftime("%H:%M:%S.%f")[:-3]
    return f"{wall}  +{time.monotonic() - started:05.2f}s"


def describe(payload: dict[str, Any]) -> str:
    request_id = payload.get("id")
    method = payload.get("method")
    if method == "tools/call":
        name = payload.get("params", {}).get("name")
        return f"id={request_id} tools/call {name}"
    if method:
        return f"id={request_id} {method}"
    if "error" in payload:
        return f"id={request_id} error={payload['error']!r}"
    return f"id={request_id} result"


def send(
    stdin: TextIO,
    payload: dict[str, Any],
    *,
    started: float,
    sent_at: dict[int, float],
) -> None:
    request_id = payload.get("id")
    now = time.monotonic()
    if isinstance(request_id, int):
        sent_at[request_id] = now
    print(f"{timestamp(started)}  SEND  {describe(payload)}", flush=True)
    stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    stdin.flush()


def read_json_lines(stdout: TextIO, output: queue.Queue[tuple[float, dict[str, Any]]]) -> None:
    for line in stdout:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            output.put((time.monotonic(), payload))


def read_stderr(stderr: TextIO, output: queue.Queue[str]) -> None:
    for line in stderr:
        output.put(line.rstrip())


def receive(
    output: queue.Queue[tuple[float, dict[str, Any]]],
    *,
    started: float,
    timeout: float,
) -> tuple[float, dict[str, Any]]:
    received_at, payload = output.get(timeout=timeout)
    print(f"{timestamp(started)}  RECV  {describe(payload)}", flush=True)
    return received_at, payload


def verify_module(python: Path, worktree: Path) -> tuple[str, str]:
    probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import hashlib;"
                "from pathlib import Path;"
                "import omnigent.claude_native_bridge as m;"
                "p=Path(m.__file__).resolve();"
                "print(p);"
                "print(hashlib.sha256(p.read_bytes()).hexdigest()[:16])"
            ),
        ],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = probe.stdout.splitlines()
    if len(lines) != 2:
        raise RuntimeError(f"unexpected module probe output: {probe.stdout!r}")
    module_path = Path(lines[0]).resolve()
    root = worktree.resolve()
    try:
        relative = module_path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"interpreter resolved module outside selected worktree: {module_path}"
        ) from exc
    expected_digest = hashlib.sha256(module_path.read_bytes()).hexdigest()[:16]
    if lines[1] != expected_digest:
        raise RuntimeError("module digest changed during verification")
    return str(relative), expected_digest


def terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main() -> int:
    args = parse_args()
    worktree = args.worktree.resolve()
    python = Path(os.path.abspath(args.python))
    demo_dir = Path(__file__).resolve().parent
    relay_script = demo_dir / "relay_host.py"
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    module_path, module_digest = verify_module(python, worktree)

    started = time.monotonic()
    print(f"{args.label}", flush=True)
    print(f"commit: {commit}", flush=True)
    print(f"module: <selected-worktree>/{module_path}", flush=True)
    print(f"module sha256: {module_digest}", flush=True)
    print("scenario: slow relay call, then ping and fast relay call", flush=True)
    print("", flush=True)

    relay = subprocess.Popen(
        [str(python), str(relay_script)],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    serve: subprocess.Popen[str] | None = None
    bridge_dir: Path | None = None
    try:
        if relay.stdout is None or relay.stderr is None:
            raise RuntimeError("relay pipes unavailable")
        bridge_line = relay.stdout.readline().strip()
        if not bridge_line:
            relay_error = relay.stderr.read()
            raise RuntimeError(f"relay did not advertise a bridge directory: {relay_error}")
        bridge_dir = Path(bridge_line)

        serve = subprocess.Popen(
            [
                str(python),
                "-m",
                "omnigent.claude_native_bridge",
                "serve-mcp",
                "--bridge-dir",
                str(bridge_dir),
            ],
            cwd=worktree,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        if serve.stdin is None or serve.stdout is None or serve.stderr is None:
            raise RuntimeError("serve-mcp pipes unavailable")

        responses: queue.Queue[tuple[float, dict[str, Any]]] = queue.Queue()
        errors: queue.Queue[str] = queue.Queue()
        threading.Thread(
            target=read_json_lines,
            args=(serve.stdout, responses),
            daemon=True,
        ).start()
        threading.Thread(
            target=read_stderr,
            args=(serve.stderr, errors),
            daemon=True,
        ).start()

        sent_at: dict[int, float] = {}
        received_at: dict[int, float] = {}
        send(
            serve.stdin,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            started=started,
            sent_at=sent_at,
        )
        init_received, initialized = receive(responses, started=started, timeout=5)
        if initialized.get("id") != 1:
            raise RuntimeError(f"unexpected initialize response: {initialized!r}")
        received_at[1] = init_received

        send(
            serve.stdin,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            started=started,
            sent_at=sent_at,
        )
        send(
            serve.stdin,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "sys_slow", "arguments": {}},
            },
            started=started,
            sent_at=sent_at,
        )
        time.sleep(1)
        send(
            serve.stdin,
            {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
            started=started,
            sent_at=sent_at,
        )
        send(
            serve.stdin,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "sys_fast", "arguments": {}},
            },
            started=started,
            sent_at=sent_at,
        )

        while not {2, 3, 4}.issubset(received_at):
            response_time, payload = receive(responses, started=started, timeout=25)
            request_id = payload.get("id")
            if isinstance(request_id, int):
                received_at[request_id] = response_time

        print("", flush=True)
        print("RESULTS", flush=True)
        for request_id, label in (
            (2, "sys_slow"),
            (3, "ping"),
            (4, "sys_fast"),
        ):
            latency = received_at[request_id] - sent_at[request_id]
            print(f"id {request_id} {label}: answered after {latency:.2f}s", flush=True)
        if not errors.empty():
            print("serve-mcp emitted stderr; see local report", flush=True)
        return 0
    except queue.Empty as exc:
        stderr_lines: list[str] = []
        if serve is not None and serve.stderr is not None:
            while not errors.empty():
                stderr_lines.append(errors.get_nowait())
        raise RuntimeError(f"timed out waiting for serve-mcp; stderr={stderr_lines!r}") from exc
    finally:
        if serve is not None:
            if serve.stdin is not None:
                serve.stdin.close()
            terminate(serve)
        terminate(relay)
        if bridge_dir is not None:
            shutil.rmtree(bridge_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        raise
