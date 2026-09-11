from pkg.renderer import MarkdownRenderer

def test_renderer():
    m = MarkdownRenderer()
    assert m.format("Hello") == "*Hello*"
