from __future__ import annotations

from dataclasses import asdict, dataclass
import math


_Z_95 = 1.959963984540054


@dataclass(frozen=True)
class BinomialRateDiagnostic:
    resolved_n: int
    successes: int | None
    rate: float | None
    wilson_95_low: float | None
    wilson_95_high: float | None
    small_sample: bool
    warning: str | None


def wilson_interval(successes: int, total: int, *, z: float = _Z_95) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("Wilson interval total must be positive")
    if successes < 0 or successes > total:
        raise ValueError("Wilson interval successes must be in [0, total]")
    if not math.isfinite(z) or z <= 0:
        raise ValueError("Wilson interval z must be positive and finite")
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denominator
    half = (
        z
        * math.sqrt((p * (1.0 - p) / total) + (z2 / (4.0 * total * total)))
        / denominator
    )
    return max(0.0, center - half), min(1.0, center + half)


def binomial_rate_diagnostic(
    rate: float | None,
    resolved_n: int,
    *,
    small_sample_n: int = 30,
) -> BinomialRateDiagnostic:
    if resolved_n < 0:
        raise ValueError("resolved_n cannot be negative")
    if small_sample_n <= 0:
        raise ValueError("small_sample_n must be positive")
    if rate is None:
        if resolved_n != 0:
            raise ValueError("rate cannot be None when resolved_n is positive")
        return BinomialRateDiagnostic(
            resolved_n=0,
            successes=None,
            rate=None,
            wilson_95_low=None,
            wilson_95_high=None,
            small_sample=True,
            warning="no resolved observations; rate is unavailable",
        )
    numeric = float(rate)
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        raise ValueError("binomial rate must be finite and in [0, 1]")
    if resolved_n == 0:
        raise ValueError("resolved_n must be positive when rate is available")
    raw_successes = numeric * resolved_n
    successes = int(round(raw_successes))
    if abs(raw_successes - successes) > 1e-9:
        raise ValueError(
            "rate/resolved_n pair is not compatible with an integer binomial success count"
        )
    low, high = wilson_interval(successes, resolved_n)
    small = resolved_n < small_sample_n
    warning = None
    if small:
        warning = (
            f"resolved_n={resolved_n} is below the descriptive small-sample threshold "
            f"{small_sample_n}; do not treat the point estimate as a precise probability"
        )
    return BinomialRateDiagnostic(
        resolved_n=resolved_n,
        successes=successes,
        rate=numeric,
        wilson_95_low=low,
        wilson_95_high=high,
        small_sample=small,
        warning=warning,
    )


def diagnostic_to_dict(value: BinomialRateDiagnostic) -> dict[str, object]:
    return asdict(value)
