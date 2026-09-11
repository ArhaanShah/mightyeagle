from pkg.proto import dispatch

class AlphaHandler:
    def process(self, msg: str) -> str:
        return f"A:{msg}"

def run_a() -> str:
    h = AlphaHandler()
    d1 = dispatch(h, "1")
    d2 = dispatch(h, "2")
    return d1 + d2
