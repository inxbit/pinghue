import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import pinghue.app as app_module
import pinghue.runner as runner
from pinghue import limits
from pinghue.limits import FD_REQUEST_MAXIMUM, FD_RESERVE, FdLimit
from pinghue.models import AddressFamily, ProbeMode, TargetRun

LINUX_RLIM_INFINITY = -1
MACOS_RLIM_INFINITY = 2**63 - 1


class FakeResource:
    """Stands in for the resource module so tests never change the real limit."""

    RLIMIT_NOFILE = 7

    def __init__(
        self,
        soft: int,
        hard: int,
        *,
        infinity: int = LINUX_RLIM_INFINITY,
        setrlimit_error: Exception | None = None,
        getrlimit_error: Exception | None = None,
    ) -> None:
        self.RLIM_INFINITY = infinity
        self.limits = (soft, hard)
        self.setrlimit_calls: list[tuple[int, int]] = []
        self.setrlimit_error = setrlimit_error
        self.getrlimit_error = getrlimit_error

    def getrlimit(self, which: int) -> tuple[int, int]:
        assert which == self.RLIMIT_NOFILE
        if self.getrlimit_error is not None:
            raise self.getrlimit_error
        return self.limits

    def setrlimit(self, which: int, new_limits: tuple[int, int]) -> None:
        assert which == self.RLIMIT_NOFILE
        self.setrlimit_calls.append(new_limits)
        if self.setrlimit_error is not None:
            raise self.setrlimit_error
        self.limits = new_limits


def use_fake_resource(monkeypatch: pytest.MonkeyPatch, fake: FakeResource) -> FakeResource:
    monkeypatch.setattr(limits, "resource", fake)
    return fake


def test_fit_does_not_touch_a_soft_limit_that_already_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_resource(monkeypatch, FakeResource(20000, 20000))

    assert limits.fit_concurrency_to_fd_limit(1024) == (1024, None)
    assert fake.setrlimit_calls == []


@pytest.mark.parametrize("infinity", [LINUX_RLIM_INFINITY, MACOS_RLIM_INFINITY])
def test_fit_raises_soft_limit_below_an_unlimited_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
    infinity: int,
) -> None:
    fake = use_fake_resource(monkeypatch, FakeResource(256, infinity, infinity=infinity))

    assert limits.fit_concurrency_to_fd_limit(1024) == (1024, None)
    # Only the soft limit moves; an unlimited hard limit is passed back unchanged.
    assert fake.setrlimit_calls == [(1024 + FD_RESERVE, infinity)]


def test_fit_raises_soft_limit_up_to_a_sufficient_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_resource(monkeypatch, FakeResource(1024, 524288))

    assert limits.fit_concurrency_to_fd_limit(1024) == (1024, None)
    assert fake.setrlimit_calls == [(1024 + FD_RESERVE, 524288)]


def test_fit_clamps_concurrency_to_a_low_hard_limit_with_one_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_resource(monkeypatch, FakeResource(256, 512))

    concurrency, warning = limits.fit_concurrency_to_fd_limit(1024)

    assert fake.setrlimit_calls == [(512, 512)]
    assert concurrency == 512 - FD_RESERVE
    assert warning == (
        "the open-file limit (soft 512, hard 512) cannot fit 1024 concurrent probes plus "
        f"{FD_RESERVE} reserved descriptors; lowering concurrency to 384. Raise the "
        "open-file limit to at least 1152 to probe more hosts at once."
    )


def test_fit_clamps_without_calling_setrlimit_when_soft_equals_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_resource(monkeypatch, FakeResource(1024, 1024))

    concurrency, warning = limits.fit_concurrency_to_fd_limit(1024)

    assert fake.setrlimit_calls == []
    assert concurrency == 1024 - FD_RESERVE
    assert warning is not None and "(soft 1024, hard 1024)" in warning


