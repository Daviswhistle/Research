# Historical Corporate-Bond Market Evidence

## 목적

Distressed-equity 연구에서 주가 폭락만 보는 대신 **당시 채권시장이 같은 기업의 생존/구조조정 위험을 어떻게 가격했는지** 복원한다.

채권 가격이나 yield를 그대로 "부도확률"이라고 부르지 않는다. 가격에는 recovery expectation이, spread에는 신용위험 외에도 liquidity/risk premium 등이 섞인다.

```text
source-verified stable debt identity (CUSIP / ISIN)
    -> corrected historical bond observation
       price (% par), yield, volume, date
    -> explicit benchmark or freshness-checked Treasury benchmark
    -> credit spread
    -> recovery-sensitive constant-hazard stress proxy
```

## Identity 원칙

Bond market data는 **검증된 debt ledger의 explicit CUSIP/ISIN에만** 연결한다.

- issuer/name fuzzy match 금지
- coupon/maturity-only market-series matching 금지
- CUSIP/ISIN가 없으면 unresolved
- provider observation은 요청한 CUSIP/ISIN 중 최소 하나와 exact match해야 함
- 반환 row의 explicit identifier가 요청 identity와 충돌하면 hard fail

Workspace bond-market path는 frozen `debt_instrument_source_packet`과 fingerprint-bound verified snapshots를 요구한다. Snapshot 승인은 **finalized row fingerprint + frozen source-packet fingerprint**에 묶이므로 승인 후 principal, maturity, `as_of_date`, identifier 또는 cited evidence가 바뀌면 무효다.

## Provider contract

`HistoricalBondMarketProvider`:

```text
observations(cusip, isin, start, end)
benchmark_on_or_before(as_of, remaining_years)
```

`BondMarketObservation`:

```text
date
cusip / isin
price_pct_par
 yield_pct
volume
benchmark_yield_pct   # optional
spread_bps            # optional
source
```

Price는 par=100, yield/benchmark는 percentage point 단위다.

## Provider 1: normalized CSV

`CsvBondMarketProvider`는 TRACE/WRDS/vendor data를 작은 명시적 schema로 읽는다.

필수:

```text
date
cusip 또는 isin
```

각 row에는 최소 하나:

```text
price_pct_par  # alias price
yield_pct      # alias yield
spread_bps
```

선택:

```text
volume
benchmark_yield_pct
source
```

예:

```csv
date,cusip,price_pct_par,yield_pct,volume,benchmark_yield_pct,source
2022-12-29,14167LAG2,42.5,28.1,1200000,4.10,TRACE normalized
```

동일 채권·동일 날짜에 서로 다른 observation이 둘 이상 있으면 hard fail한다. Exact duplicate만 collapse한다. 즉 transaction-level data를 repository가 임의로 "daily close"로 만들지 않는다.

## Raw FINRA TRACE → normalized daily CSV

Raw FINRA Enhanced Historical transaction file을 직접 정리하는 별도 단계가 있다.

```bash
distressed-equity-trace-normalize \
  enhanced-time-and-sales-cusip-2022-12.txt \
  enhanced-time-and-sales-cusip-2023-01.txt \
  --aggregation vwap \
  --output trace_daily.csv \
  --metadata-output trace_daily.meta.json
```

그 결과는 바로 CSV provider에 연결한다.

```bash
distressed-equity-bond-market \
  --ledger workspace/debt_instrument_ledger.json \
  --analysis-date 2022-12-31 \
  --provider csv \
  --bond-observations-csv trace_daily.csv \
  --output workspace/bond_market.json
```

### TRACE 처리 순서

```text
raw reports
  -> parse/validate
  -> exact semantic duplicate collapse
  -> lifecycle resolution
     T/R active
     X/C/Y or As-Of R cancel/reverse prior report
  -> eligibility filter
  -> execution date + CUSIP grouping
  -> explicit VWAP or last-execution policy
  -> normalized daily observation
```

기본 정책은 보수적이다.

- CUSIP-bearing historical file만 허용: TRACE Symbol fuzzy identity를 만들지 않음
- `Dissemination Flag=Y`만 포함: inter-dealer buy/sell two-sided report의 이중계산 위험을 줄임
- `Trade Modifier 4=W` 제외: 이미 weighted-average인 가격을 다시 aggregation하지 않음
- `Special Price Indicator=Y` 제외
- primary-market `P1` 제외
- unresolved cancel/correction/reversal은 기본 hard fail
- overlapping files의 exact semantic duplicate는 한 번만 처리하고 별도 count로 기록
- 같은 report key에 다른 economics가 있으면 hard fail

Daily policy는 명시적으로 선택한다.

```text
vwap  = quantity-weighted price/yield   # default
last  = latest eligible execution price/yield
```

`last`도 공식 FINRA close를 주장하지 않는다. 선택한 연구 policy다. 자세한 lifecycle/field/audit 규칙은 [`TRACE_TRANSACTION_NORMALIZATION.md`](TRACE_TRANSACTION_NORMALIZATION.md)를 참고한다.

FINRA/WRDS credential이나 라이선스를 repository가 우회하지 않는다. 사용 권한을 가진 원본/export를 입력으로 사용하는 구조다.

## Provider 2: EODHD

`EodhdBondProvider`는 single-company deep dive용 targeted provider다.

- explicit CUSIP/ISIN로 EOD bond history 조회
- price / yield / volume
- `/ust/yield-rates` Treasury curve
- observation date를 넘지 않는 latest curve에서 remaining maturity nearest tenor
- per-bond 요청이므로 `bulk_safe=False`

```text
EODHD_API_KEY
```

`.BOND` route는 strict compatibility surface로 취급한다.

