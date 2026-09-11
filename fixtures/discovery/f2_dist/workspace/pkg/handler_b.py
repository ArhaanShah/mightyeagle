from pkg.proto import TextSink


class Handler:
    def render(self, text: str) -> bytes:
        return f"B:{text}"


sink_b1: TextSink = Handler()
sink_b2: TextSink = Handler()
