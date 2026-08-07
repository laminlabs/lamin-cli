import importlib
import sys
import uuid
from code import InteractiveConsole
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


class LaminConsole(InteractiveConsole):
    """A minimally wrapped interactive console for tracking."""

    locals: dict[str, Any]

    def __init__(self):
        _locals = {}
        super().__init__(locals=_locals, filename="<console>")
        self.history = []

    def resetbuffer(self):
        """Reset the input buffer."""
        if getattr(self, "buffer", []):
            self.history.append("\n".join(self.buffer))
        self.buffer = []

    def tracked_interact(self, instance: str | None):
        key = f"interactive-{uuid.uuid4()}"
        self.filename = f"{key}.py"

        banner = [
            f"Lamin CLI Python {sys.version} on {sys.platform}",
            "This session is tracked on your lamin instance.",
            "",
        ]
        self.write("\n".join(banner))

        self.write(">>> import lamindb as ln\n")
        self.history.append("import lamindb as ln")
        ln = importlib.import_module("lamindb")

        if instance is not None:
            self.write(f'>>> ln.connect("{instance}")\n')
            self.history.append(f'ln.connect("{instance}")')
            ln.connect(instance)

        # make sure to error early in case connect didn't connect
        ln_finish = importlib.import_module("lamindb._finish")

        with TemporaryDirectory(prefix="lamin-cli-") as tdir:
            source = Path(tdir).joinpath(self.filename)

            self.write(">>> ln.track()\n")
            # FIXME: type should be "interactive"
            t = ln.Transform(key=key, type="script").save()
            ln.track(t.uid, path=source)
            self.history.append(f'ln.track("{t.uid}")')

            self.locals.update(
                {
                    "__main__": t.key,
                    "__doc__": None,
                    "ln": ln,
                }
            )

            try:
                self.interact(banner=False)  # type: ignore
            finally:
                source.write_text("\n".join(self.history))

                ln_finish.save_context_core(
                    run=ln.context.run,
                    transform=t,
                    filepath=source,
                    finished_at=True,
                    ignore_non_consecutive=None,
                    from_cli=True,
                    is_retry=False,
                )


if __name__ == "__main__":
    console = LaminConsole()
    console.tracked_interact(sys.argv[1])
