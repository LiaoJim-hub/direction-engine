# -*- coding: utf-8 -*-
"""标定产物的组装与自校验（2026-10-01 新增）。

**为什么要有这个文件**

在此之前，**没有任何脚本会写 `*_calibration.json`**（`grep json.dump` 只命中
运行态统计与巡检状态，`CALIB_FILE` 只有读、没有写）。两份正式标定产物的元信息层
——`mode` / `encoder` / `weights` / `prompt_version` / `card_fingerprint` /
`recall_at_low`——**全部由人手写**。手写的后果不是"偶尔写错字"，而是：

  · `calibrate_thresholds` 里 `encoder_name` / `weights` / `prompt_version`
    三个参数**早就存在**（`roc.py:13-15`），两处调用点至今**一个都没传**；
  · `card_fingerprint` 只能以 `backfilled` 形态补记，永远说不清
    "标定当时用的是哪张卡"。

所以正确的修法**不是**再给标定函数加第四个参数（加了一定还是没人传——三个现成
参数就是证据），而是**先有一个产出脚本**，让这张 JSON 由代码生成，参数才有意义。
`card_fingerprint` 只是这张清单上新增的一项。

本模块提供**组装**与**自校验**；取数与落盘在
`AI方向漂移检测/build_calibration.py`（那里才有语料、标注与页面常量）。

**本模块能自动做到、而手写做不到的那件事**：把 `card_fingerprint_status` 写成
`recorded_at_calibration`——即"这个指纹是标定当场记下的"，而不是事后补记。
"""
from datetime import date
from typing import Dict, List, Optional

# 认识论三值。**不是自由文本**：写错会让消费端（calib_meta）无法三值判定。
MODES = ("formal", "synthetic")

# 卡指纹状态的**闭集**（2026-10-01 补）。理由与 MODES 相同，但更硬：
# 已经有三个消费端按**字符串字面值**分支——`calib_meta.py`、
# `card_page.py`、`inspect_cycle.py`。写错一个字母（如 `recorded_at_calibraton`）
# 不会报错，只会三个消费端**同时**掉进 else 分支，把「标定当场记录」当成
# 「没有来源信息」。这正是静默降级的典型形态：唯一能发现它的地方是产出口。
#   recorded_at_calibration —— 标定当场记下的（只有产出脚本能给）
#   backfilled              —— 事后补记的（能证明"自补记以来卡未变"，
#                              不能证明"标定当时用的就是这张卡"）
#   missing                 —— 记录里根本没有指纹
CARD_FINGERPRINT_STATUSES = ("recorded_at_calibration", "backfilled", "missing")

# 正式标定必须自描述的字段。缺任何一项，别人就无法套用你的操作点——
# 这正对应 CONTRIBUTING 的"标定报告必须记录三样东西"（编码器 / 权重 / 提示词版本）。
REQUIRED_FORMAL = (
    "mode", "low", "high", "auc", "n", "n_positive", "source",
    "encoder", "weights", "prompt_version", "card_fingerprint",
    "card_fingerprint_status",
)


def validate_calibration_record(record: Dict) -> None:
    """校验标定产物能否被人复查。不通过则抛 ValueError（不静默降级）。

    判据用 `k not in record or record[k] is None`，**不用真值判断**——
    `low=0.0` 或 `n=0` 都是合法值，用 `not record[k]` 会把它们误报成缺失。
    """
    if not isinstance(record, dict):
        raise ValueError(f"标定产物应为 dict，收到 {type(record).__name__}")
    mode = record.get("mode")
    if mode not in MODES:
        raise ValueError(
            f"mode 必须是 {MODES} 之一，收到 {mode!r}——认识论状态不是自由文本，"
            f"写错会让消费端无法三值判定")
    if mode != "formal":
        return                      # synthetic 是兜底路径，不要求自描述

    missing = [k for k in REQUIRED_FORMAL
               if k not in record or record[k] is None]
    if missing:
        raise ValueError(
            f"正式标定缺以下自描述字段 {missing}——缺了它们，别人无法套用你的操作点，"
            f"这份标定也就不构成「可被别人复查的产物」")

    status = record.get("card_fingerprint_status")
    if status not in CARD_FINGERPRINT_STATUSES:
        raise ValueError(
            f"card_fingerprint_status 必须是 {CARD_FINGERPRINT_STATUSES} 之一，"
            f"收到 {status!r}——这个字段区分「标定当场记录」与「事后补记」，"
            f"三个消费端按字面值分支，写错只会静默掉进 else，不会报错")


