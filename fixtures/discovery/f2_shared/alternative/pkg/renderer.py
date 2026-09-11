from typing import Protocol

ByteText = bytes


class Renderer(Protocol):
    def render(self, text: str) -> bytes: ...


class MarkdownRenderer:
    def render(self, text: str) -> ByteText:
        return f"*{text}*".encode()

    def format(self, text: str) -> str:
        return f"*{text}*"


slots: list[Renderer] = [
    MarkdownRenderer(), MarkdownRenderer(), MarkdownRenderer(),
    MarkdownRenderer(), MarkdownRenderer(), MarkdownRenderer(),
    MarkdownRenderer(), MarkdownRenderer(), MarkdownRenderer(),
    MarkdownRenderer(), MarkdownRenderer(), MarkdownRenderer(),
]


def run_render() -> str:
    return MarkdownRenderer().format("Hello")
