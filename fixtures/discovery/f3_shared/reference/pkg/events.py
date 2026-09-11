from typing import List

class EventQueue:
    def pop_event(self) -> str:
        return "event_data"

def process(q: EventQueue) -> List[int]:
    e1 = q.pop_event()
    e2 = q.pop_event()
    l1 = len(e1)
    l2 = len(e2)
    return [l1, l2]