def build_calibration_record(*, calibration: Dict, mode: str,
                             source: str, n: int, n_positive: int,
                             low: Optional[float] = None,
                             high: Optional[float] = None,
                             encoder: Optional[str] = None,
                             weights: Optional[Dict] = None,
                             prompt_version: Optional[str] = None,
                             card_fingerprint: Optional[str] = None,
                             created: Optional[str] = None,
                             **reporting) -> Dict:
    """组装一份标定产物。

    `calibration`：`roc.calibrate_thresholds` 的返回值（提供 auc / 建议阈值等）。
    `low` / `high`：**人工选定的操作点**。缺省沿用 `calibration` 的建议值，
    但请注意——操作点选择是业务权衡（漏报代价 >> 误报代价 → 偏查全），
    正式标定通常要按 `scan_operating_points` 的表人工拍板，不宜全自动。
    `**reporting`：报告性 / 审计性字段（`label_provenance`、`operating_point`、
    `recall_at_low`、`audit`、`card_fingerprint_note`、`operating_point_scan` …）。
    这些字段逐份不同，故不固定签名；但**不得覆盖下面已确定的键**（会静默改口径）。

    返回的 dict 已通过 `validate_calibration_record`。
    """
    if not isinstance(calibration, dict):
        raise ValueError("calibration 应为 calibrate_thresholds 的返回值（dict）")

    record: Dict = {
        "created": created or date.today().isoformat(),
        "mode": mode,
        "source": source,
        "n": int(n),
        "n_positive": int(n_positive),
        "low": float(low if low is not None else calibration["suggested_low"]),
        "high": float(high if high is not None else calibration["suggested_high"]),
        "auc": float(calibration["auc"]),
        "target_precision": calibration.get("target_precision"),
        "encoder": encoder,
        "weights": weights,
        "prompt_version": prompt_version,
        "card_fingerprint": card_fingerprint,
        # ★ 本模块存在的直接产物：由脚本写下的指纹，其状态是**标定当场记录**，
        #   而不是事后补记。这正是加产出脚本相对于"给函数加参数"的差别。
        "card_fingerprint_status": ("recorded_at_calibration"
                                    if card_fingerprint else "missing"),
    }
    # `inputs` 是 roc.py 的回显层（与阈值同源的三样）。有就带进来，
    # 它让"这份标定是在什么环境下算出来的"多一条交叉印证。
    if calibration.get("inputs"):
        record["calibration_inputs"] = calibration["inputs"]

    clobber = sorted(set(reporting) & set(record))
    if clobber:
        raise ValueError(
            f"reporting 字段 {clobber} 与已确定的键重名——"
            f"这会让口径在无声中被改掉；请改走显式参数或换个字段名")
    record.update(reporting)

    validate_calibration_record(record)
    return record


def environment_fingerprint(*, encoder: str, weights: Optional[Dict] = None,
                            library_versions: Optional[Dict[str, str]] = None,
                            weights_hashes: Optional[Dict[str, str]] = None) -> Dict:
    """环境指纹（**本版只提供结构，不接入标定流程**）。

    与 `card_fingerprint` **正交**：卡指纹答"卡变没变"，环境指纹答"环境变没变"。
    两者合起来，"分数为什么不可复算"才算有人管——单独任一都答不了。

    实例（2026-10-01 实测）：同一方法同一机器下，寻路蝶复算残差 0.0005、
    蝶鉴 0.0111，**差 20 倍而当时没有任何字段能解释为什么**。卡指纹后来排除了
    "卡变过"这一半；剩下那一半（编码器实现 / 权重文件 / 库版本）需要它。

    设计留待下一个窗口定稿：字段怎么取（权重哈希成本实测约 1 秒 / 96 MB）、
    存哪、与卡指纹怎么合并展示。
    """
    return {
        "encoder": encoder,
        "weights": weights,
        "library_versions": library_versions or {},
        "weights_hashes": weights_hashes or {},
    }


def required_formal_fields() -> List[str]:
    """把"正式标定该记什么"暴露成可枚举的清单，便于文档与守卫共用一份。"""
    return list(REQUIRED_FORMAL)
