"""Chạy toàn bộ `check_*.py` trong một lệnh.

Chỉ gom `check_*` — `probe_*` và `smoke_*` cần mạng thật / browser thật nên phải
gọi tay.

Buộc UTF-8 trước khi in bất cứ thứ gì: console Windows mặc định cp1252, mọi
thông báo fail tiếng Việt sẽ ném `UnicodeEncodeError` và làm test **tự chết**
thay vì báo lỗi thật (đã gặp).
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    _reconfig = getattr(_stream, "reconfigure", None)
    if _reconfig is not None:
        try:
            _reconfig(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(path: Path) -> tuple[int, float]:
    spec = importlib.util.spec_from_file_location(f"_check_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    started = time.monotonic()
    try:
        spec.loader.exec_module(module)
        # Hai quy ước cùng tồn tại: `main()` đồng bộ và `_run()` async (test nào
        # phải await page/asyncio). Runner chấp cả hai thay vì bắt sửa 20 file.
        entry = getattr(module, "main", None) or getattr(module, "_run", None)
        if entry is None:
            raise AttributeError("thiếu main() hoặc _run()")
        failures = asyncio.run(entry()) if inspect.iscoroutinefunction(entry) else entry()
    except SystemExit as exc:  # main() gọi sys.exit
        failures = int(exc.code or 0)
    except Exception as exc:
        print(f"[error] {path.name} nổ: {type(exc).__name__}: {exc}")
        failures = 1
    return int(failures or 0), time.monotonic() - started


def main() -> int:
    only = [a.lower() for a in sys.argv[1:]]
    files = sorted(Path(__file__).resolve().parent.glob("check_*.py"))
    if only:
        files = [f for f in files if any(o in f.stem.lower() for o in only)]
    if not files:
        print("không có test nào khớp")
        return 1

    failed: list[str] = []
    total = 0
    for path in files:
        failures, took = _run(path)
        total += failures
        status = "FAIL" if failures else "ok"
        print(f"  {status:>4}  {path.stem:<28} {took:5.2f}s")
        if failures:
            failed.append(path.stem)

    print()
    if failed:
        print(f"[FAIL] {len(failed)}/{len(files)} file lỗi ({total} assert): {', '.join(failed)}")
    else:
        print(f"[OK] {len(files)}/{len(files)} file pass")
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
