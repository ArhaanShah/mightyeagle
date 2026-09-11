from pkg.proto import TextSink


class Handler:
    def render(self, text: str) -> str:
        return f"C:{text}"


sink_c1: TextSink = Handler()
sink_c2: TextSink = Handler()
