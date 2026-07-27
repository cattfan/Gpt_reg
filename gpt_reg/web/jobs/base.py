from __future__ import annotations

from typing import Any, Protocol


class JobManager(Protocol):
    kind: str

    def start_batch(self, *args: Any, **kwargs: Any) -> list[str]: ...

    def subscribe(self, fn: Any) -> None: ...

    def snapshot_for_sse(self, jobs: list[dict[str, Any]]) -> dict[str, Any]: ...
