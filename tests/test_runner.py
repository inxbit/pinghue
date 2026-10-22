import asyncio
import io
import socket
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import pinghue.probes as probes
import pinghue.runner as runner
from pinghue.models import (
    AddressFamily,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    TargetRun,
    TargetStatus,
)
from pinghue.probes import ResolvedTarget, ResolverBusyError


def build_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 1,
        "fail_on_all_down": False,
        "fail_on_any_down": False,
        "fail_on_down": False,
        "fail_threshold": 3,
        "history_style": "bar",
        "host_label": "maintenance-window",
        "interval": 1.0,
        "jitter_threshold": 50.0,
        "no_samples": False,
        "no_tui": True,
        "numeric": False,
        "output": None,
        "output_mode": "private",
        "overwrite": False,
        "port": None,
        "targets": ["1.1.1.1"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_print_sample_routes_both_branches_to_given_stream() -> None:
    stream = io.StringIO()
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    sample = ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=2.0,
        status=SampleStatus.OK,
    )

    runner.print_sample(target, sample, stream=stream)
    failed = TargetRun(target="bad.example", error="resolution failed")
    runner.print_sample(failed, None, stream=stream)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert "1.1.1.1 ok latency=2.00ms" in lines[0]
    assert "bad.example dns_failure error=resolution failed" in lines[1]


async def test_run_writes_configured_host_label(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return [], "completed", timestamp, timestamp

    def fake_write_output_json(_: Path, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)
    monkeypatch.setattr(runner, "write_output_json", fake_write_output_json)

    exit_code = await runner.run(
        build_args(output=tmp_path / "out.json", host_label="operator-selected"),
        mode=ProbeMode.ICMP,
    )

    assert exit_code == 0
    assert captured["host"] == "operator-selected"
    assert captured["overwrite"] is False
    assert captured["output_mode"] == "private"


async def test_run_fail_on_down_returns_nonzero_when_all_targets_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun("1.1.1.1", status=TargetStatus.DOWN)

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return [target], "completed", timestamp, timestamp

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    exit_code = await runner.run(build_args(fail_on_all_down=True), mode=ProbeMode.ICMP)

    assert exit_code == 3


async def test_run_fail_on_down_returns_zero_when_any_target_is_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [
        TargetRun("1.1.1.1", status=TargetStatus.DOWN),
        TargetRun("8.8.8.8", status=TargetStatus.HEALTHY),
    ]

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return targets, "completed", timestamp, timestamp

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    exit_code = await runner.run(build_args(fail_on_all_down=True), mode=ProbeMode.ICMP)

    assert exit_code == 0


async def test_run_fail_on_any_down_returns_nonzero_when_any_target_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [
        TargetRun("1.1.1.1", status=TargetStatus.DOWN),
        TargetRun("8.8.8.8", status=TargetStatus.HEALTHY),
    ]

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return targets, "completed", timestamp, timestamp

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    exit_code = await runner.run(build_args(fail_on_any_down=True), mode=ProbeMode.ICMP)

    assert exit_code == 3


async def test_run_legacy_fail_on_down_matches_all_down_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [
        TargetRun("1.1.1.1", status=TargetStatus.DOWN),
        TargetRun("8.8.8.8", status=TargetStatus.HEALTHY),
    ]

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return targets, "completed", timestamp, timestamp

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    exit_code = await runner.run(build_args(fail_on_down=True), mode=ProbeMode.ICMP)

    assert exit_code == 0


def test_short_all_failed_run_is_down_and_trips_fail_on_any_down() -> None:
    # H1: a run shorter than fail_threshold with zero successful samples must be
    # DOWN and return a failing exit code, not be treated as usable/INTERMITTENT.
    target = TargetRun("192.0.2.1")
    for _ in range(2):  # fewer samples than the default fail_threshold of 3
        target.apply_sample(
            ProbeSample(
                timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
                latency_ms=None,
                status=SampleStatus.TIMEOUT,
            ),
            fail_threshold=3,
            jitter_threshold_ms=50.0,
        )

    assert target.status == TargetStatus.DOWN
    assert (
        runner.exit_code_for_targets([target], fail_on_any_down=True, fail_on_all_down=False) == 3
    )


async def test_probe_once_caches_and_prioritizes_working_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    target = TargetRun(
        target="service.example",
        resolved_address="192.0.2.1",
        resolved_family=AddressFamily.IPV4,
        resolved_addresses=("192.0.2.1", "192.0.2.2"),
    )

    async def fake_tcp_probe(address: str, _port: int, *, timeout_s: float) -> ProbeSample:
        assert timeout_s > 0
        calls.append(address)
        if address == "192.0.2.1":
            return ProbeSample(
                timestamp=datetime.now(timezone.utc),
                latency_ms=None,
                status=SampleStatus.TIMEOUT,
            )
        return ProbeSample(
            timestamp=datetime.now(timezone.utc),
            latency_ms=3.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "tcp_probe", fake_tcp_probe)
    args = build_args(port=443)

    sample1 = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.TCP,
        semaphore=asyncio.Semaphore(1),
    )
    assert sample1 is not None
    assert sample1.status == SampleStatus.OK
    assert target.resolved_address == "192.0.2.2"
    assert calls == ["192.0.2.1", "192.0.2.2"]

    calls.clear()
    sample2 = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.TCP,
        semaphore=asyncio.Semaphore(1),
    )
    assert sample2 is not None
    assert sample2.status == SampleStatus.OK
    assert target.resolved_address == "192.0.2.2"
    assert calls == ["192.0.2.2"]