@pytest.mark.parametrize(
    "error", [ValueError("not allowed"), OSError(1, "Operation not permitted")]
)
def test_fit_clamps_to_the_current_soft_limit_when_setrlimit_fails(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    # macOS rejects soft limits above kern.maxfilesperproc even below the hard limit.
    fake = use_fake_resource(
        monkeypatch,
        FakeResource(256, MACOS_RLIM_INFINITY, infinity=MACOS_RLIM_INFINITY, setrlimit_error=error),
    )

    concurrency, warning = limits.fit_concurrency_to_fd_limit(569)

    assert fake.setrlimit_calls == [(569 + FD_RESERVE, MACOS_RLIM_INFINITY)]
    assert concurrency == 256 - FD_RESERVE
    assert warning is not None and "(soft 256, hard unlimited)" in warning


def test_fit_clamps_to_the_old_soft_limit_when_it_cannot_be_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForgetfulResource(FakeResource):
        def setrlimit(self, which: int, new_limits: tuple[int, int]) -> None:
            super().setrlimit(which, new_limits)
            self.getrlimit_error = OSError("unsupported")

    use_fake_resource(monkeypatch, ForgetfulResource(256, 512))

    concurrency, warning = limits.fit_concurrency_to_fd_limit(1024)

    assert concurrency == 256 - FD_RESERVE
    assert warning is not None and "(soft 256, hard 512)" in warning


def test_fit_never_requests_more_than_macos_open_max(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_fake_resource(monkeypatch, FakeResource(256, LINUX_RLIM_INFINITY))

    concurrency, warning = limits.fit_concurrency_to_fd_limit(20000)

    assert fake.setrlimit_calls == [(FD_REQUEST_MAXIMUM, LINUX_RLIM_INFINITY)]
    assert concurrency == FD_REQUEST_MAXIMUM - FD_RESERVE
    assert warning is not None


def test_fit_keeps_at_least_one_probe_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_resource(monkeypatch, FakeResource(100, 100))

    concurrency, warning = limits.fit_concurrency_to_fd_limit(64)
    assert concurrency == 1
    assert warning is not None and "lowering concurrency to 1." in warning

    # Nothing lower is possible, so there is nothing to warn about.
    assert limits.fit_concurrency_to_fd_limit(1) == (1, None)


def test_fit_leaves_concurrency_alone_when_limits_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_fake_resource(monkeypatch, FakeResource(64, 64, getrlimit_error=OSError("unsupported")))
    assert limits.fit_concurrency_to_fd_limit(1024) == (1024, None)

    monkeypatch.setattr(limits, "resource", None)
    assert limits.read_fd_limit() is None
    assert limits.fit_concurrency_to_fd_limit(1024) == (1024, None)


def test_read_fd_limit_maps_infinity_to_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_resource(
        monkeypatch,
        FakeResource(MACOS_RLIM_INFINITY, MACOS_RLIM_INFINITY, infinity=MACOS_RLIM_INFINITY),
    )
    assert limits.read_fd_limit() == FdLimit(None, None)

    use_fake_resource(monkeypatch, FakeResource(1024, LINUX_RLIM_INFINITY))
    assert limits.read_fd_limit() == FdLimit(1024, None)


def test_read_fd_limit_reads_the_real_process_limit() -> None:
    limit = limits.read_fd_limit()

    assert limit is not None
    assert limit.soft is None or limit.soft > 0


def test_fit_raises_and_clamps_the_real_process_limit() -> None:
    # The child lowers its own limits, so the test process keeps its own.
    code = textwrap.dedent(
        """
        import resource
        from pinghue.limits import fit_concurrency_to_fd_limit

        _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
        concurrency, warning = fit_concurrency_to_fd_limit(1024)
        print(concurrency, warning is None, resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        concurrency, warning = fit_concurrency_to_fd_limit(1024)
        print(concurrency, warning is None, resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        """
    )
    src_dir = Path(__file__).resolve().parent.parent / "src"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(src_dir)},
        check=True,
        timeout=30,
    )

    raised, clamped = result.stdout.splitlines()
    # The child inherits this process's hard limit; raising needs room under it.
    parent = limits.read_fd_limit()
    if parent is not None and (parent.hard is None or parent.hard >= 1024 + FD_RESERVE):
        assert raised == f"1024 True {1024 + FD_RESERVE}"
    assert clamped == f"{256 - FD_RESERVE} False 256"


def run_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 569,
        "fail_on_all_down": False,
        "fail_on_any_down": False,
        "interval": 1.0,
        "no_tui": True,
        "output": None,
        "port": None,
        "targets": ["127.0.0.1"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def finished_run() -> tuple[list[TargetRun], str, datetime, datetime]:
    now = datetime.now(timezone.utc)
    return [], "completed", now, now


async def test_run_fits_concurrency_before_no_tui_probing_starts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_fake_resource(monkeypatch, FakeResource(256, 256))
    started: list[tuple[int, str]] = []

    async def fake_run_no_tui(
        args: SimpleNamespace, _mode: ProbeMode
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        started.append((args.concurrency, capsys.readouterr().err))
        return finished_run()

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    assert await runner.run(run_args(), mode=ProbeMode.ICMP) == 0

    [(concurrency, stderr_before_probing)] = started
    assert concurrency == 256 - FD_RESERVE
    assert stderr_before_probing.count("pinghue: warning: the open-file limit") == 1
    assert "lowering concurrency to 128" in stderr_before_probing
    assert capsys.readouterr().err == ""


async def test_run_fits_concurrency_before_the_tui_starts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_fake_resource(monkeypatch, FakeResource(256, 256))
    started: list[tuple[int, str]] = []

    class FakeApp:
        def __init__(self, *, args: SimpleNamespace, **_: object) -> None:
            started.append((args.concurrency, capsys.readouterr().err))

        async def run_async(self) -> tuple[list[TargetRun], str, datetime, datetime]:
            return finished_run()

    monkeypatch.setattr(app_module, "PinghueApp", FakeApp)

    assert await runner.run(run_args(no_tui=False), mode=ProbeMode.ICMP) == 0

    [(concurrency, stderr_before_tui)] = started
    assert concurrency == 256 - FD_RESERVE
    assert "lowering concurrency to 128" in stderr_before_tui


async def test_run_raises_the_soft_limit_silently_when_the_hard_limit_allows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = use_fake_resource(monkeypatch, FakeResource(256, LINUX_RLIM_INFINITY))
    seen: list[int] = []

    async def fake_run_no_tui(
        args: SimpleNamespace, _mode: ProbeMode
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        seen.append(args.concurrency)
        return finished_run()

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    assert await runner.run(run_args(), mode=ProbeMode.ICMP) == 0

    assert seen == [569]
    assert fake.setrlimit_calls == [(569 + FD_RESERVE, LINUX_RLIM_INFINITY)]
    assert capsys.readouterr().err == ""
