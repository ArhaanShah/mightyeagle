from typing import Protocol, List

class Renderer(Protocol):
    def render(self, text: str) -> str: ...

class MarkdownRenderer:
    def format(self, text: str) -> str:
        """Return the markdown-formatted version of *text*.

        The original implementation only provided a ``format`` method, which
        meant that ``MarkdownRenderer`` did not satisfy the ``Renderer``
        protocol required by :func:`display_title`.  Adding a ``render`` method
        that forwards to ``format`` preserves the public API expected by the
        tests while allowing the object to be used wherever a ``Renderer`` is
        required.
        """
        return f"*{text}*"

    # The ``Renderer`` protocol expects a ``render`` method.  Implement it as a
    # thin wrapper around ``format`` so that ``MarkdownRenderer`` conforms to
    # the protocol without breaking existing callers that use ``format``.
    def render(self, text: str) -> str:
        return self.format(text)

def display_title(r: Renderer, title: str) -> str:
    return r.render(title)

def run_render() -> List[str]:
    m = MarkdownRenderer()
    t1 = display_title(m, "Hello")
    t2 = display_title(m, "World")
    return [t1, t2]
