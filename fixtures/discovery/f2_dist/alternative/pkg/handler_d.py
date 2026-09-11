from pkg.proto import TextSink


class Handler:
    def render(self, text: str) -> str:
        return f"D:{text}"


sink_d1: TextSink = Handler()
sink_d2: TextSink = Handler()
