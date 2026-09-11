class EventQueue:
    def pop_event(self) -> str:
        return "event_data"

def process(q: EventQueue) -> list[int]:
    events = (q.pop_event(), q.pop_event())
    return [len(event) for event in events]
