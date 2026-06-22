"""In-process gateway metrics exposed in Prometheus text format."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Iterable

Labels = tuple[tuple[str, str], ...]


def _labels(values: dict[str, str | int | float | None]) -> Labels:
    return tuple(sorted((key, str(value)) for key, value in values.items() if value is not None))


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: Labels) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{_escape_label(value)}"' for key, value in labels)
    return "{" + body + "}"


@dataclass
class MetricsStore:
    counters: dict[tuple[str, Labels], float] = field(default_factory=dict)
    histograms: dict[tuple[str, Labels], list[float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def inc(
        self,
        name: str,
        *,
        amount: float = 1.0,
        **labels: str | int | float | None,
    ) -> None:
        key = (name, _labels(labels))
        with self._lock:
            self.counters[key] = self.counters.get(key, 0.0) + amount

    def observe(self, name: str, value: float, **labels: str | int | float | None) -> None:
        key = (name, _labels(labels))
        with self._lock:
            self.histograms.setdefault(key, []).append(value)

    def record_http_request(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        self.inc(
            "x402_gateway_http_requests_total",
            method=method,
            path=path,
            status_code=str(status_code),
        )
        self.observe(
            "x402_gateway_http_request_duration_seconds",
            duration_seconds,
            method=method,
            path=path,
            status_code=str(status_code),
        )

    def to_prometheus(self) -> str:
        with self._lock:
            counters = dict(self.counters)
            histograms = {key: list(values) for key, values in self.histograms.items()}

        lines = [
            "# HELP x402_gateway_http_requests_total Total HTTP requests handled by the gateway.",
            "# TYPE x402_gateway_http_requests_total counter",
        ]
        lines.extend(_format_counter_series(counters, "x402_gateway_http_requests_total"))
        lines.extend(
            [
                "# HELP x402_gateway_http_request_duration_seconds HTTP request duration.",
                "# TYPE x402_gateway_http_request_duration_seconds summary",
            ]
        )
        lines.extend(
            _format_summary_series(histograms, "x402_gateway_http_request_duration_seconds")
        )

        business_counters = [
            "x402_gateway_payment_challenges_total",
            "x402_gateway_payment_verify_total",
            "x402_gateway_payment_settle_total",
            "x402_gateway_upstream_requests_total",
        ]
        for name in business_counters:
            lines.append(f"# TYPE {name} counter")
            lines.extend(_format_counter_series(counters, name))

        return "\n".join(lines) + "\n"


def _format_counter_series(
    counters: dict[tuple[str, Labels], float],
    name: str,
) -> Iterable[str]:
    for (metric_name, labels), value in sorted(counters.items()):
        if metric_name == name:
            yield f"{metric_name}{_format_labels(labels)} {value:g}"


def _format_summary_series(
    histograms: dict[tuple[str, Labels], list[float]],
    name: str,
) -> Iterable[str]:
    for (metric_name, labels), values in sorted(histograms.items()):
        if metric_name != name:
            continue
        count = len(values)
        total = sum(values)
        yield f"{metric_name}_count{_format_labels(labels)} {count}"
        yield f"{metric_name}_sum{_format_labels(labels)} {total:g}"
