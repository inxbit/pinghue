"""Open-file limit handling for TCP probe concurrency.

Every in-flight TCP probe holds its own socket. ICMP probes share the ICMP
engine's few sockets, so ICMP concurrency is not bounded by this limit.
"""

from __future__ import annotations

import contextlib
from typing import NamedTuple

try:
    import resource
except ImportError:  # pragma: no cover - every supported platform provides it
    resource = None  # type: ignore[assignment]

# Descriptors kept free beside the TCP probe sockets: stdio, the event loop's
# selector and self-pipe, the TUI driver, DNS lookups still running on their
# resolver threads, and the --output file.
FD_RESERVE = 128
# A run uses a dozen or so descriptors besides its probe sockets, so a limit too
# small for the full reserve keeps half of itself, but never less than this.
FD_RESERVE_MINIMUM = 32
# macOS OPEN_MAX: setrlimit rejects a larger soft limit there even when the hard
# limit is RLIM_INFINITY, so pinghue never asks for more.
FD_REQUEST_MAXIMUM = 10_240


class FdLimit(NamedTuple):
    """The RLIMIT_NOFILE soft and hard limits; ``None`` means unlimited."""

    soft: int | None
    hard: int | None


def _limit_value(value: int) -> int | None:
    # RLIM_INFINITY is -1 on Linux and 2**63 - 1 on macOS.
    if value < 0 or value == resource.RLIM_INFINITY:
        return None
    return value


def read_fd_limit() -> FdLimit | None:
    """Return the process open-file limits, or ``None`` when they are unavailable."""
    if resource is None:  # pragma: no cover - every supported platform provides it
        return None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError):
        return None
    return FdLimit(_limit_value(soft), _limit_value(hard))


def fd_limit_label(value: int | None) -> str:
    return "unlimited" if value is None else str(value)


def concurrency_capacity(limit: int) -> int:
    """Return how many concurrent probes an open-file limit of ``limit`` can hold."""
    reserve = min(FD_RESERVE, max(FD_RESERVE_MINIMUM, limit // 2))
    return max(1, limit - reserve)


def fit_concurrency_to_fd_limit(concurrency: int) -> tuple[int, str | None]:
    """Make room for ``concurrency`` in-flight TCP probes, each holding one socket.

    Raises the soft open-file limit toward ``concurrency + FD_RESERVE``, never past
    the hard limit. When the limit still cannot fit, returns a lower concurrency and
    a warning for the operator; otherwise returns ``concurrency`` unchanged and
    ``None``. A limit below twice ``FD_RESERVE`` keeps a smaller reserve (see
    ``concurrency_capacity``), so small limits still fit a few dozen probes.
    """
    needed = concurrency + FD_RESERVE
    limit = read_fd_limit()
    if limit is None or limit.soft is None or limit.soft >= needed:
        return concurrency, None

    soft = limit.soft
    requested = min(needed, FD_REQUEST_MAXIMUM)
    if limit.hard is not None:
        requested = min(requested, limit.hard)
    if requested > soft:
        hard = resource.RLIM_INFINITY if limit.hard is None else limit.hard
        with contextlib.suppress(OSError, ValueError):
            resource.setrlimit(resource.RLIMIT_NOFILE, (requested, hard))
        raised = read_fd_limit()
        if raised is not None:
            if raised.soft is None or raised.soft >= needed:
                return concurrency, None
            soft = raised.soft

    effective = concurrency_capacity(soft)
    if effective >= concurrency:
        return concurrency, None
    warning = (
        f"the open-file limit (soft {soft}, hard {fd_limit_label(limit.hard)}) "
        f"cannot fit {concurrency} concurrent TCP probes plus the descriptors pinghue keeps "
        f"in reserve; lowering concurrency to {effective}. Raise the open-file limit "
        f"to at least {needed} to probe more hosts at once."
    )
    return effective, warning
