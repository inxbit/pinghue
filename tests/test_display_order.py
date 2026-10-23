"""Row order for the TUI: header-click sorting and the / search filter."""

from datetime import datetime, timezone

from pinghue.models import ProbeSample, SampleStatus, TargetRun, TargetStatus
from pinghue.ui import column_label, display_order, next_sort


def reply(latency_ms: float) -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc),
        latency_ms=latency_ms,
        status=SampleStatus.OK,
    )


def timeout() -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc),
        latency_ms=None,
        status=SampleStatus.TIMEOUT,
    )


def names(targets: list[TargetRun], order: list[int]) -> list[str]:
    return [targets[index].target for index in order]


def test_host_sort_orders_ipv4_literals_by_octet_value() -> None:
    targets = [
        TargetRun("10.1.10.10"),
        TargetRun("10.1.9.9"),
        TargetRun("10.1.1.11"),
        TargetRun("9.255.255.255"),
        TargetRun("10.1.1.2"),
    ]

    order = display_order(targets, sort_column="host", descending=False, query="")

    assert names(targets, order) == [
        "9.255.255.255",
        "10.1.1.2",
        "10.1.1.11",
        "10.1.9.9",
        "10.1.10.10",
    ]


def test_host_sort_puts_ipv4_then_ipv6_then_names_in_natural_order() -> None:
    targets = [
        TargetRun("host-10.example"),
        TargetRun("2001:db8::10"),
        TargetRun("Host-2.example"),
        TargetRun("10.0.0.1"),
        TargetRun("2001:db8::9"),
    ]

    order = display_order(targets, sort_column="host", descending=False, query="")

    assert names(targets, order) == [
        "10.0.0.1",
        "2001:db8::9",
        "2001:db8::10",
        "Host-2.example",
        "host-10.example",
    ]


def test_second_direction_reverses_the_host_order() -> None:
    targets = [TargetRun("10.1.9.9"), TargetRun("10.1.10.10"), TargetRun("10.1.1.2")]

    order = display_order(targets, sort_column="host", descending=True, query="")

    assert names(targets, order) == ["10.1.10.10", "10.1.9.9", "10.1.1.2"]


def test_address_sort_uses_the_resolved_address_and_keeps_unresolved_hosts_last() -> None:
    targets = [
        TargetRun("pending.example"),
        TargetRun("b.example", resolved_address="10.0.0.10"),
        TargetRun("a.example", resolved_address="10.0.0.9"),
        TargetRun("gone.example"),
        TargetRun("c.example", resolved_address="9.0.0.1"),
    ]

    ascending = display_order(targets, sort_column="address", descending=False, query="")
    descending = display_order(targets, sort_column="address", descending=True, query="")

    assert names(targets, ascending) == [
        "c.example",
        "a.example",
        "b.example",
        "pending.example",
        "gone.example",
    ]
    assert names(targets, descending) == [
        "b.example",
        "a.example",
        "c.example",
        "pending.example",
        "gone.example",
    ]


def test_state_sort_puts_problems_first_then_reverses_to_healthy_first() -> None:
    targets = [
        TargetRun("healthy.example", resolved_address="10.0.0.1", status=TargetStatus.HEALTHY),
        TargetRun("flaky.example", resolved_address="10.0.0.2", status=TargetStatus.INTERMITTENT),
        TargetRun("pending.example", resolved_address="10.0.0.3", status=TargetStatus.RESOLVING),
        TargetRun("nxdomain.example", status=TargetStatus.DNS_FAILURE),
        TargetRun("down.example", resolved_address="10.0.0.5", status=TargetStatus.DOWN),
        TargetRun(
            "denied.example", resolved_address="10.0.0.6", status=TargetStatus.PERMISSION_DENIED
        ),
        TargetRun("broken.example", resolved_address="10.0.0.7", status=TargetStatus.ERROR),
    ]

    worst_first = display_order(targets, sort_column="state", descending=True, query="")
    best_first = display_order(targets, sort_column="state", descending=False, query="")

    assert names(targets, worst_first) == [
        "down.example",
        "nxdomain.example",
        "denied.example",
        "broken.example",
        "flaky.example",
        "pending.example",
        "healthy.example",
    ]
    assert names(targets, best_first) == names(targets, worst_first)[::-1]


