from typing import Protocol, List

class Renderer(Protocol):
    def render(self, text: str) -> str: ...

class MarkdownRenderer:
    def format(self, text: str) -> str:
        return f"*{text}*"

def display_title(r: Renderer, title: str) -> str:
    return r.render(title)

def run_render() -> List[str]:
    m = MarkdownRenderer()
    # Repeated errors: Argument 1 to "display_title" has incompatible type "MarkdownRenderer"; expected "Renderer"
    t1 = display_title(m, "Hello")
    t2 = display_title(m, "World")
    return [t1, t2]
