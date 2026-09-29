"""JSON 安全转换：把 numpy 标量等转成 `json.dumps` 能吃的原生类型（v2.2.4）。

托管层要把会话快照写进 Redis（本质是 JSON 字符串），而引擎内部的对齐分、标定
阈值可能是 numpy 标量——`json.dumps(numpy.float64(0.5))` 直接抛 TypeError。
若让每个调用方各自 `float()`，迟早漏一处，且漏处在运行时才炸。这里统一收口。

纪律：只转换类型，不改数值；**不宽吞异常**（v2.3.3 收窄为 ValueError/TypeError
——多元素数组的 `.item()` 抛的就是这两类，其余异常说明是别的问题，不该被吞）。
"""

from typing import Any


def jsonable(value: Any) -> Any:
    """递归把 value 里的 numpy/扩展标量转成原生类型；其余原样返回。"""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    item = getattr(value, "item", None)          # numpy 标量 / np.bool_
    if callable(item):
        try:
            return jsonable(item())
        except (ValueError, TypeError):          # 多元素数组等无法 item() 的：原样返回
            return value
    return value
