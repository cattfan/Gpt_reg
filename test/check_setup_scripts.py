"""Check Windows setup/start scripts are repeatable and process-safe."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    setup = (ROOT / "setup.bat").read_text(encoding="utf-8").lower()
    start = (ROOT / "start.bat").read_text(encoding="utf-8").lower()
    guard_path = ROOT / "scripts" / "prepare_web_port.ps1"
    guard = guard_path.read_text(encoding="utf-8").lower() if guard_path.exists() else ""
    failures: list[str] = []

    reuse_marker = 'if exist "%python%"'
    create_marker = "py -3.11 -m venv .venv311"
    if reuse_marker not in setup or setup.index(reuse_marker) > setup.index(create_marker):
        failures.append("setup khong tai su dung venv truoc khi tao moi")
    if 'set "python=.venv311\\scripts\\python.exe"' not in setup:
        failures.append("setup khong khoa interpreter trong venv")
    if "activate.bat" in setup:
        failures.append("setup con phu thuoc activate.bat")
    for command in (
        '"%python%" -m pip install -e .',
        '"%python%" -m gpt_reg migrate',
        '"%python%" test\\run_all.py',
    ):
        if command not in setup:
            failures.append(f"setup khong dung venv: {command}")

    if "netstat -ano" in start or "taskkill /f /pid %%a" in start:
        failures.append("start van kill moi process dang giu port")
    if "pause" in start:
        failures.append("start con pause lam treo process khi chay nen")
    for marker in (
        "prepare_web_port.ps1",
        "-m gpt_reg web",
        "-m gpt_reg migrate",
        "static\\app\\index.html",
    ):
        if marker not in start:
            failures.append(f"start thieu preflight/process guard: {marker}")
    for marker in (
        "get-nettcpconnection",
        "parentprocessid",
        "resolve-path",
        "stop-process",
        "expectedpython",
    ):
        if marker not in guard:
            failures.append(f"port guard thieu ownership check: {marker}")

    for failure in failures:
        print(f"[fail] {failure}")
    print("[fail] setup scripts" if failures else "[ok] setup scripts")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
