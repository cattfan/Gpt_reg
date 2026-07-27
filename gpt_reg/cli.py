from __future__ import annotations

import json
import ipaddress
import secrets
import sys
import uuid
from pathlib import Path

import typer
from rich.console import Console

from gpt_reg.config import ensure_runtime_dirs, load_settings
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import SettingsRepository
from gpt_reg.mail.modes import parse_outlook_combo
from gpt_reg.mail.outlook import OutlookMailProvider
from gpt_reg.mail.providers import build_request_from_combo
from gpt_reg.models import SignupRequest
from gpt_reg.proxy.format import materialize_proxy, proxy_url_for_httpx
from gpt_reg.proxy.pool import ProxyPool
from gpt_reg.signup import run_signup

def _force_utf8_console() -> None:
    """Buộc stdout/stderr sang UTF-8 trước khi in bất cứ thứ gì.

    Console Windows mặc định cp1252: mọi log tiếng Việt (`đã`, `→`, `mật khẩu`)
    ném `UnicodeEncodeError` và **giết cả job** giữa chừng — đã gặp thật, job
    chết ngay sau bước phân loại landing. `errors="replace"` để nếu terminal vẫn
    không dựng được ký tự nào thì in dấu thay thế chứ không nổ.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_console()

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _settings_repo() -> SettingsRepository:
    settings = load_settings()
    ensure_runtime_dirs(settings)
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    return SettingsRepository(conn)


@app.command("migrate")
def cmd_migrate() -> None:
    _settings_repo()
    console.print("[green]migrate OK[/green]")


@app.command("smoke")
def cmd_smoke(
    proxy_line: str = typer.Option("", help="Optional proxy line"),
) -> None:
    settings = load_settings()
    ensure_runtime_dirs(settings)
    pool = ProxyPool.from_multiline(
        proxy_line or _settings_repo().get("proxy.pool") or "",
    )
    mat = pool.acquire()
    if not mat:
        console.print("[yellow]no proxy configured — skip IP check[/yellow]")
        raise typer.Exit(0)
    import httpx

    url = proxy_url_for_httpx(mat)
    with httpx.Client(proxy=url, timeout=30.0) as client:
        r = client.get("https://api.ipify.org?format=json")
        r.raise_for_status()
        console.print(f"[green]proxy OK[/green] {r.json()}")


@app.command("mail-test")
def cmd_mail_test(
    combo: str = typer.Option(..., help="email|pass|refresh|client_id"),
) -> None:
    settings = load_settings()
    ensure_runtime_dirs(settings)
    parsed = parse_outlook_combo(combo)
    pool = ProxyPool.from_multiline(_settings_repo().get("proxy.pool") or "")
    proxy_url = pool.acquire_url()
    provider = OutlookMailProvider(
        combo=parsed,
        state_dir=settings.outlook_state_dir,
        proxy_url=proxy_url,
    )
    subjects = provider.list_subjects(limit=5)
    for s in subjects:
        console.print(f" - {s}")
    console.print("[green]Graph mail OK[/green]")


def _read_combo_file(path: Path) -> str:
    """Đọc combo đầu tiên trong file — tránh dán combo dài vào PowerShell history.

    Dùng `utf-8-sig`: Notepad và `Set-Content -Encoding utf8` của Windows
    PowerShell 5.1 đều ghi BOM, BOM lọt vào email làm form auth từ chối.
    """
    if not path.exists():
        console.print(f"[red]combo file không tồn tại[/red] {path}")
        raise typer.Exit(1)
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            return line.strip()
    console.print(f"[red]combo file rỗng[/red] {path}")
    raise typer.Exit(1)


@app.command("signup")
def cmd_signup(
    combo: str = typer.Option("", help="email|pass|refresh|client_id"),
    combo_file: Path | None = typer.Option(
        None, "--combo-file", help="File chứa combo (dùng dòng đầu tiên)"
    ),
    proxy_line: str = typer.Option("", help="Override proxy line"),
    headless: bool = typer.Option(False, help="Headless browser"),
    reg_mode: str = typer.Option("browser", "--reg-mode", help="browser | http"),
    with_2fa: bool = typer.Option(False, "--with-2fa", help="Enable TOTP after signup"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Session JSON path"),
) -> None:
    from gpt_reg.phases.registry import available_modes

    if combo_file:
        combo = _read_combo_file(combo_file)
    if not combo.strip():
        console.print("[red]cần --combo hoặc --combo-file[/red]")
        raise typer.Exit(1)
    if reg_mode not in available_modes():
        console.print(f"[red]reg_mode phải là một trong {available_modes()}[/red]")
        raise typer.Exit(1)
    combo = combo.strip()
    email, password = build_request_from_combo(combo)
    req = SignupRequest(
        email=email,
        password=password,
        outlook_combo=combo,
        headless=headless,
        proxy=proxy_line or None,
        mail_provider="outlook",
        reg_mode=reg_mode,
    )

    def log(msg: str) -> None:
        console.print(msg)

    result = run_signup(req, log=log, with_2fa=with_2fa, session_file=output)
    if result.ok:
        console.print(f"[green]OK[/green] {result.email}")
        if result.session_path:
            console.print(f"session: {result.session_path}")
        if result.mfa_activated:
            console.print("[green]2FA enabled[/green]")
    else:
        console.print(f"[red]FAIL[/red] {result.error}")
    raise typer.Exit(result.exit_code)


@app.command("enable-2fa")
def cmd_enable_2fa(
    session_file: Path = typer.Option(..., "--session-file", "-f"),
    activate: bool = typer.Option(True, "--activate/--enroll-only"),
    proxy: str | None = typer.Option(None, "--proxy"),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    import asyncio

    from gpt_reg.phases.mfa import MfaError, enable_2fa

    settings = load_settings()
    sf_path = session_file if session_file.is_absolute() else settings.root_dir / session_file
    if not sf_path.exists():
        console.print(f"[red]session not found[/red] {sf_path}")
        raise typer.Exit(1)

    sdata = json.loads(sf_path.read_text(encoding="utf-8"))
    access_token = sdata.get("access_token")
    if not access_token:
        console.print("[red]missing access_token in session file[/red]")
        raise typer.Exit(1)

    fingerprint_profile = str(sdata.get("fingerprint_profile") or "").strip()
    if not fingerprint_profile:
        console.print("[red]missing fingerprint_profile in session file[/red]")
        raise typer.Exit(1)
    cookies = sdata.get("cookies")

    def log(msg: str) -> None:
        console.print(msg)

    try:
        result = asyncio.run(
            enable_2fa(
                access_token=access_token,
                cookies=cookies,
                fingerprint_profile=fingerprint_profile,
                proxy=proxy,
                activate=activate,
                log=log,
            )
        )
    except MfaError as exc:
        console.print(f"[red]MFA fail[/red] {exc}")
        raise typer.Exit(1)

    out_path = output or sf_path.with_suffix(".2fa.json")
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    sdata["mfa_secret"] = result.get("secret")
    sdata["mfa_activated"] = bool(result.get("activated"))
    sf_path.write_text(json.dumps(sdata, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]2FA OK[/green] activated={result.get('activated')} → {out_path}")


def _is_loopback_web_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip()).is_loopback
    except ValueError:
        return False


@app.command("web")
def cmd_web(
    host: str = typer.Option("127.0.0.1"),
    port: int | None = typer.Option(None),
) -> None:
    import uvicorn

    settings = load_settings()
    p = port or settings.web_port
    if not _is_loopback_web_host(host):
        console.print("[red]Web UI chỉ được bind trên localhost.[/red]")
        raise typer.Exit(2)
    uvicorn.run("gpt_reg.web.server:app", host=host, port=p, reload=False)


if __name__ == "__main__":
    app()
