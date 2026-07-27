"""Kiểm tra cầu nối SSE giữa worker thread và event loop.

Manager phát event từ daemon thread, còn `asyncio.Queue` thuộc event loop.
`put_nowait` gọi thẳng từ thread khác không đánh thức coroutine đang chờ, nên
event nằm im tới nhịp timeout 15s kế tiếp — UI trông như bị treo. Test này bắt
đúng lỗi đó bằng cách đo độ trễ qua một server uvicorn thật + socket có timeout.
"""

from __future__ import annotations

import socket
import threading
import time
from http.client import HTTPConnection


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    import uvicorn

    from gpt_reg.web.server import reg_manager

    failures: list[str] = []
    port = _free_port()
    config = uvicorn.Config(
        "gpt_reg.web.server:app", host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        print("[fail] sse: server không khởi động")
        return 1

    sock = None
    try:
        bootstrap = HTTPConnection("127.0.0.1", port, timeout=5)
        bootstrap.request("GET", "/")
        bootstrap_response = bootstrap.getresponse()
        bootstrap_response.read()
        cookie = (bootstrap_response.getheader("Set-Cookie") or "").split(";", 1)[0]
        cache_control = bootstrap_response.getheader("Cache-Control") or ""
        bootstrap.close()
        if cookie.startswith("gptreg_session="):
            failures.append("root vẫn cấp session cookie auth")
        if "no-store" not in cache_control:
            failures.append("HTML thiếu Cache-Control: no-store")

        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(
            f"GET /api/sse HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Accept: text/event-stream\r\nConnection: keep-alive\r\n\r\n".encode()
        )
        sock.settimeout(5)

        # Đọc tới khi thấy dòng hello, rồi phát event từ thread khác.
        buffer = b""
        got_hello = False
        while time.monotonic() < time.monotonic() + 5:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if b"hello" in buffer:
                got_hello = True
                break
        if not got_hello:
            failures.append("không nhận được hello")

        reg_manager._emit({"type": "job", "job_id": "x1", "status": "running"})

        started = time.monotonic()
        payload_seen = False
        while time.monotonic() - started < 4:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            if b"x1" in chunk:
                payload_seen = True
                break
        elapsed = time.monotonic() - started
        if not payload_seen:
            failures.append("không nhận được event x1")
        elif elapsed >= 3.0:
            failures.append(f"event trễ {elapsed:.1f}s — cầu nối thread hỏng")
    except Exception as exc:
        failures.append(f"lỗi kết nối: {type(exc).__name__}: {exc}")
    finally:
        if sock is not None:
            sock.close()
        server.should_exit = True
        thread.join(timeout=5)

    time.sleep(0.2)
    if reg_manager._listeners:
        failures.append(f"listener không được gỡ: còn {len(reg_manager._listeners)}")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] sse" if failures else "[ok] sse")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