async def test_probe_once_attempts_dns_re_resolution_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="re-resolve.example",
        status=TargetStatus.DNS_FAILURE,
    )
    target._last_resolve_time = 0.0

    resolve_calls = 0

    async def fake_resolve_target(
        target_name: str,
        _family: AddressFamily,
        *,
        numeric: bool,
    ) -> ResolvedTarget:
        assert numeric is not None
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 1:
            return ResolvedTarget(target_name, None, None, "still failing")
        return ResolvedTarget(
            target_name,
            "192.0.2.5",
            AddressFamily.IPV4,
            addresses=("192.0.2.5",),
        )

    monkeypatch.setattr(runner, "resolve_target", fake_resolve_target)
    args = build_args()

    sample1 = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.ICMP,
        semaphore=asyncio.Semaphore(1),
    )
    assert sample1 is None
    assert target.status == TargetStatus.DNS_FAILURE
    assert target.resolved_address is None

    target._last_resolve_time = 0.0

    async def fake_probe_address(*_: object, **__: object) -> ProbeSample:
        return ProbeSample(
            timestamp=datetime.now(timezone.utc),
            latency_ms=10.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "_probe_address", fake_probe_address)

    sample2 = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.ICMP,
        semaphore=asyncio.Semaphore(1),
    )
    assert sample2 is not None
    assert sample2.status == SampleStatus.OK
    assert target.status == TargetStatus.HEALTHY
    assert target.resolved_address == "192.0.2.5"


async def test_initial_resolution_starts_cooldown_and_prevents_immediate_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def initial_failure(*_: object, **__: object) -> ResolvedTarget:
        return ResolvedTarget("service.example", None, None, "not found")

    monkeypatch.setattr(runner, "_monotonic_time", lambda: 5.0, raising=False)
    monkeypatch.setattr(runner, "_resolve_target_bounded", initial_failure)
    args = build_args(targets=["service.example"])

    target = await runner.resolve_run_target("service.example", args)

    assert target._last_resolve_time == 5.0

    retry_calls = 0

    async def unexpected_retry(*_: object, **__: object) -> ResolvedTarget:
        nonlocal retry_calls
        retry_calls += 1
        return ResolvedTarget("service.example", None, None, "still missing")

    monkeypatch.setattr(runner, "_monotonic_time", lambda: 5.5, raising=False)
    monkeypatch.setattr(runner, "_resolve_target_bounded", unexpected_retry)

    sample_result = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.ICMP,
        semaphore=asyncio.Semaphore(1),
    )

    assert sample_result is None
    assert retry_calls == 0


