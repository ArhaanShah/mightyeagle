from pkg.events import EventQueue, process

def test_events():
    q = EventQueue()
    assert process(q) == [10, 10]
