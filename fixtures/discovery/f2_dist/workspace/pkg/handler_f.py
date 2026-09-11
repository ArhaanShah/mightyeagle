from pkg.proto import TextSink


class Handler:
    def render(self, text: str) -> bytes:
        return f"F:{text}"


sink_f1: TextSink = Handler()
sink_f2: TextSink = Handler()
