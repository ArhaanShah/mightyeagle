from pkg.proto import TextSink


class Handler:
    def render(self, text: str) -> str:
        return f"E:{text}"


sink_e1: TextSink = Handler()
sink_e2: TextSink = Handler()
