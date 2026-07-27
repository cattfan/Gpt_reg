from gpt_reg.web.jobs.reg_manager import RegJobManager

REGISTRY = {"reg": RegJobManager}
_INSTANCES: dict[str, RegJobManager] = {}


def get_job_manager(kind: str) -> RegJobManager:
    """Một manager cho mỗi kind, dùng lại qua các lần gọi.

    Manager giữ cờ huỷ và trạng thái worker trong bộ nhớ; trả về instance mới sẽ
    khiến `stop_all()` bật cờ trên một object mà không worker nào đang đọc.
    """
    if kind not in _INSTANCES:
        _INSTANCES[kind] = REGISTRY[kind]()
    return _INSTANCES[kind]
