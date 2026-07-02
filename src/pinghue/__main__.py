"""Support `python -m pinghue`."""

import sys

from pinghue.cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
