# AGENTS.md — Gpt_reg

- Trả lời tiếng Việt, ngắn gọn.
- Test/debug → `test/` (`smoke_*`, `check_*`). Docs user yêu cầu → `docs/`.
- Settings runtime: SQLite (`gpt_reg/db/repositories.py`), không JSON config.
- Fail-fast config; không fallback che lỗi auth/mail.
