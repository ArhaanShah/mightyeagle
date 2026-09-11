from typing import TypedDict

# ``workers`` represents the number of worker processes and is naturally an
# integer. The original annotation used ``str`` which conflicted with the
# integer literal used in ``run_web`` and caused type‑checking errors. The
# field type is corrected to ``int``.
class WebConfig(TypedDict):
    workers: int

def get_workers(cfg: WebConfig) -> int:
    return cfg["workers"]

def run_web() -> int:
    cfg: WebConfig = {"workers": 4}
    return get_workers(cfg)
