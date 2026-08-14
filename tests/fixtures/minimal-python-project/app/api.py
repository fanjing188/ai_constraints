"""公开结构的直接消费者。"""

from app.schema import RESPONSE_KEYS
from app.service import greeting


def response() -> dict[str, str]:
    return dict(zip(RESPONSE_KEYS, (greeting(),), strict=True))
