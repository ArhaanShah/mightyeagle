from pkg.handler_a import Handler as AlphaHandler
from pkg.handler_b import Handler as BetaHandler


def test_handlers():
    assert AlphaHandler().render("1") == "A:1"
    assert BetaHandler().render("3") == "B:3"
