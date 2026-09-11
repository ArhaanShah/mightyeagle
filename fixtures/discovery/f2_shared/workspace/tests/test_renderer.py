from pkg.renderer import MarkdownRenderer, run_render

def test_renderer():
    m = MarkdownRenderer()
    assert m.format("Hello") == "*Hello*"
    assert m.render("Hello") == b"*Hello*"
    assert m.render("") == b"**"
    assert run_render() == "*Hello*"
