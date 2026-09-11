from pkg.proto import TextSink


class Handler:
    def render(self, text: str) -> str:
        return f"A:{text}"


sink_a1: TextSink = Handler()
sink_a2: TextSink = Handler()
