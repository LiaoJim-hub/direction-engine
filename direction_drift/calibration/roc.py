"""参数标定。注意：overall_alignment 是"越高越正常"，
ROC/PR 一律在 drift_score = 1 - alignment 空间进行（修复方向性 bug），返回值换算回对齐空间。"""

from typing import Dict, List, Optional

import numpy as np
from sklearn.metrics import precision_recall_curve, roc_auc_score, roc_curve


def calibrate_thresholds(alignment_scores: List[float], labels: List[int],
                         target_precision: float = 0.75,
                         normal_quantile: float = 5.0,
                         encoder_name: Optional[str] = None,
                         weights: Optional[Dict] = None,
                         prompt_version: Optional[str] = None) -> Dict:
    """
    alignment_scores: overall_alignment 连续分数（越高越正常）
    labels: 1=漂移, 0=正常

    `encoder_name` / `weights` / `prompt_version`：**可选但应当给**。
    这三样与阈值同源——换了任何一个，分数分布整体移动、原阈值失效
    （CONTRIBUTING 的"标定报告必须记录三样东西"即指此）。传进来会被原样
    回显在返回值的 `inputs` 里，让标定产物自描述。

    `normal_quantile`：`suggested_high`（警告带上界）的取法。v2.3.3 之前用
    `low + 0.2` 这个凭空的间隔，实测会把**典型的在轨输出**也圈进 warning
    （demo 首行 0.401 < high 0.407 即此）。现改为**正常类的低分位**
    （默认 5 分位）：high 之下只该有 5% 的正常样本落进警告带，
    与 aperture 取核心样本 95 分位是同一套思路。
    """
    drift = 1.0 - np.asarray(alignment_scores, dtype=float)
    labels = np.asarray(labels)
    scores = np.asarray(alignment_scores, dtype=float)
    if len(np.unique(labels)) < 2:
        raise ValueError("标签必须包含两类（正常和漂移）")

    auc = float(roc_auc_score(labels, drift))
    precision, recall, pr_thr = precision_recall_curve(labels, drift)

    valid = np.where(precision[:-1] >= target_precision)[0]
    if len(valid) > 0:
        # v2.2.1 修复：recall 随阈值降低单调不减，达标阈值并列时 argmax 会取到
        # 最大阈值（典型触发：反面样本 alignment 被截断为 0 → drift=1.0 并列），
        # 导致 suggested_low 被压到 0、标定失效。此处取达标集合中最后（最小阈值）项。
        max_recall = recall[valid].max()
        best = valid[np.where(recall[valid] == max_recall)[0][-1]]
        t = float(pr_thr[best])
        ap, ar = float(precision[best]), float(recall[best])
    else:
        t, ap, ar = 0.5, None, None

    # v2.2.1 补充：类间完全可分且分数被截断并列举（反面 alignment=0 → drift=1.0 撞顶）时，
    # PR 阈值会退化到端点（t=1.0 → low=0）。此时直接取两类 drift 的中点，最稳健。
    drift0, drift1 = drift[labels == 0], drift[labels == 1]
    if float(drift0.max()) < float(drift1.min()):
        t = float((drift0.max() + drift1.min()) / 2)
        pred = drift >= t
        n_pred = int(pred.sum())
        ap = float(((pred & (labels == 1)).sum()) / n_pred) if n_pred else None
        ar = float(((pred & (labels == 1)).sum()) / int((labels == 1).sum()))

    low = 1.0 - t                          # 对齐空间漂移边界
    # high = 正常类的低分位（v2.3.3，取代 low + 0.2）
    normal_scores = scores[labels == 0]
    if len(normal_scores) > 0:
        high_data = float(np.percentile(normal_scores, normal_quantile))
        high_source = f"normal_p{normal_quantile:g}({high_data:.3f})"
    else:
        high_data, high_source = low + 0.2, "fallback_low_plus_0.2"
    high = float(min(1.0, max(high_data, low)))     # 警告带永不为负宽度
    return {"auc": auc, "suggested_high": high, "suggested_low": low,
            "high_source": high_source,
            "target_precision": target_precision,
            "achieved_precision": ap, "achieved_recall": ar,
            "n_samples": int(len(labels)),
            # 与阈值同源的三样（缺一项，别人就无法套用你的操作点）
            "inputs": {"encoder": encoder_name,
                       "weights": dict(weights) if weights else None,
                       "prompt_version": prompt_version}}


def scan_operating_points(alignment_scores: List[float], labels: List[int],
                          candidates: List[float] = None) -> List[Dict]:
    """候选操作点扫描表（v2.2.3 新增）。

    PR 自动阈值在正负类分布重叠时会退化（蝶鉴与寻路蝶两次实测均如此），
    操作点选择本质是业务权衡（漏报代价 >> 误报代价 → 偏查全），不能全自动。
    本函数把"人工选点"这一步所需的数据固化为标定的标准输出：
    每个候选 low 的查全、误标、精确率一张表，供人工拍板。

    alignment_scores: overall_alignment（越高越正常）
    labels: 1=漂移（正类）, 0=正常（负类）
    candidates: 候选 low 列表；缺省在正类分数范围内按 0.005 步长扫描。
    返回: [{"low", "recall", "recall_rate", "false_positives", "precision"}, ...]
    注意：只算语义阈值本身的表现；实际检测是组合判据
    （score<low 且 长度保护 且 不在白名单，或规则命中），误标数是上界。
    """
    scores = np.asarray(alignment_scores, dtype=float)
    labels = np.asarray(labels)
    pos_total = int((labels == 1).sum())
    neg_total = int((labels == 0).sum())
    if pos_total == 0 or neg_total == 0:
        raise ValueError("扫描需要两类样本齐全（正常和漂移）")
    if candidates is None:
        pos_scores = scores[labels == 1]
        candidates = np.arange(float(pos_scores.min()),
                               min(float(pos_scores.max()) + 0.01, 1.0), 0.005)
    rows = []
    for c in candidates:
        caught = (labels == 1) & (scores < c)
        fp = (labels == 0) & (scores < c)
        n_hit, n_fp = int(caught.sum()), int(fp.sum())
        rows.append({
            "low": round(float(c), 4),
            "recall": f"{n_hit}/{pos_total}",
            "recall_rate": round(n_hit / pos_total, 3),
            "false_positives": n_fp,
            "precision": round(n_hit / (n_hit + n_fp), 3) if (n_hit + n_fp) else None,
        })
    return rows


def calibrate_aperture(core_angles, boundary_angles=None,
                       negative_angles=None) -> Dict:
    """aperture/softness 的唯一实现（from_samples 亦调用此函数）。"""
    core = np.asarray(core_angles)
    aperture = float(np.percentile(core, 95))
    softness = 0.1
    if boundary_angles is not None and len(boundary_angles) > 0:
        softness = max(0.05, float(np.std(np.asarray(boundary_angles))))
    result = {"aperture": aperture, "softness": softness,
              "core_mean_angle": float(np.mean(core)),
              "core_std_angle": float(np.std(core))}
    if negative_angles is not None and len(negative_angles) > 0:
        neg = np.asarray(negative_angles)
        result["negative_mean_angle"] = float(np.mean(neg))
        result["negative_outside_ratio"] = float(np.mean(neg > aperture + softness))
    return result
