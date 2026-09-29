"""嵌入模型：bge-small-zh-v1.5；默认 HF 镜像（国内可用，可被外部 HF_ENDPOINT 覆盖）。"""
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import numpy as np

_models = {}


def get_encoder(model_name: str = "BAAI/bge-small-zh-v1.5"):
    if model_name not in _models:
        from sentence_transformers import SentenceTransformer
        _models[model_name] = SentenceTransformer(model_name)
    return _models[model_name]


class Encoder:
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
        self.model = get_encoder(model_name)

    def encode(self, text: str) -> np.ndarray:
        return self.model.encode(text)

    def encode_batch(self, texts: list) -> np.ndarray:
        return self.model.encode(texts)
