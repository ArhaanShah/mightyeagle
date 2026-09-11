from typing import Protocol

class Renderer(Protocol):
    def render(self, text: str) -> str: ...

class MarkdownRenderer(Renderer):
    def format(self, text: str) -> str:
        return f"*{text}*"
    def render(self, text: str) -> str:
        return self.format(text)

def display_title(r: Renderer, title: str) -> str:
    return r.render(title)

def run_render() -> list[str]:
    renderer = MarkdownRenderer()
    return [display_title(renderer, title) for title in ("Hello", "World")]