async def test_probe_once_refreshes_stale_dns_after_repeated_address_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="service.example",
        resolved_address="192.0.2.10",
        resolved_family=AddressFamily.IPV4,
        resolved_addresses=("192.0.2.10",),
    )
    target._last_resolve_time = 0.0
    for _ in range(2):
        target.apply_sample(
            ProbeSample(
                timestamp=datetime.now(timezone.utc),
                latency_ms=None,
                status=SampleStatus.TIMEOUT,
            ),
            fail_threshold=2,
            jitter_threshold_ms=50.0,
        )

    resolution_calls = 0

    async def refreshed_resolution(*_: object, **__: object) -> ResolvedTarget:
        nonlocal resolution_calls
        resolution_calls += 1
        return ResolvedTarget(
            "service.example",
            "192.0.2.20",
            AddressFamily.IPV4,
            addresses=("192.0.2.20",),
        )

    probed_addresses: list[str] = []

    async def successful_probe(address: str, **_: object) -> ProbeSample:
        probed_addresses.append(address)
        return ProbeSample(
            timestamp=datetime.now(timezone.utc),
            latency_ms=2.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "_monotonic_time", lambda: 11.0, raising=False)
    monkeypatch.setattr(runner, "_resolve_target_bounded", refreshed_resolution)
    monkeypatch.setattr(runner, "_probe_address", successful_probe)
    args = build_args(fail_threshold=2, targets=["service.example"])

    sample_result = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.ICMP,
        semaphore=asyncio.Semaphore(1),
    )

    assert sample_result is not None
    assert sample_result.status == SampleStatus.OK
    assert resolution_calls == 1
    assert probed_addresses == ["192.0.2.20"]
    assert target.resolved_address == "192.0.2.20"


async def test_failed_stale_dns_refresh_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="service.example",
        resolved_address="192.0.2.10",
        resolved_family=AddressFamily.IPV4,
        resolved_addresses=("192.0.2.10",),
    )
    target._last_resolve_time = 0.0
    for _ in range(2):
        target.apply_sample(
            ProbeSample(
                timestamp=datetime.now(timezone.utc),
                latency_ms=None,
                status=SampleStatus.TIMEOUT,
            ),
            fail_threshold=2,
            jitter_threshold_ms=50.0,
        )

    resolution_calls = 0

    async def failed_resolution(*_: object, **__: object) -> ResolvedTarget:
        nonlocal resolution_calls
        resolution_calls += 1
        return ResolvedTarget("service.example", None, None, "temporary DNS failure")

    async def failed_probe(*_: object, **__: object) -> ProbeSample:
        return ProbeSample(
            timestamp=datetime.now(timezone.utc),
            latency_ms=None,
            status=SampleStatus.TIMEOUT,
        )

    # The lookup itself may spend a long time queued behind the resolver bound.
    # Cooldown starts when that attempt completes, not when it entered the queue.
    monotonic_times = iter([11.0, 30.0, 35.0])
    monkeypatch.setattr(
        runner,
        "_monotonic_time",
        lambda: next(monotonic_times),
        raising=False,
    )
    monkeypatch.setattr(runner, "_resolve_target_bounded", failed_resolution)
    monkeypatch.setattr(runner, "_probe_address", failed_probe)
    args = build_args(fail_threshold=2, targets=["service.example"])

    for _ in range(2):
        await runner.probe_once(
            target,
            args=args,
            mode=ProbeMode.ICMP,
            semaphore=asyncio.Semaphore(1),
        )

    assert resolution_calls == 1


async def test_probe_once_handles_unexpected_exceptions_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="exception.example",
        resolved_address="192.0.2.1",
        resolved_family=AddressFamily.IPV4,
    )

    async def fake_probe_address(*_: object, **__: object) -> ProbeSample:
        raise RuntimeError("Severe socket failure or interface down")

    monkeypatch.setattr(runner, "_probe_address", fake_probe_address)
    args = build_args()

    sample = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.ICMP,
        semaphore=asyncio.Semaphore(1),
    )
    assert sample is not None
    assert sample.status == SampleStatus.ERROR
    assert "Unexpected probe error" in sample.error
    assert "Severe socket failure" in sample.error


