# nli.py —— 【V2 功能】MVP 不启用。需内存 ≥4GB；ENABLE_NLI=1 时才加载。
_model_cache = {}


def get_nli(model_name: str = "IDEA-CCNL/Erlangshen-Roberta-110M-NLI"):
    if model_name not in _model_cache:
        from transformers import pipeline
        _model_cache[model_name] = pipeline("text-classification", model=model_name)
    return _model_cache[model_name]


class NLIModel:
    def __init__(self, model_name: str = "IDEA-CCNL/Erlangshen-Roberta-110M-NLI"):
        self.model = get_nli(model_name)

    def __call__(self, premise: str, hypothesis: str) -> str:
        result = self.model({"text": premise, "text_pair": hypothesis})
        label = str(result[0]["label"]).lower() if isinstance(result, list) else str(result["label"]).lower()
        if "entail" in label and "not" not in label:
            return "entailment"
        if "contradict" in label or "not_entail" in label:
            return "contradiction"
        return "neutral"
