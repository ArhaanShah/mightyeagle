from pkg.proto import dispatch

class BetaHandler:
    def process(self, msg: str) -> str:
        return f"B:{msg}"

def run_b() -> str:
    h = BetaHandler()
    return dispatch(h, "3")