async def test_resolve_runs_limits_concurrency_with_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_resolutions = 0
    max_active_resolutions = 0

    async def fake_resolve_target(
        target: str,
        _family: AddressFamily,
        *,
        numeric: bool,
    ) -> ResolvedTarget:
        assert numeric is not None
        nonlocal active_resolutions, max_active_resolutions
        active_resolutions += 1
        max_active_resolutions = max(max_active_resolutions, active_resolutions)
        await asyncio.sleep(0.01)
        active_resolutions -= 1
        return ResolvedTarget(target, "192.0.2.1", AddressFamily.IPV4)

    monkeypatch.setattr(runner, "resolve_target", fake_resolve_target)

    test_semaphore = asyncio.Semaphore(2)
    monkeypatch.setattr(runner, "_dns_semaphore", test_semaphore)
    monkeypatch.setattr(runner, "_get_dns_semaphore", lambda: test_semaphore)

    args = build_args(targets=["a", "b", "c", "d"])

    await runner.resolve_runs(args)

    assert max_active_resolutions == 2


async def test_resolve_run_target_converts_unexpected_exception_to_target_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve_target(
        _target: str,
        _family: AddressFamily,
        *,
        numeric: bool,
    ) -> ResolvedTarget:
        assert numeric is not None
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(runner, "resolve_target", fake_resolve_target)

    resolved = await runner.resolve_run_target("unstable.example", build_args())

    assert resolved.target == "unstable.example"
    assert resolved.status == TargetStatus.DNS_FAILURE
    assert resolved.resolved_address is None
    assert resolved.error == "resolver exploded"


async def test_resolve_run_target_converts_slow_dns_to_target_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve_target(
        _target: str,
        _family: AddressFamily,
        *,
        numeric: bool,
    ) -> ResolvedTarget:
        assert numeric is not None
        await asyncio.sleep(1)
        return ResolvedTarget("slow.example", "192.0.2.44", AddressFamily.IPV4)

    monkeypatch.setattr(runner, "resolve_target", fake_resolve_target)
    monkeypatch.setattr(runner, "DNS_LOOKUP_TIMEOUT_SECONDS", 0.01)

    resolved = await runner.resolve_run_target("slow.example", build_args())

    assert resolved.target == "slow.example"
    assert resolved.status == TargetStatus.DNS_FAILURE
    assert resolved.resolved_address is None
    assert resolved.error == "getaddrinfo timed out after 0.01s"


async def _ok_probe(*_: object, **__: object) -> ProbeSample:
    return ProbeSample(
        timestamp=datetime.now(timezone.utc),
        latency_ms=1.0,
        status=SampleStatus.OK,
    )


def _blackholed_zone_getaddrinfo(release: threading.Event) -> Callable[..., list[object]]:
    """getaddrinfo stand-in: "hang-" names wait for a dead nameserver, others resolve."""

    def fake_getaddrinfo(host: str, *_: object, **__: object) -> list[object]:
        if host.startswith("hang-"):
            release.wait(10.0)
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    return fake_getaddrinfo


