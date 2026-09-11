from pkg.proto import dispatch

class BetaHandler:
    # ``dispatch`` expects a ``handle`` method as defined by the ``Handler``
    # protocol.  The original implementation only provided ``process`` which
    # is used directly in the public tests.  Adding a thin ``handle`` wrapper
    # preserves the external API while satisfying the protocol.
    def process(self, msg: str) -> str:
        return f"B:{msg}"

    def handle(self, msg: str) -> str:
        return self.process(msg)

def run_b() -> str:
    h = BetaHandler()
    return dispatch(h, "3")
