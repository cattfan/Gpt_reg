"""Bắt bộ header mặc định (và THỨ TỰ) mà curl_cffi gửi cho từng impersonate.

Chạy tay khi cần cập nhật `gpt_reg/fingerprint.py`:

    .venv311\\Scripts\\python test\\probe_default_headers.py

Dùng server HTTP local nên không cần mạng/proxy. Thứ tự header cũng là một phần
vân tay, nên bảng này là nguồn sự thật để giữ `Profile.user_agent` khớp với
`impersonate` — sai lệch là anti-bot bắt được.
"""

from __future__ import annotations

import socket
import threading

from curl_cffi import requests

TARGETS = ("chrome120", "chrome124", "chrome131", "chrome136", "chrome145", "safari18_0")


def _capture_once(port_box: list[int], captured: list[str]) -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_box.append(srv.getsockname()[1])
    conn, _ = srv.accept()
    data = b""
    conn.settimeout(3)
    try:
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
    except Exception:
        pass
    captured.append(data.decode("latin-1"))
    try:
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    except Exception:
        pass
    conn.close()
    srv.close()


def main() -> int:
    for target in TARGETS:
        port_box: list[int] = []
        captured: list[str] = []
        thread = threading.Thread(target=_capture_once, args=(port_box, captured), daemon=True)
        thread.start()
        while not port_box:
            pass

        session = requests.Session(impersonate=target)
        session.trust_env = False
        session.proxies = {"http": "", "https": ""}
        try:
            session.get(f"http://127.0.0.1:{port_box[0]}/", timeout=5)
        except Exception:
            pass
        finally:
            session.close()
        thread.join(timeout=5)

        print(f"=== {target} ===")
        if not captured:
            print("  (không bắt được)")
            continue
        lines = [ln for ln in captured[0].split("\r\n")[1:] if ln.strip()]
        for line in lines:
            name = line.split(":", 1)[0].lower()
            if name in ("user-agent", "sec-ch-ua", "sec-ch-ua-platform", "accept-language"):
                print(f"  {line[:110]}")
        print(f"  thứ tự: {' → '.join(ln.split(':', 1)[0] for ln in lines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
