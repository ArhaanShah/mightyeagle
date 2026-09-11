from pkg.normalize import normalize, run_norm
from pkg.scale import scale_batch, run_scale

def test_normalize():
    assert normalize(10.0, 2.0) == 5.0
    run_norm()

def test_scale_batch():
    assert scale_batch([1.0, 2.0], 2.0) == [2.0, 4.0]
    run_scale()
