"""Dò bundle công khai của `/auth/login_with` để tìm request SPA tương đương."""

from __future__ import annotations

import re
import json
from urllib.parse import urljoin

from curl_cffi import requests


MARKERS = ("login_with", "/api/auth/", "signin", "oauth", "callbackUrl", "zNt")


def main() -> int:
    session = requests.Session(impersonate="safari18_0")
    session.trust_env = False
    try:
        response = session.get("https://chatgpt.com/auth/login_with", timeout=30)
        html = response.text or ""
        print(f"page HTTP {response.status_code}, url={response.url}, bytes={len(html)}")
        sources = re.findall(r'(?:src|href)=["\']([^"\']+\.js(?:\?[^"\']*)?)', html)
        print(f"scripts={len(sources)}")
        print(f"script_tags={html_lower.count('<script') if 'html_lower' in locals() else html.lower().count('<script')}")
        html_lower = html.lower()
        for marker in MARKERS:
            indexes = [match.start() for match in re.finditer(re.escape(marker.lower()), html_lower)]
            print(f"html marker {marker}: {len(indexes)}")
            for index in indexes[:3]:
                snippet = html[max(0, index - 220):index + 420].replace("\n", " ")
                print(f"  {snippet[:640]}")
        for source in sources:
            url = urljoin(str(response.url), source)
            script = session.get(url, timeout=30)
            body = script.text or ""
            if "auth.login_with-" in url:
                print(f"route bundle:\n{body[:5000]}")
            if "4813494d-" in url:
                source_map = session.get(url + ".map", timeout=30)
                print(f"source map HTTP {source_map.status_code}, bytes={len(source_map.content)}")
                try:
                    mapped = source_map.json()
                except Exception:
                    mapped = {}
                for name, content in zip(
                    mapped.get("sources") or [], mapped.get("sourcesContent") or []
                ):
                    source = content or ""
                    if "skipLoginModal" not in source:
                        continue
                    print(f"source match: {name}")
                    index = source.find("skipLoginModal")
                    print(source[max(0, index - 2500):index + 3500])
                export_match = re.search(r"([A-Za-z0-9_$]+) as zNt", body)
                print(f"zNt export source={export_match.group(1) if export_match else 'missing'}")
                if export_match:
                    internal = export_match.group(1)
                    for pattern in (rf"function {re.escape(internal)}\(", rf"{re.escape(internal)}="):
                        match = re.search(pattern, body)
                        if match:
                            print(body[max(0, match.start() - 1500):match.start() + 5000])
                            break
            found = [marker for marker in MARKERS if marker.lower() in body.lower()]
            if found:
                print(f"match HTTP {script.status_code} {url} markers={found}")
                lower = body.lower()
                for marker in found:
                    index = lower.find(marker.lower())
                    snippet = body[max(0, index - 180):index + 320].replace("\n", " ")
                    print(f"  {marker}: {snippet[:500]}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
