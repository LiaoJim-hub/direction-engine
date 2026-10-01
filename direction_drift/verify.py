# -*- coding: utf-8 -*-
"""复算内核：判定产物信封的组装与三层比对（零依赖，纯标准库）。

「可验证」不是说"我们保证对"，而是**把判定过程打包成一份第三方能自己重算的
产物**。本模块是这条承诺的机械载体：产物的组装（`build_artifact`）与比对
（`compare_artifacts`）都在这里，`de verify` 与服务端 `POST /v1/verify`
**共用同一份**——两处实现即第二处分叉，分叉之后"一致"这个词就没有意义了。

与引擎其他部分的关系：
- 判定由 `core/` 负责，本模块**不判定**，只把判定的输入与结果如实装进信封；
- 卡指纹由 `ScenarioCard.fingerprint()` 产出，本模块**不算指纹**，只比对。

---

## 卡指纹判定为什么是四值（不是布尔，也不是三值）

`fingerprint_line()` 返回四值闭集：

| level | 含义 | 读到它该做什么 |
|---|---|---|
| `ok` | 同算法、同摘要 → 同源 | 可以说"阈值是对着这张卡标出的" |
| `mismatch` | 同算法、摘要不同 → 卡变了 | 阈值可能已失效，**分数不可直接用于复核** |
| `missing` | 标定侧没记 → 不知道 | **无从判断**，不猜测 |
| `algo_mismatch` | 算法版本不同 → 不可比 | **不是卡被改过**，不要重跑标定 |

把 `algo_mismatch` 折进 `mismatch` 是最坏的一种折法：规范化规则一变，
**全部历史指纹会一起变**，于是"我们换了算法"会被读成"这些卡都被改过"，
进而在正确的卡上停用正确的阈值。错误归因的代价比沉默更大。

无冒号的旧格式归入 `algo_mismatch`（不是单独第五值）：算法版本不明 ⇒
无法与当前算法比较 ⇒ 不可比。契约 `Check.status` 在 provenance 层只允许
`equal / differ / missing / algo_mismatch / skipped`，没有第五值的容身处；
且这类情形同样需要"不要因此重跑标定"的警示，与 `algo_mismatch` 同处置。
**渲染层可据 `recorded_algo` 是否为空，把文案侧重调整为"格式不明、不猜测"。**

## 输出若进入比对结果，词汇由契约裁定

`fingerprint_line()` 的 level 是**语义面**；一旦写进 `Check.status`（机械面），
必须走 `LEVEL_TO_CHECK_STATUS` 映射——两个闭集的词不一样，直接透传会越界。
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .scenario import FINGERPRINT_VERSION

__all__ = [
    "FINGERPRINT_LEVELS",
    "LEVEL_TO_CHECK_STATUS",
    "fingerprint_line",
    "split_fingerprint",
    "ARTIFACT_SCHEMA_VERSION",
    "VERDICTS",
    "EXIT_CODES",
    "DEFAULT_TOLERANCE",
    "build_artifact",
    "compare_artifacts",
]

# ---------------------------------------------------------------- 产物信封版本

ARTIFACT_SCHEMA_VERSION = "1"

# 判定结论（语义面）与退出码（机械面）**同序**——同序本身可被断言，
# 顺序一旦错位，"脚本按码处理"与"人按词理解"就会对不上。
VERDICTS = ("consistent", "mismatch", "not_comparable",
            "input_error", "env_unavailable")
EXIT_CODES = {"consistent": 0, "mismatch": 1, "not_comparable": 2,
              "input_error": 3, "env_unavailable": 4}

# 容差是**显式参数**，不是内部常量：实测残差从 0.0005 到 0.0111 分布很宽，
# 任何单一阈值都会在某一张卡上误判。把残差亮出来，决定权在人。
DEFAULT_TOLERANCE = 1e-3

# 卡指纹判定的四值闭集（顺序即严重度递增的阅读顺序，非严重度排序）
FINGERPRINT_LEVELS = ("ok", "mismatch", "missing", "algo_mismatch")

# 语义面 → 机械面。契约 openapi-verify.yaml 的 Check.status 在 provenance 层
# 只允许 equal / differ / missing / algo_mismatch / skipped；本表是唯一映射处，
# 不得在别处另写一份（映射分叉 = 两份实现对同一输入给出不同判定）。
LEVEL_TO_CHECK_STATUS = {
    "ok": "equal",
    "mismatch": "differ",
    "missing": "missing",
    "algo_mismatch": "algo_mismatch",
}


def split_fingerprint(fp: Optional[str]) -> Tuple[str, str]:
    """把 `c2:55be644d4c446514` 拆成 `("c2", "55be644d4c446514")`。

    无冒号者视为**旧格式**（算法版本为空串）。分开取版本与摘要，是为了让
    "算法变了"与"卡变了"不再同形——两者在只比较整个字符串时都表现为
    `recorded != live`，而该做的事完全相反。
    """
    if not fp:
        return "", ""
    text = str(fp)
    if ":" in text:
        algo, _, digest = text.partition(":")
        return algo, digest
    return "", text


def fingerprint_line(recorded: Optional[str], live: Optional[str],
                     recorded_status: str = "") -> Dict[str, Any]:
    """判定「标定侧记下的卡指纹」与「当前这张卡的指纹」的关系。

    返回结构化结果（**不含任何 HTML**）——渲染由调用方负责。本函数是纯逻辑，
    因此 `de verify`（机械面）与卡片页/报告（人读面）可以共用同一份判定，
    不会各自演化出第二套。

    Args:
        recorded: 标定产物里记下的指纹（可能为 None / 空串）。
        live: 当前卡实时算出的指纹。
        recorded_status: 标定文件自申报的取数时机
            （`recorded_at_calibration` / `backfilled` / ...）。

    Returns:
        dict，至少含：
        - `level`: 四值闭集之一（见模块 docstring）
        - `recorded` / `live`: 原值
        - `recorded_algo` / `live_algo`: 算法版本（无冒号者为空串）
        - `recorded_digest` / `live_digest`: 摘要
        - `backfilled`: 是否事后补记（补记的"一致"是弱证据，见下）

    关于 `backfilled`：补记的一致**只说明"自补记以来本卡未变"**，不说明
    标定当时用的就是这张卡。这个区别必须由调用方说出来，否则补记出来的
    字段会被当成原始记录用。
    """
    live_text = "" if live is None else str(live)
    live_algo, live_digest = split_fingerprint(live_text)

    if not recorded:
        return {
            "level": "missing",
            "recorded": recorded,
            "live": live_text,
            "recorded_algo": None,
            "live_algo": live_algo,
            "recorded_digest": None,
            "live_digest": live_digest,
            "backfilled": recorded_status == "backfilled",
        }

    recorded_text = str(recorded)
    rec_algo, rec_digest = split_fingerprint(recorded_text)

    # 算法版本不同（含"记录侧无版本前缀"这一旧格式情形）→ 不可比。
    # 注意：这不是卡的问题，归因错了会让人重跑本来正确的标定。
    if rec_algo != live_algo:
        return {
            "level": "algo_mismatch",
            "recorded": recorded_text,
            "live": live_text,
            "recorded_algo": rec_algo,
            "live_algo": live_algo,
            "recorded_digest": rec_digest,
            "live_digest": live_digest,
            "backfilled": recorded_status == "backfilled",
        }

    if rec_digest != live_digest:
        return {
            "level": "mismatch",
            "recorded": recorded_text,
            "live": live_text,
            "recorded_algo": rec_algo,
            "live_algo": live_algo,
            "recorded_digest": rec_digest,
            "live_digest": live_digest,
            "backfilled": recorded_status == "backfilled",
        }

    return {
        "level": "ok",
        "recorded": recorded_text,
        "live": live_text,
        "recorded_algo": rec_algo,
        "live_algo": live_algo,
        "recorded_digest": rec_digest,
        "live_digest": live_digest,
        "backfilled": recorded_status == "backfilled",
    }


# ------------------------------------------------------------------ 复算内核


def _get(obj: Any, path: Sequence[str], default=None):
    """按 key_path 逐层取值；任一层缺失即返回 default（不抛、不猜）。"""
    cur = obj
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _check(key: str, key_path: Sequence[str], layer: str, status: str,
           a=None, b=None, note=None, delta=None,
           tolerance=None) -> Dict[str, Any]:
    """按契约 Check 组装一条比对项（additionalProperties: false，故不多加键）。"""
    c = {"key": key, "key_path": list(key_path), "layer": layer,
         "status": status, "a": a, "b": b, "note": note}
    if delta is not None:
        c["delta"] = delta
    if tolerance is not None:
        c["tolerance"] = tolerance
    return c


def _cmp_scalar(checks, a, b, key, path, layer, differ_note=None,
                missing_note=None) -> bool:
    """比对一个标量：缺失→missing；不等→differ；相等→equal。返回是否相等。"""
    va, vb = _get(a, path), _get(b, path)
    if va is None or vb is None:
        checks.append(_check(key, path, layer, "missing", va, vb, missing_note))
        return False
    if va != vb:
        checks.append(_check(key, path, layer, "differ", va, vb, differ_note))
        return False
    checks.append(_check(key, path, layer, "equal", va, vb))
    return True


def _cmp_mapping(checks, a, b, key, path, layer, skip=()) -> bool:
    """逐键比对一个字典（如 calibration.weights / detector.config）。"""
    va, vb = _get(a, path), _get(b, path)
    if va is None or vb is None:
        checks.append(_check(key, path, layer, "missing", va, vb,
                             "一侧缺失：条件无从对齐，属不可比"))
        return False
    if not isinstance(va, dict) or not isinstance(vb, dict):
        checks.append(_check(key, path, layer, "differ", va, vb, "类型不是对象"))
        return False
    ok = True
    for k in sorted(set(va) | set(vb)):
        if k in skip:
            continue
        sub = list(path) + [k]
        if k not in va or k not in vb:
            checks.append(_check(f"{key}.{k}", sub, layer, "missing",
                                 va.get(k), vb.get(k)))
            ok = False
        elif va[k] != vb[k]:
            checks.append(_check(f"{key}.{k}", sub, layer, "differ", va[k], vb[k]))
            ok = False
    if ok:
        checks.append(_check(key, path, layer, "equal", va, vb))
    return ok


def _digest_of(fp: Optional[str]) -> Optional[str]:
    """从 `c2:xxxx` 取摘要部分（产物未单列 fingerprint_digest 时的回落）。"""
    _, digest = split_fingerprint(fp)
    return digest or None


def compare_artifacts(a: Dict[str, Any], b: Dict[str, Any], *,
                      tolerance: float = DEFAULT_TOLERANCE) -> Dict[str, Any]:
    """三层比对两份判定产物。**先判可比性，再判数值**——顺序不能反。

    先比值会把「不可比」误报成「不一致」：换了引擎版本与判错了方向，在只看
    数字时完全同形，但该做的事完全相反。`0` 与 `2` 的区别是核心——
    `2` **不是失败**，是"条件不同、结论不可对话"，脚本必须能区分它，
    否则 CI 会把"换了引擎版本"误报成"回归"。

    Returns:
        契约 `VerifyResponse` 的同构字典：verdict / exit_code_equivalent /
        checks / residuals / notes。
    """
    checks: List[Dict[str, Any]] = []
    notes: List[str] = []

    # ---- 第一层：可比性（comparability）--------------------------------
    not_comparable: List[str] = []

    if not _cmp_scalar(checks, a, b, "artifact_schema_version",
                       ["artifact_schema_version"], "comparability",
                       missing_note="产物格式版本缺失：语义不同的两份不能比"):
        not_comparable.append("artifact_schema_version")

    if not _cmp_scalar(checks, a, b, "engine.version", ["engine", "version"],
                       "comparability",
                       differ_note="判定器版本不同：条件不同，结论不可对话"):
        not_comparable.append("engine.version")

    if not _cmp_scalar(checks, a, b, "card.fingerprint_algo",
                       ["card", "fingerprint_algo"], "comparability",
                       differ_note="指纹算法版本不同，属不可比；"
                                   "这不表示卡被改过——不要因此重跑标定"):
        not_comparable.append("card.fingerprint_algo")

    if not _cmp_scalar(checks, a, b, "encoder.name", ["encoder", "name"],
                       "comparability",
                       missing_note="编码器不可得：跨编码器分数水位不可比"):
        not_comparable.append("encoder.name")

    if not _cmp_mapping(checks, a, b, "calibration.weights",
                        ["calibration", "weights"], "comparability"):
        not_comparable.append("calibration.weights")

    mode_a = _get(a, ["calibration", "mode"])
    mode_b = _get(b, ["calibration", "mode"])
    if mode_a == "synthetic" or mode_b == "synthetic":
        checks.append(_check("calibration.mode", ["calibration", "mode"],
                             "comparability", "differ", mode_a, mode_b,
                             "合成标定不能用来比判定：一侧为 synthetic"))
        not_comparable.append("calibration.mode")
    else:
        _cmp_scalar(checks, a, b, "calibration.mode", ["calibration", "mode"],
                    "comparability")

    if not _cmp_mapping(checks, a, b, "detector.config", ["detector", "config"],
                        "comparability"):
        not_comparable.append("detector.config")

    if not_comparable:
        return {
            "verdict": "not_comparable",
            "exit_code_equivalent": EXIT_CODES["not_comparable"],
            "checks": checks,
            "residuals": {"max_abs_delta": None, "per_item": [],
                          "tolerance": tolerance},
            "notes": notes + [
                "不可比**不是**不一致：条件不同则结论不可对话，"
                "不要把这次结果读成回归。",
            ],
        }

    # ---- 第二层：同源性（provenance）-----------------------------------
    provenance_bad: List[str] = []

    digest_a = _get(a, ["card", "fingerprint_digest"]) or _digest_of(
        _get(a, ["card", "fingerprint"]))
    digest_b = _get(b, ["card", "fingerprint_digest"]) or _digest_of(
        _get(b, ["card", "fingerprint"]))
    if digest_a != digest_b:
        checks.append(_check("card.fingerprint_digest",
                             ["card", "fingerprint_digest"], "provenance",
                             "differ", digest_a, digest_b,
                             "同一算法下摘要不同 → 卡变了，阈值可能已失效"))
        provenance_bad.append("card.fingerprint_digest")
    else:
        checks.append(_check("card.fingerprint_digest",
                             ["card", "fingerprint_digest"], "provenance",
                             "equal", digest_a, digest_b))

    # 标定侧记的指纹，与卡本身的指纹是否同源（四值判定 → 契约词汇）
    line = fingerprint_line(_get(a, ["calibration", "card_fingerprint"]),
                            _get(a, ["card", "fingerprint"]))
    status = LEVEL_TO_CHECK_STATUS[line["level"]]
    if status in ("differ", "algo_mismatch"):
        provenance_bad.append("calibration.card_fingerprint")
    checks.append(_check("calibration.card_fingerprint",
                         ["calibration", "card_fingerprint"], "provenance", status,
                         _get(a, ["calibration", "card_fingerprint"]),
                         _get(a, ["card", "fingerprint"]),
                         None if status == "equal" else
                         "阈值不属于这张卡：标定记录的卡与当前卡不同源"))
    if line["backfilled"]:
        notes.append("calibration.card_fingerprint 为**事后补记**：一致只说明"
                     "自补记以来卡未变，不说明标定当时用的就是这张卡。")

    # 诚实边界（§8.5）：权重哈希不可得 ⇒ 只能证卡同源，不能证分数可复现
    for side, art in (("A", a), ("B", b)):
        if _get(art, ["encoder", "weights_hash"]) is None:
            notes.append(f"产物 {side} 的 encoder.weights_hash 为 null：环境指纹"
                         "不可得，**分数可复现未证**，本次只能证卡同源——"
                         "不得表述为「复算成功」。")
            break

    fp_status = _get(a, ["card", "fingerprint_status"])
    if fp_status in ("backfilled", "missing"):
        notes.append(f"card.fingerprint_status={fp_status}：同源性可质疑，"
                     "已如实记录但不据此判成功。")

    # ---- 第三层：数值（numeric）----------------------------------------
    numeric_bad: List[str] = []
    residuals: List[Dict[str, Any]] = []

    if not _cmp_scalar(checks, a, b, "judgment.texts_digest",
                       ["judgment", "texts_digest"], "numeric",
                       differ_note="输入摘要不同：条目无从对齐，不猜"):
        numeric_bad.append("judgment.texts_digest")

    items_a = _get(a, ["judgment", "results"]) or _get(a, ["items"]) or []
    items_b = _get(b, ["judgment", "results"]) or _get(b, ["items"]) or []

    max_delta: Optional[float] = None
    if items_a and items_b and len(items_a) == len(items_b):
        for i, (ia, ib) in enumerate(zip(items_a, items_b)):
            sa, sb = ia.get("score"), ib.get("score")
            if isinstance(sa, (int, float)) and isinstance(sb, (int, float)):
                delta = abs(float(sa) - float(sb))
                residuals.append({"index": i, "delta": delta})
                max_delta = delta if max_delta is None else max(max_delta, delta)
                st = ("within_tolerance" if delta <= tolerance
                      else "beyond_tolerance")
                checks.append(_check(f"items[{i}].score",
                                     ["items", str(i), "score"], "numeric", st,
                                     sa, sb, None, delta, tolerance))
                if st == "beyond_tolerance":
                    numeric_bad.append(f"items[{i}].score")
            for field in ("drift_level", "suspect"):
                if ia.get(field) != ib.get(field):
                    checks.append(_check(f"items[{i}].{field}",
                                         ["items", str(i), field], "numeric",
                                         "differ", ia.get(field), ib.get(field)))
                    numeric_bad.append(f"items[{i}].{field}")
    elif items_a or items_b:
        checks.append(_check("judgment.results", ["judgment", "results"],
                             "numeric", "missing",
                             len(items_a) or None, len(items_b) or None,
                             "条目数不等或一侧空缺：无法逐条对齐，不猜"))
        numeric_bad.append("judgment.results")

    verdict = "mismatch" if (provenance_bad or numeric_bad) else "consistent"
    return {
        "verdict": verdict,
        "exit_code_equivalent": EXIT_CODES[verdict],
        "checks": checks,
        "residuals": {"max_abs_delta": max_delta, "per_item": residuals,
                      "tolerance": tolerance},
        "notes": notes,
    }


def build_artifact(*, engine_version: str, card, encoder_info: Dict[str, Any],
                   calibration: Optional[Dict[str, Any]] = None,
                   detector_config: Optional[Dict[str, Any]] = None,
                   judgment: Optional[Dict[str, Any]] = None,
                   items: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """组装一份判定产物信封（最小可复算溯源集）。

    **不判定**：判定由 `core/` 负责，本函数只把判定的**条件与结果**如实装进去。
    装进去的东西越少，"可复算"越可信——所以只收那些能被第三方独立检验的项。
    """
    fp = getattr(card, "fingerprint", None)
    fp_text = fp() if callable(fp) else fp
    algo, digest = split_fingerprint(fp_text)
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "engine": {"name": "direction-drift", "version": engine_version},
        "card": {
            "card_id": getattr(card, "card_id", None),
            "card_source": getattr(card, "card_source", None),
            "fingerprint": fp_text,
            "fingerprint_algo": algo or None,
            "fingerprint_digest": digest or None,
        },
        "encoder": dict(encoder_info or {}),
        "calibration": dict(calibration or {}),
        "detector": {"config": dict(detector_config or {})},
        "judgment": dict(judgment or {}),
        "items": [dict(x) for x in (items or [])],
    }
