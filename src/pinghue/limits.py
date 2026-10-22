"""Open-file limit handling for TCP probe concurrency.

Every in-flight TCP probe holds its own socket. ICMP probes share the ICMP
engine's few sockets, so ICMP concurrency is not bounded by this limit.
"""

from __future__ import annotations

import contextlib
from typing import NamedTuple

from pinghue.probes import MAX_DNS_DAEMON_THREADS

try:
    import resource
except ImportError:  # pragma: no cover - every supported platform provides it
    resource = None  # type: ignore[assignment]

# Descriptors a run uses besides its TCP probe sockets and resolver threads:
# stdio, the event loop's selector and self-pipe, the ICMP engine's shared
# sockets, the TUI driver and the --output file.
FD_BASE_RESERVE = 64
# A lookup stuck in the OS resolver keeps its sockets until libc returns, and
# glibc holds one for each nameserver it has tried, up to MAXNS (3).
RESOLVER_SOCKETS_PER_LOOKUP = 3
# Descriptors kept free beside the TCP probe sockets: the base reserve plus
# every resolver thread holding its sockets during a DNS outage.
FD_RESERVE = FD_BASE_RESERVE + MAX_DNS_DAEMON_THREADS * RESOLVER_SOCKETS_PER_LOOKUP
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


def _raise_soft_limit(limit: FdLimit, needed: int) -> int | None:
    """Raise the soft open-file limit toward ``needed``, never past the hard limit.

    ``limit`` holds the current limits, with a finite soft limit below ``needed``.
    Returns the soft limit now in force (``None`` for unlimited).
    """
    soft = limit.soft
    requested = min(needed, FD_REQUEST_MAXIMUM)
    if limit.hard is not None:
        requested = min(requested, limit.hard)
    if soft is None or requested <= soft:
        return soft
    hard = resource.RLIM_INFINITY if limit.hard is None else limit.hard
    with contextlib.suppress(OSError, ValueError):
        resource.setrlimit(resource.RLIMIT_NOFILE, (requested, hard))
    raised = read_fd_limit()
    return soft if raised is None else raised.soft


def ensure_fd_reserve() -> None:
    """Raise the soft open-file limit to hold ``FD_RESERVE``, when the hard limit allows.

    For runs without TCP probe sockets: ICMP probes share the ICMP engine's few
    sockets, but lookups stuck in the OS resolver still hold theirs. Best effort,
    and silent, since nothing needs lowering when the limit cannot be raised.
    """
    limit = read_fd_limit()
    if limit is not None and limit.soft is not None and limit.soft < FD_RESERVE:
        _raise_soft_limit(limit, FD_RESERVE)


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

    soft = _raise_soft_limit(limit, needed)
    if soft is None or soft >= needed:
        return concurrency, None

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