- stock-like `close`를 bond price로 reinterpret하지 않음
- in-range row는 모두 explicit `price` + `yield` shape를 만족해야 함
- valid row와 incompatible row가 섞여도 전체 response 거부
- 한 identifier가 incompatible인데 다른 fallback이 empty라고 오류를 숨기지 않음

대규모 historical replay에는 TRACE/WRDS bulk path가 우선이다.

## Credit spread

우선순위:

```text
1. provider spread_bps
2. yield_pct - observation benchmark_yield_pct
3. yield_pct - tenor-matched Treasury benchmark
4. unresolved
```

Maturity가 year-only precision이면 warning을 남긴다. 만기가 없으면 Treasury tenor를 임의 선택하지 않는다.

### Treasury benchmark freshness

Assessment는 `benchmark_days_stale`를 기록한다. 기본적으로 external Treasury curve가 bond observation보다 **7일 초과** 오래됐으면:

```text
raw benchmark: 보존
spread_bps: unresolved
implied_credit_risk: 계산하지 않음
warning: stale benchmark
```

```text
standalone: --max-benchmark-staleness-days
workspace:  --bond-max-benchmark-staleness-days
```

Observation 자체에 `benchmark_yield_pct`나 provider `spread_bps`가 있으면 해당 값을 우선하므로 external benchmark freshness gate는 적용되지 않는다.

## Spread-implied credit-risk proxy

Repository는 spread에서 physical default probability를 추정한다고 주장하지 않는다.

```math
spread \approx hazard \times (1 - recovery)
```

```math
hazard = max(spread, 0) / (1 - recovery)
```

```math
PD_Q(T) = 1 - exp(-hazard \times T)
```

기본 recovery:

```text
20% / 40% / 60%
```

기본 horizon:

```text
1 year
```

`implied_credit_risk`는 **risk-neutral stress equivalent**다. Physical forecast가 아닌 이유:

- liquidity premium
- risk aversion / risk premium
- recovery sensitivity
- flat hazard approximation
- callable/convertible/structural optionality
- distressed YTM의 경제적 취약성

따라서 기존 base-rate/survival probability와 별도로 비교한다.

## 가격·spread stress와 stale trade

가격은 확률로 변환하지 않고 그대로 보존한다.

Signals:

```text
price_below_80_pct_par
price_below_60_pct_par
price_below_40_pct_par
spread_at_or_above_1000_bps
spread_at_or_above_2000_bps
stale_bond_market_observation
```

Lookback에서 다음도 기록한다.

- peak price
- peak 대비 drawdown
- current yield - lookback low yield
- observation count
- cutoff 대비 bond observation staleness
- bond observation 대비 benchmark staleness

회사채는 매일 거래되지 않을 수 있으므로 마지막 값을 cutoff current value로 조용히 취급하지 않는다. 기본 30일보다 오래됐으면 stale signal/warning을 붙인다.

Stale observation은 유동성 evidence로 보존하되 workspace headline current-stress count에서는 제외한다.

```text
all lookback observations
fresh observations eligible for current stress
stale observations excluded from current stress
```

따라서 months-old 50-cent trade가 cutoff 시점 `<80% par` headline count를 만들지 않는다.

## Standalone CLI

```bash
distressed-equity-bond-market \
  --ledger workspace/debt_instrument_ledger.json \
  --analysis-date 2022-12-31 \
  --provider csv \
  --bond-observations-csv trace_daily.csv \
  --max-staleness-days 30 \
  --max-benchmark-staleness-days 7 \
  --output workspace/bond_market.json
```

EODHD:

```bash
EODHD_API_KEY=... distressed-equity-bond-market \
  --ledger workspace/debt_instrument_ledger.json \
  --analysis-date 2022-12-31 \
  --provider eodhd \
  --max-benchmark-staleness-days 7 \
  --output workspace/bond_market.json
```

## Research workspace integration

```bash
distressed-equity-research \
  --ticker TEST \
  --analysis-date 2022-12-31 \
  --workspace ./work/TEST-2022-12-31 \
  --debt-instruments ./verified_debt.json \
  --bond-market-provider csv \
  --bond-observations-csv ./trace_daily.csv \
  --bond-max-staleness-days 30 \
  --bond-max-benchmark-staleness-days 7
```

생성:

```text
debt_instrument_ledger.json
bond_market.json
summary.md
```

Debt ledger rebuild는 과거 stable-ID basis에 의존하는 derived artifact를 자동 무효화한다.

```text
debt_lineage.json
debt_lineage_verification_template.json
bond_market.json
merged.json
```

## 투자 연구에서의 사용법

이 layer의 목적은 하나의 확률 숫자를 만드는 것이 아니다.

예를 들어 분석일에:

```text
equity: -95% drawdown
senior unsecured bond: 42 cents on par
yield: 30%
Treasury-matched spread: 2,500 bps
```

라면 다음 질문을 분리한다.

1. common equity만 망가진다고 봤나, credit도 심각한 loss/recovery stress를 가격했나?
2. 가장 안전한 claim도 distressed였나, junior claim만 distressed였나?
3. bond recovery expectation과 liquidation/reorganization recovery model이 얼마나 다른가?
4. equity Required Probability와 credit-market stress가 동시에 과도했는가?
5. 정상화 국면에서 bond spread가 equity보다 먼저 움직였는가?

Carvana형 발굴에서는 **equity가 실패를 가격하는 정도와 credit market이 같은 실패를 가격하는 정도를 서로 교차검증**한다.

## 남은 확장

- TRACE Symbol-only/non-CUSIP historical file용 source-backed security master mapping
- vendor/WRDS layout adapter와 대규모 batch normalization
- bid/ask 또는 evaluated-price source
- CDS curve와 bond spread 분리 비교
- richer recovery/default calibration
- CRSP/TRACE-scale historical distress population replay
