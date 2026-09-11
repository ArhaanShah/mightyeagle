from typing import Optional, List

class EventQueue:
    def pop_event(self) -> Optional[str]:
        return "event_data"

def process(q: EventQueue) -> List[int]:
    """Process two events from the queue and return their lengths.

    ``EventQueue.pop_event`` is typed as returning ``Optional[str]`` because a
    real implementation could signal an empty queue with ``None``.  In the
    simplified test scenario the method always returns a string, but the type
    checker still sees the possibility of ``None``.  To satisfy static typing
    without changing the public API we assert that the values are not ``None``.
    The assertions act as runtime guards (preserving behaviour) and also narrow
    the type for the subsequent ``len`` calls.
    """

    e1 = q.pop_event()
    e2 = q.pop_event()
    # Ensure the values are strings for type checking and runtime safety.
    assert e1 is not None, "EventQueue.pop_event() returned None for the first event"
    assert e2 is not None, "EventQueue.pop_event() returned None for the second event"
    l1 = len(e1)
    l2 = len(e2)
    return [l1, l2]
