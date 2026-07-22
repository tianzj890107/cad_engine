"""平台面向用户展示的统一时间。

私有化部署的容器通常运行在 UTC；业务单据、审签和审计则按中国团队的
工作时区展示。把这件事收口在这里，避免不同模块各自调用 ``time.strftime``
而出现相差八小时的记录。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


CHINA_STANDARD_TIME = ZoneInfo("Asia/Shanghai")


def now_cst_str(format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """返回东八区的业务时间字符串。"""
    return datetime.now(CHINA_STANDARD_TIME).strftime(format)
