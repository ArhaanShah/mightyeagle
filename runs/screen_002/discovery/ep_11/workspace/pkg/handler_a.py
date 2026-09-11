from pkg.proto import dispatch

class AlphaHandler:
    def process(self, msg: str) -> str:
        """Process a message and return a formatted string.

        This method is part of the public API used by the tests. The
        :func:`pkg.proto.dispatch` helper expects a ``handle`` method as
        defined by the ``Handler`` protocol. To satisfy the protocol while
        preserving the original behaviour we provide a thin ``handle``
        wrapper that forwards to ``process``.
        """
        return f"A:{msg}"

    # The protocol ``Handler`` requires a ``handle`` method. Adding this
    # method makes ``AlphaHandler`` a structural subtype of ``Handler``
    # without altering its external contract.
    def handle(self, msg: str) -> str:
        """Delegate to :meth:`process` to satisfy the ``Handler`` protocol.

        Keeping the implementation separate from ``process`` ensures that
        existing callers that rely on ``process`` continue to work unchanged.
        """
        return self.process(msg)

def run_a() -> str:
    h = AlphaHandler()
    d1 = dispatch(h, "1")
    d2 = dispatch(h, "2")
    return d1 + d2
