from pkg.handler_a import AlphaHandler
from pkg.handler_b import BetaHandler

def test_a():
    assert AlphaHandler().process("1") == "A:1"

def test_b():
    assert BetaHandler().process("3") == "B:3"
