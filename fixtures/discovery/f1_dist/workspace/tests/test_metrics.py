from pkg.normalize import use_a, use_a_again
from pkg.scale import use_b, use_b_again
from pkg.source_c import use_c, use_c_again
from pkg.source_d import use_d, use_d_again
from pkg.source_e import use_e, use_e_again
from pkg.source_f import use_f, use_f_again


def test_sources():
    assert [use_a(), use_b(), use_c(), use_d(), use_e(), use_f()] == [2, 3, 4, 5, 6, 7]
    assert [
        use_a_again(), use_b_again(), use_c_again(), use_d_again(),
        use_e_again(), use_f_again(),
    ] == [3, 4, 5, 6, 7, 8]
