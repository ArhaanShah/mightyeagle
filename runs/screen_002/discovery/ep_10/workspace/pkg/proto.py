from typing import Protocol

class Handler(Protocol):
    def handle(self, msg: str) -> str: ...

def dispatch(h: Handler, msg: str) -> str:
    return h.handle(msg)
