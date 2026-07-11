import asyncio
import io
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import pinghue.runner as runner
from pinghue.models import (
    AddressFamily,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    TargetRun,
    TargetStatus,
)
from pinghue.probes import ResolvedTarget


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
        runner.exit_code_for_targets([target], fail_on_any_down=True, fail_on_all_down=False)
        == 3
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
