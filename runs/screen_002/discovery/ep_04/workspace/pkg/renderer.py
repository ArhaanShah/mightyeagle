from typing import Protocol, List

class Renderer(Protocol):
    def render(self, text: str) -> str: ...

class MarkdownRenderer:
    def format(self, text: str) -> str:
        """Return the text wrapped in Markdown emphasis.

        The original implementation exposed a ``format`` method, which is
        used by the public tests.  The :func:`display_title` helper, however,
        expects an object that implements the ``Renderer`` protocol – i.e. it
        must provide a ``render`` method.  To satisfy both requirements without
        altering the public API, we keep ``format`` unchanged and add a thin
        ``render`` wrapper that forwards to ``format``.  This makes
        ``MarkdownRenderer`` conform to ``Renderer`` while preserving the
        existing behaviour expected by the tests.
        """
        return f"*{text}*"

    def render(self, text: str) -> str:
        """Adapter method to satisfy the ``Renderer`` protocol.

        Delegates to :meth:`format` so that callers expecting a ``Renderer``
        (such as :func:`display_title`) receive the same output as callers
        using ``format`` directly.
        """
        return self.format(text)

def display_title(r: Renderer, title: str) -> str:
    return r.render(title)

def run_render() -> List[str]:
    m = MarkdownRenderer()
    t1 = display_title(m, "Hello")
    t2 = display_title(m, "World")
    return [t1, t2]
