from pkg.proto import dispatch

class AlphaHandler:
    # The protocol defined in ``pkg.proto`` expects a ``handle`` method.
    # Historically this class exposed a ``process`` method which the tests
    # exercise directly. To satisfy the ``Handler`` protocol (and therefore
    # the ``dispatch`` function) we provide a ``handle`` method that forwards
    # to ``process``. This keeps the public API unchanged – callers can still
    # use ``process`` – while allowing ``dispatch`` to work correctly at
    # runtime and fixing the static type errors.
    def process(self, msg: str) -> str:
        return f"A:{msg}"

    def handle(self, msg: str) -> str:
        return self.process(msg)

def run_a() -> str:
    h = AlphaHandler()
    d1 = dispatch(h, "1")
    d2 = dispatch(h, "2")
    return d1 + d2
