from pkg.db_config import run_db
from pkg.web_config import run_web

def test_settings():
    assert run_db() == 10864
    assert run_web() == 4
