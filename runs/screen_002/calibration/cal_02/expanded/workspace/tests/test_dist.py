from pkg.part_a import run_a
from pkg.part_b import run_b
def test_dist():
    assert run_a() == 1
    assert run_b() == 11
