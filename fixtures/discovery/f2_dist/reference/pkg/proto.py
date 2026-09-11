from typing import Protocol


class TextSink(Protocol):
    def render(self, text: str) -> str: ...
