DRIVER_DEAD_MARKERS = (
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "Connection closed",
)

LAUNCH_RETRY_MAX = 2
LAUNCH_RETRY_BACKOFF = 2.0


def is_driver_dead_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(m in msg for m in DRIVER_DEAD_MARKERS)
