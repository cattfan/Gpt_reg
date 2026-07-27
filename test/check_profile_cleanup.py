"""Kiểm tra profile Camoufox được dọn sau mỗi job.

Mỗi profile ~37 MB và trước đây không bao giờ bị xoá — chạy 200 job browser là
7.4 GB rác trong `runtime/profiles`. Đây là thứ đã phải dọn tay 948 MB.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from gpt_reg.phases.browser import _remove_profile_dir, reap_stale_profiles


def _make_profile(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "prefs.js").write_text("x" * 512, encoding="utf-8")
    (d / "cache").mkdir(exist_ok=True)
    (d / "cache" / "blob").write_text("y" * 2048, encoding="utf-8")
    return d


def main() -> int:
    failures: list[str] = []
    root = Path(tempfile.mkdtemp())

    # Xoá sau job, kể cả khi có file con.
    target = _make_profile(root, "camoufox_abc123")
    _remove_profile_dir(target, lambda _m: None)
    if target.exists():
        failures.append("_remove_profile_dir không xoá được profile")

    # Xoá thư mục không tồn tại không được ném lỗi.
    try:
        _remove_profile_dir(root / "khong_co", lambda _m: None)
    except Exception as exc:
        failures.append(f"xoá thư mục không tồn tại lại ném lỗi: {exc}")

    # KEEP_BROWSER_PROFILES=1 thì giữ lại để debug.
    keep = _make_profile(root, "camoufox_keepme")
    os.environ["KEEP_BROWSER_PROFILES"] = "1"
    try:
        _remove_profile_dir(keep, lambda _m: None)
        if not keep.exists():
            failures.append("KEEP_BROWSER_PROFILES=1 mà vẫn xoá")
    finally:
        os.environ.pop("KEEP_BROWSER_PROFILES", None)

    # Dọn mồ côi: xoá camoufox_*, không đụng thứ khác.
    _make_profile(root, "camoufox_old1")
    _make_profile(root, "camoufox_old2")
    other = root / "khong-phai-profile"
    other.mkdir(exist_ok=True)
    (other / "giu.txt").write_text("giữ lại", encoding="utf-8")

    removed = reap_stale_profiles(root)
    if removed != 3:  # old1, old2, keepme
        failures.append(f"reap_stale_profiles xoá {removed} (muốn 3)")
    if not other.exists():
        failures.append("reap_stale_profiles xoá nhầm thư mục khác")
    if any(p.name.startswith("camoufox_") for p in root.iterdir()):
        failures.append("còn sót profile sau khi dọn")

    # Thư mục không tồn tại → 0, không ném lỗi.
    if reap_stale_profiles(root / "khong_co_dau") != 0:
        failures.append("dọn thư mục không tồn tại phải trả 0")

    # Phase phải xoá trong `finally` để job lỗi cũng được dọn.
    source = Path(__file__).resolve().parent.parent / "gpt_reg" / "phases" / "browser" / "__init__.py"
    text = source.read_text(encoding="utf-8")
    if "finally:" not in text or "_remove_profile_dir(profile_dir" not in text:
        failures.append("BrowserPhase.run không dọn profile trong finally")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] profile cleanup" if failures else "[ok] profile cleanup")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
