from __future__ import annotations

from bankofai.x402_gateway.config.spec import (
    EndpointSpec,
    MeteringDimensionSpec,
    MeteringSpec,
    MeteringVariantSpec,
    SplitSpec,
    TierSpec,
)
from bankofai.x402_gateway.server.metering import resolve_endpoint_price


def _endpoint(metering: MeteringSpec | None = None, path: str = "/x") -> EndpointSpec:
    return EndpointSpec(method="GET", path=path, metering=metering)


def test_free_endpoint_resolves_to_zero() -> None:
    res = resolve_endpoint_price(_endpoint())
    assert res.price_usd == 0.0
    assert res.is_free


def test_single_dimension_single_tier() -> None:
    metering = MeteringSpec(
        dimensions=[
            MeteringDimensionSpec(unit="requests", tiers=[TierSpec(price_usd=0.05)])
        ]
    )
    res = resolve_endpoint_price(_endpoint(metering))
    assert res.price_usd == 0.05
    assert res.splits == []


def test_variant_overrides_dimensions() -> None:
    metering = MeteringSpec(
        dimensions=[
            MeteringDimensionSpec(unit="requests", tiers=[TierSpec(price_usd=0.01)])
        ],
        variants=[
            MeteringVariantSpec(
                param="model",
                value="gpt-4o",
                dimensions=[
                    MeteringDimensionSpec(unit="requests", tiers=[TierSpec(price_usd=0.10)])
                ],
            )
        ],
    )
    base = resolve_endpoint_price(_endpoint(metering))
    matched = resolve_endpoint_price(_endpoint(metering), request_params={"model": "gpt-4o"})
    unmatched = resolve_endpoint_price(_endpoint(metering), request_params={"model": "other"})

    assert base.price_usd == 0.01
    assert matched.price_usd == 0.10
    assert unmatched.price_usd == 0.01


def test_tier_ladder_selects_by_usage() -> None:
    metering = MeteringSpec(
        dimensions=[
            MeteringDimensionSpec(
                unit="requests",
                tiers=[
                    TierSpec(price_usd=0.10, up_to=1000),
                    TierSpec(price_usd=0.02),  # final unbounded
                ],
            )
        ]
    )
    cheap = resolve_endpoint_price(_endpoint(metering), usage=500)
    expensive = resolve_endpoint_price(_endpoint(metering), usage=5000)

    assert cheap.price_usd == 0.10
    assert expensive.price_usd == 0.02


def test_per_tier_splits_take_precedence_over_endpoint_splits() -> None:
    metering = MeteringSpec(
        dimensions=[
            MeteringDimensionSpec(
                unit="requests",
                tiers=[
                    TierSpec(
                        price_usd=0.05,
                        splits=[SplitSpec(recipient="vendor", percent=80)],
                    )
                ],
            )
        ]
    )
    endpoint_splits = [SplitSpec(recipient="other", percent=10)]
    res = resolve_endpoint_price(
        _endpoint(metering), endpoint_level_splits=endpoint_splits
    )
    assert [s.recipient for s in res.splits] == ["vendor"]


def test_endpoint_splits_fallback_when_no_tier_splits() -> None:
    metering = MeteringSpec(
        dimensions=[
            MeteringDimensionSpec(unit="requests", tiers=[TierSpec(price_usd=0.05)])
        ],
    )
    endpoint_splits = [SplitSpec(recipient="fallback", percent=10)]
    res = resolve_endpoint_price(
        _endpoint(metering), endpoint_level_splits=endpoint_splits
    )
    assert [s.recipient for s in res.splits] == ["fallback"]
