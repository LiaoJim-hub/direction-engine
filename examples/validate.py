"""验证：200 段做方向性验证；任务级阈值适配需每任务 ≥50 段（500–1000 段总量）。"""

import numpy as np
from sklearn.metrics import (cohen_kappa_score, f1_score, precision_recall_curve,
                             precision_score, recall_score, roc_auc_score)


def validate(system_scores, system_labels, human_1, human_2,
             system_id_per_item=None):
    h1, h2 = np.array(human_1), np.array(human_2)
    kappa = cohen_kappa_score(h1, h2)
    print(f"标注者间 Kappa：{kappa:.3f}")
    if kappa < 0.6:
        print("标注一致性不足（<0.6）：先重新定义漂移或培训标注者，验证结论无效")
        return None

    agree = h1 == h2
    if (~agree).any():                      # 分歧仲裁制：不按半数舍入进金标准
        print(f"警告：{int((~agree).sum())} 条标注分歧，已剔除待仲裁：")
        for i in np.where(~agree)[0]:
            print(f"  [{i}] 标注者1={h1[i]} 标注者2={h2[i]}")
    gold = h1[agree]
    sys_scores = np.asarray(system_scores)[agree]
    sys_labels = np.asarray(system_labels)[agree]

    precision = precision_score(gold, sys_labels, pos_label=1, zero_division=0)
    recall = recall_score(gold, sys_labels, pos_label=1, zero_division=0)
    f1 = f1_score(gold, sys_labels, pos_label=1, zero_division=0)
    auc = roc_auc_score(gold, 1.0 - sys_scores)   # 方向修复：对齐分取反再算 AUC
    print(f"漂移类 Precision={precision:.3f} Recall={recall:.3f} "
          f"F1={f1:.3f} ROC-AUC={auc:.3f}")

    if precision > 0.75 and recall > 0.75:
        verdict = "usable"
    elif precision > 0.6 and recall > 0.6:
        verdict = "needs_optimization"
    else:
        verdict = "invalid"
    print(f"结论：{verdict}")
    return {"kappa": float(kappa), "precision": float(precision),
            "recall": float(recall), "f1": float(f1), "auc": float(auc),
            "verdict": verdict}
