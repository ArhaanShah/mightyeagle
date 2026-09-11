from pkg.proto import dispatch

class BetaHandler:
    def process(self, msg: str) -> str:
        """Process a message and return a formatted string.

        As with :class:`pkg.handler_a.AlphaHandler`, the ``dispatch`` helper
        expects a ``handle`` method defined by the ``Handler`` protocol.  We
        provide that method below, delegating to ``process`` so the public
        API remains unchanged.
        """
        return f"B:{msg}"

    # Implement the protocol's required ``handle`` method.
    def handle(self, msg: str) -> str:
        """Forward to :meth:`process` to satisfy ``Handler``.
        """
        return self.process(msg)

def run_b() -> str:
    h = BetaHandler()
    return dispatch(h, "3")