async def test_healthy_name_resolves_promptly_after_max_abandoned_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Blackholed names first, retried until MAX_DNS_DAEMON_THREADS lookups were
    # abandoned: their threads still hold slots, yet a healthy name must
    # resolve at once instead of failing with a resolver worker limit.
    release = threading.Event()
    calls: list[str] = []
    resolve = _blackholed_zone_getaddrinfo(release)

    def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[object]:
        calls.append(host)
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(runner, "DNS_LOOKUP_TIMEOUT_SECONDS", 0.05)
    hung_names = [f"hang-{index}.regression.test" for index in range(16)]
    loop = asyncio.get_running_loop()

    try:
        for _ in range(probes.MAX_DNS_DAEMON_THREADS // len(hung_names)):
            hung = await asyncio.gather(
                *(
                    runner._resolve_target_bounded(name, AddressFamily.AUTO, numeric=False)
                    for name in hung_names
                )
            )
            assert all(
                result is not None and "timed out" in (result.error or "") for result in hung
            )

        started = loop.time()
        good = await asyncio.wait_for(
            runner._resolve_target_bounded(
                "good.regression.test", AddressFamily.AUTO, numeric=False
            ),
            timeout=2.0,
        )
        elapsed = loop.time() - started
    finally:
        release.set()

    assert good is not None
    assert good.error is None
    assert good.address == "127.0.0.1"
    assert elapsed < 1.0
    # Retries joined the still-running lookups: one resolver thread per stuck name.
    assert sorted(host for host in calls if host.startswith("hang-")) == sorted(hung_names)


async def test_resolver_capacity_miss_keeps_target_pending_without_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def busy_resolver(*_: object, **__: object) -> ResolvedTarget:
        raise ResolverBusyError("all resolver threads are busy")

    monkeypatch.setattr(runner, "resolve_target", busy_resolver)
    args = build_args(targets=["busy.example"])

    pending = await runner.resolve_run_target("busy.example", args)

    assert pending.status == TargetStatus.RESOLVING
    assert pending.error is None
    # No cooldown: the first probe iteration retries right away.
    assert pending._last_resolve_time is None

    sample = await runner.probe_once(
        pending, args=args, mode=ProbeMode.ICMP, semaphore=asyncio.Semaphore(1)
    )

    assert sample is None
    assert pending.status == TargetStatus.RESOLVING
    assert pending.error is None
    assert pending._last_resolve_time is None
    stream = io.StringIO()
    runner.print_sample(pending, None, stream=stream)
    assert stream.getvalue() == ""

    # A target with an earlier verdict keeps it, and its cooldown anchor.
    failed = TargetRun(
        target="busy.example",
        status=TargetStatus.DNS_FAILURE,
        error="getaddrinfo: Name or service not known",
    )
    failed._last_resolve_time = 0.0
    sample = await runner.probe_once(
        failed, args=args, mode=ProbeMode.ICMP, semaphore=asyncio.Semaphore(1)
    )

    assert sample is None
    assert failed.status == TargetStatus.DNS_FAILURE
    assert failed.error == "getaddrinfo: Name or service not known"
    assert failed._last_resolve_time == 0.0
    assert runner._dns_retry_delay("busy.example") == runner.DNS_RETRY_INTERVAL_SECONDS


async def test_resolve_runs_resolves_healthy_names_listed_after_hung_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Host list with a blackholed zone first, as many names as the old
    # resolver-thread cap: their abandoned lookups used to fail every later
    # name with "resolver worker limit reached".
    release = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", _blackholed_zone_getaddrinfo(release))
    monkeypatch.setattr(runner, "DNS_LOOKUP_TIMEOUT_SECONDS", 0.05)
    hung_names = [f"hang-{index}.runs.test" for index in range(16)]
    good_names = [f"ok-{index}.runs.test" for index in range(200)]

    try:
        runs = await asyncio.wait_for(
            runner.resolve_runs(build_args(targets=hung_names + good_names)), timeout=5.0
        )
    finally:
        release.set()

    hung_runs, good_runs = runs[: len(hung_names)], runs[len(hung_names) :]
    assert all(run.status == TargetStatus.DNS_FAILURE for run in hung_runs)
    assert all("timed out" in (run.error or "") for run in hung_runs)
    assert all(run.error is None for run in good_runs)
    assert all(run.resolved_address == "127.0.0.1" for run in good_runs)


async def test_exhausted_resolver_threads_leave_targets_pending_until_one_frees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", _blackholed_zone_getaddrinfo(release))
    monkeypatch.setattr(probes, "_dns_thread_slots", threading.BoundedSemaphore(4))
    monkeypatch.setattr(probes, "DNS_THREAD_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(runner, "DNS_LOOKUP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(runner, "_probe_address", _ok_probe)
    hung_names = [f"hang-{index}.exhausted.test" for index in range(4)]
    good_names = [f"ok-{index}.exhausted.test" for index in range(3)]
    args = build_args(targets=hung_names + good_names)

    try:
        runs = await asyncio.wait_for(runner.resolve_runs(args), timeout=5.0)
        good_runs = runs[len(hung_names) :]

        # Every thread is held by a stuck lookup, so no lookup ran for the
        # healthy names: they stay pending, with no verdict, line or cooldown.
        assert [run.status for run in good_runs] == [TargetStatus.RESOLVING] * 3
        assert all(run.error is None and run._last_resolve_time is None for run in good_runs)
        stream = io.StringIO()
        for run in good_runs:
            runner.print_sample(run, None, stream=stream)
        assert stream.getvalue() == ""

        release.set()
        monkeypatch.setattr(probes, "DNS_THREAD_WAIT_SECONDS", 2.0)
        samples = [
            await asyncio.wait_for(
                runner.probe_once(
                    run, args=args, mode=ProbeMode.ICMP, semaphore=asyncio.Semaphore(1)
                ),
                timeout=5.0,
            )
            for run in good_runs
        ]
    finally:
        release.set()

    assert all(sample is not None and sample.status == SampleStatus.OK for sample in samples)
    assert [run.status for run in good_runs] == [TargetStatus.HEALTHY] * 3


async def test_probe_loop_retries_resolver_capacity_miss_without_dns_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def busy_then_resolved(name: str, *_: object, **__: object) -> ResolvedTarget:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ResolverBusyError("all resolver threads are busy")
        return ResolvedTarget(name, "192.0.2.60", AddressFamily.IPV4, addresses=("192.0.2.60",))

    monkeypatch.setattr(runner, "resolve_target", busy_then_resolved)
    monkeypatch.setattr(runner, "_probe_address", _ok_probe)
    target = TargetRun(target="retry.example", status=TargetStatus.RESOLVING)
    stop_event = asyncio.Event()

    def stop_on_first_sample(_target: TargetRun, sample: ProbeSample | None) -> None:
        if sample is not None:
            stop_event.set()

    # Bounded well below DNS_RETRY_INTERVAL_SECONDS: capacity misses must not
    # start the DNS cooldown.
    await asyncio.wait_for(
        runner.probe_target_loop(
            target,
            args=build_args(count=None, interval=0.1, targets=["retry.example"]),
            mode=ProbeMode.ICMP,
            semaphore=asyncio.Semaphore(1),
            stop_event=stop_event,
            immediate_event=asyncio.Event(),
            initial_delay=0.0,
            on_sample=stop_on_first_sample,
        ),
        timeout=5.0,
    )

    assert attempts == 3
    assert target.status == TargetStatus.HEALTHY
    assert target.resolved_address == "192.0.2.60"


async def test_late_resolution_stays_resolving_until_first_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="late.example",
        status=TargetStatus.DNS_FAILURE,
        error="getaddrinfo timed out after 12s",
    )
    target._last_resolve_time = 0.0

    async def resolved_now(name: str, *_: object, **__: object) -> ResolvedTarget:
        return ResolvedTarget(name, "192.0.2.61", AddressFamily.IPV4, addresses=("192.0.2.61",))

    seen: list[tuple[TargetStatus, str | None]] = []

    async def observing_probe(*_: object, **__: object) -> ProbeSample:
        seen.append((target.status, target.error))
        return await _ok_probe()

    monkeypatch.setattr(runner, "resolve_target", resolved_now)
    monkeypatch.setattr(runner, "_probe_address", observing_probe)

    sample = await runner.probe_once(
        target, args=build_args(), mode=ProbeMode.ICMP, semaphore=asyncio.Semaphore(1)
    )

    assert sample is not None
    assert seen == [(TargetStatus.RESOLVING, None)]
    assert target.status == TargetStatus.HEALTHY


async def test_dns_timeouts_back_off_per_name_and_answers_reset_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = "hang"

    async def scripted_resolver(name: str, *_: object, **__: object) -> ResolvedTarget:
        if outcome == "hang":
            await asyncio.sleep(1)
        if outcome == "answer":
            return ResolvedTarget(name, "192.0.2.62", AddressFamily.IPV4)
        return ResolvedTarget(name, None, None, "getaddrinfo: Temporary failure")

    monkeypatch.setattr(runner, "resolve_target", scripted_resolver)
    monkeypatch.setattr(runner, "DNS_LOOKUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runner.random, "uniform", lambda _low, high: high)

    async def lookup_and_delay(name: str = "blackholed.example") -> float:
        await runner._resolve_target_bounded(name, AddressFamily.AUTO, numeric=False)
        return runner._dns_retry_delay(name)

    jitter = 1.0 + runner.DNS_RETRY_JITTER_FRACTION
    timeouts = [await lookup_and_delay() for _ in range(4)]
    assert timeouts == pytest.approx([10.0 * jitter, 30.0 * jitter, 60.0 * jitter, 60.0 * jitter])

    # A quick failure after timeouts keeps the backoff level; an answer resets it.
    outcome = "fail"
    assert await lookup_and_delay() == pytest.approx(60.0 * jitter)
    outcome = "answer"
    assert await lookup_and_delay() == runner.DNS_RETRY_INTERVAL_SECONDS

    # A transient failure that never timed out keeps the base interval.
    outcome = "fail"
    assert await lookup_and_delay("transient.example") == pytest.approx(10.0 * jitter)


async def test_slow_dns_failures_back_off_like_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # glibc returns EAI_AGAIN once its own nameserver timeouts run out, often
    # inside the lookup budget; a blackholed name must still back off.
    async def slow_failure(name: str, *_: object, **__: object) -> ResolvedTarget:
        await asyncio.sleep(0.03)
        return ResolvedTarget(name, None, None, "getaddrinfo: Temporary failure")

    async def quick_failure(name: str, *_: object, **__: object) -> ResolvedTarget:
        return ResolvedTarget(name, None, None, "getaddrinfo: Name or service not known")

    monkeypatch.setattr(runner, "DNS_SLOW_FAILURE_SECONDS", 0.02)
    monkeypatch.setattr(runner.random, "uniform", lambda _low, _high: 0.0)

    monkeypatch.setattr(runner, "resolve_target", slow_failure)
    slow_delays = []
    for _ in range(3):
        await runner._resolve_target_bounded("slow.example", AddressFamily.AUTO, numeric=False)
        slow_delays.append(runner._dns_retry_delay("slow.example"))

    monkeypatch.setattr(runner, "resolve_target", quick_failure)
    quick_delays = []
    for _ in range(3):
        await runner._resolve_target_bounded("quick.example", AddressFamily.AUTO, numeric=False)
        quick_delays.append(runner._dns_retry_delay("quick.example"))

    assert slow_delays == list(runner.DNS_TIMEOUT_BACKOFF_SECONDS)
    assert quick_delays == [runner.DNS_RETRY_INTERVAL_SECONDS] * 3


async def test_dns_retry_jitter_spreads_names_that_failed_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_resolver(name: str, *_: object, **__: object) -> ResolvedTarget:
        return ResolvedTarget(name, None, None, "getaddrinfo: Name or service not known")

    monkeypatch.setattr(runner, "resolve_target", failing_resolver)
    names = [f"missing-{index}.example" for index in range(20)]

    for name in names:
        await runner._resolve_target_bounded(name, AddressFamily.AUTO, numeric=False)
    delays = {runner._dns_retry_delay(name) for name in names}

    base = runner.DNS_RETRY_INTERVAL_SECONDS
    assert all(base <= delay <= base * (1 + runner.DNS_RETRY_JITTER_FRACTION) for delay in delays)
    assert len(delays) > 1


async def test_probe_once_waits_out_the_names_backoff_before_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookups = 0

    async def still_failing(name: str, *_: object, **__: object) -> ResolvedTarget:
        nonlocal lookups
        lookups += 1
        return ResolvedTarget(name, None, None, "getaddrinfo: Temporary failure")

    monkeypatch.setattr(runner, "resolve_target", still_failing)
    target = TargetRun(target="backoff.example", status=TargetStatus.DNS_FAILURE)
    target._last_resolve_time = 100.0
    runner._get_dns_retries()["backoff.example"] = runner._DnsRetry(timeouts=2, delay_s=30.0)
    args = build_args(targets=["backoff.example"])

    monkeypatch.setattr(runner, "_monotonic_time", lambda: 120.0)
    await runner.probe_once(target, args=args, mode=ProbeMode.ICMP, semaphore=asyncio.Semaphore(1))
    assert lookups == 0

    monkeypatch.setattr(runner, "_monotonic_time", lambda: 131.0)
    await runner.probe_once(target, args=args, mode=ProbeMode.ICMP, semaphore=asyncio.Semaphore(1))
    assert lookups == 1
