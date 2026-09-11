from typing import List

class EventQueue:
    def pop_event(self) -> str:
        """Return the next event data.

        The original implementation annotated the return type as ``Optional[str]``
        even though the method always returns a string. This caused type errors
        when ``len`` was called on the result because ``None`` is not ``Sized``.
        Updating the annotation to ``str`` resolves the static type checking
        issue while preserving the runtime behavior.
        """
        return "event_data"

def process(q: EventQueue) -> List[int]:
    e1 = q.pop_event()
    e2 = q.pop_event()
    l1 = len(e1)
    l2 = len(e2)
    return [l1, l2]
