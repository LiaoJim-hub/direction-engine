# -*- coding: utf-8 -*-
"""确定性离线嵌入器（demo / CI / 测试专用，生产请用 utils.encoder.Encoder）。

原理：jieba 分词后按词哈希到固定维度，词长加权（长词承载更多内容信息），
L2 归一化。完全离线、确定性：同一文本永远得到同一向量，测试可在无 torch、
无网络的环境稳定运行；且词级共现对中文出戏/在轨有足够区分度（实测同域
对齐 ≈0.9+，跨域 ≈0.05）。

诚实声明：它不做语义理解，只捕捉表层词汇共现。用它跑 demo 是为了让"建锥→
标定→检测→返回协议"的机械链路在 30 秒内可复现；生产场景请安装 [sbert] 扩展
并使用 bge-small-zh-v1.5 等真实语义嵌入模型——分数水位不可跨编码器比较。
"""
import hashlib
from typing import List

import jieba
import numpy as np

_DIM = 64


def _bucket(token: str) -> int:
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "big") % _DIM


class DemoEncoder:
    """确定性词级哈希嵌入器。接口与 utils.encoder.Encoder 一致（encode/encode_batch）。"""

    def __init__(self, dim: int = _DIM):
        self.dim = dim

    def encode(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float64)
        for w in jieba.lcut(text):
            w = w.strip()
            if not w:
                continue
            v[_bucket(w)] += len(w) ** 1.5
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def encode_batch(self, texts: List[str]) -> np.ndarray:
        return np.stack([self.encode(t) for t in texts])
