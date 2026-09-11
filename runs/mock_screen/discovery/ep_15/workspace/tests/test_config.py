from pkg.config_a import run_host
from pkg.config_b import run_port

def test_config():
    assert run_host() == 18
    assert run_port() == 8081
