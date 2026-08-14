"""公开结构的直接消费者。"""

from app.schema import RESPONSE_KEYS
from app.service import greeting


def response() -> dict[str, str]:
    values = (greeting(),)
    if len(RESPONSE_KEYS) != len(values):
        raise ValueError("响应结构与值数量不一致")
    return dict(zip(RESPONSE_KEYS, values))
