import sys

from config import PIPELINE_STAGE
from .ingest import main as run


def main() -> None:
    stage = PIPELINE_STAGE.lower()

    if stage == "ingest":
        from .ingest import main as run
    elif stage == "validate":
        from .validate import main as run
    elif stage == "publish":
        from .publish import main as run
    elif stage == "crashloop":
        from .crashloop import main as run
    else:
        print(f"PIPELINE_ERROR: unknown stage '{stage}'", file=sys.stderr)
        sys.exit(1)

    run()
