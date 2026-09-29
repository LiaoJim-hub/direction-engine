"""样本构建：LLM合成本解决冷启动。数量带缓冲、围栏剥离、重试、去重。"""

import json
from typing import Callable, List, Optional

import numpy as np


class SampleBuilder:
    # 数量带缓冲：LLM 偶发少给仍能过 from_samples 下限（v2.1 P0-2）
    CORE_N, BOUNDARY_N, NEGATIVE_N = 25, 12, 12

    CORE_PROMPT = """你是一个AI助手，当前方向是：{goal}
约束：{constraints}
请生成{n}个符合这个方向的输出示例。每个示例1-2句话，直接输出在方向上、不偏离、彼此话题不同。
只输出JSON数组，格式：["示例1", "示例2", ...]"""

    BOUNDARY_PROMPT = """你是一个AI助手，当前方向是：{goal}
约束：{constraints}
请生成{n}个在方向上但有轻微偏移的输出示例。每个示例1-2句话，仍属同一方向但边缘化。
只输出JSON数组，格式：["示例1", "示例2", ...]"""

    NEGATIVE_PROMPT = """你是一个AI助手，当前方向是：{goal}
约束：{constraints}
请生成{n}个明显偏离这个方向的输出示例。每个示例1-2句话，违反方向或约束。
只输出JSON数组，格式：["示例1", "示例2", ...]"""

    def __init__(self, llm_call_fn: Callable[[str], str],
                 embed_fn: Optional[Callable] = None):
        self.llm_call_fn = llm_call_fn
        self._embed_fn = embed_fn       # 注入则启用近重复剔除，否则仅精确去重

    def build(self, goal: str, constraints: Optional[List[str]] = None) -> dict:
        c = "、".join(constraints) if constraints else "无"
        return {
            "core": self._generate(self.CORE_PROMPT.format(goal=goal, constraints=c, n=self.CORE_N), self.CORE_N),
            "boundary": self._generate(self.BOUNDARY_PROMPT.format(goal=goal, constraints=c, n=self.BOUNDARY_N), self.BOUNDARY_N),
            "negative": self._generate(self.NEGATIVE_PROMPT.format(goal=goal, constraints=c, n=self.NEGATIVE_N), self.NEGATIVE_N),
        }

    def _generate(self, prompt: str, expected: int) -> List[str]:
        last_err = None
        for attempt in range(3):
            try:
                samples = self._parse_json_array(self.llm_call_fn(prompt))
                samples = self._dedupe(samples)
                if len(samples) >= expected:
                    return samples[:expected]
                last_err = ValueError(f"样本数不足：{len(samples)}/{expected}")
                prompt += "\n（上次数量不足或有重复，请补足且不要重复已有示例）"
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e
        raise ValueError(f"LLM样本生成失败（重试3次）：{last_err}")

    @staticmethod
    def _parse_json_array(response: str) -> List[str]:
        text = response.strip()
        if text.startswith("```"):                       # 剥离 ```json 围栏
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        samples = json.loads(text)
        if not isinstance(samples, list):
            raise ValueError("LLM返回非数组")
        out = [str(s).strip() for s in samples if str(s).strip()]
        return list(dict.fromkeys(out))                  # 精确去重保序

    def _dedupe(self, samples: List[str], threshold: float = 0.95) -> List[str]:
        """近重复剔除（余弦>0.95 视为重复，防锥口被人为收窄）。"""
        if self._embed_fn is None or len(samples) < 2:
            return samples
        embs = np.array([self._embed_fn(s) for s in samples])
        embs = embs / np.linalg.norm(embs, axis=1, keepdims=True)
        keep: List[int] = []
        for i, e in enumerate(embs):
            if all(float(np.dot(e, embs[j])) < threshold for j in keep):
                keep.append(i)
        return [samples[i] for i in keep]