def test_loss_sort_puts_full_loss_first_then_no_loss_first_and_unprobed_hosts_last() -> None:
    targets = [
        TargetRun("half.example", samples=[reply(5.0), timeout()]),
        TargetRun("unprobed.example"),
        TargetRun("clean.example", samples=[reply(5.0), reply(6.0)]),
        TargetRun("dead.example", samples=[timeout(), timeout()]),
    ]

    worst_first = display_order(targets, sort_column="loss", descending=True, query="")
    best_first = display_order(targets, sort_column="loss", descending=False, query="")

    assert names(targets, worst_first) == [
        "dead.example",
        "half.example",
        "clean.example",
        "unprobed.example",
    ]
    assert names(targets, best_first) == [
        "clean.example",
        "half.example",
        "dead.example",
        "unprobed.example",
    ]


def test_latency_sorts_put_the_slowest_first_and_hosts_without_replies_last() -> None:
    targets = [
        TargetRun("fast.example", samples=[reply(2.0), reply(4.0)]),
        TargetRun("dead.example", samples=[timeout()]),
        TargetRun("slow.example", samples=[reply(80.0), reply(120.0)]),
        TargetRun("spiky.example", samples=[reply(1.0), reply(300.0), reply(2.0)]),
    ]

    def slowest_first(column: str) -> list[str]:
        return names(targets, display_order(targets, sort_column=column, descending=True, query=""))

    assert slowest_first("last") == [
        "slow.example",
        "fast.example",
        "spiky.example",
        "dead.example",
    ]
    assert slowest_first("min") == ["slow.example", "fast.example", "spiky.example", "dead.example"]
    assert slowest_first("avg") == ["spiky.example", "slow.example", "fast.example", "dead.example"]
    assert slowest_first("max") == ["spiky.example", "slow.example", "fast.example", "dead.example"]
    assert slowest_first("jitter")[0] == "spiky.example"
    assert slowest_first("jitter")[-1] == "dead.example"


FLEET = [
    TargetRun("10.0.0.5", resolved_address="10.0.0.5"),
    TargetRun("10.0.0.50", resolved_address="10.0.0.50"),
    TargetRun("db-1.example", resolved_address="10.0.0.5"),
    TargetRun("DB-2.example", resolved_address="10.0.1.7"),
    TargetRun("web.example", resolved_address="192.0.2.80"),
    TargetRun("v6.example", resolved_address="2001:db8::1"),
]


def search(query: str, *, sort_column: str | None = None, descending: bool = False) -> list[str]:
    order = display_order(FLEET, sort_column=sort_column, descending=descending, query=query)
    return names(FLEET, order)


def test_search_matches_part_of_a_host_name_or_address_ignoring_case() -> None:
    assert search("db") == ["db-1.example", "DB-2.example", "v6.example"]
    assert search("10.0.1") == ["DB-2.example"]
    assert search("WEB") == ["web.example"]


def test_search_for_a_full_ip_address_matches_only_that_address() -> None:
    assert search("10.0.0.5") == ["10.0.0.5", "db-1.example"]
    assert search("2001:db8:0::1") == ["v6.example"]


def test_search_with_several_terms_shows_hosts_matching_any_of_them() -> None:
    assert search("10.0.0.50 web") == ["10.0.0.50", "web.example"]
    assert search("10.0.0.50, 192.0.2.80") == ["10.0.0.50", "web.example"]


def test_an_empty_search_shows_every_host_and_no_match_shows_none() -> None:
    assert search("  ") == [target.target for target in FLEET]
    assert search("nothing-here") == []


def test_search_results_follow_the_sort_order() -> None:
    # Equal addresses keep their input order in either direction.
    assert search("10.0", sort_column="address", descending=True) == [
        "DB-2.example",
        "10.0.0.50",
        "10.0.0.5",
        "db-1.example",
    ]


def test_header_clicks_cycle_through_both_directions_then_back_to_input_order() -> None:
    clicks = [("loss", True), ("loss", False), (None, False)]
    sort: tuple[str | None, bool] = (None, False)
    for expected in clicks:
        sort = next_sort("loss", *sort)
        assert sort == expected

    assert next_sort("host", None, False) == ("host", False)
    assert next_sort("host", "host", False) == ("host", True)
    # Another column starts over in its own first direction.
    assert next_sort("address", "loss", True) == ("address", False)


def test_clicks_on_columns_that_cannot_be_sorted_keep_the_sort() -> None:
    assert next_sort("mode", "avg", True) == ("avg", True)
    assert next_sort("history", None, False) == (None, False)


def test_the_sorted_column_header_carries_an_arrow_that_fits_its_width() -> None:
    assert column_label("host", "host", False) == "host ▲"
    assert column_label("loss", "loss", True) == "loss ▼"
    # jitter is 7 cells wide at its narrowest: no room for the space.
    assert column_label("jitter", "jitter", True) == "jitter▼"
    assert column_label("avg", "loss", True) == "avg"
