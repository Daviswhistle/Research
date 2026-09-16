# Historical Corporate-Bond Market Evidence

## 목적

Distressed-equity 연구에서 주가 폭락만 보는 대신 **당시 채권시장이 같은 기업의 생존/구조조정 위험을 어떻게 가격했는지** 복원한다.

다만 채권 가격이나 yield를 그대로 "부도확률"이라고 부르지 않는다. 가격에는 회수율 기대가, spread에는 부도위험 외에도 유동성·risk premium 등이 섞인다.

따라서 저장 계층을 분리한다.

```text
source-verified stable debt identity (CUSIP / ISIN)
    -> historical bond market observation
       price (% par), yield, volume, date
    -> explicit benchmark or freshness-checked Treasury benchmark
    -> credit spread
    -> recovery-sensitive constant-hazard stress proxy
```

## 가장 중요한 identity 원칙

Bond market data는 **검증된 debt ledger의 explicit CUSIP/ISIN에만** 연결한다.

- issuer name fuzzy match 금지
- note name similarity match 금지
- maturity/coupon similarity만으로 market series 연결 금지
- CUSIP/ISIN가 없으면 unresolved로 남김
- provider가 반환한 observation은 요청한 CUSIP/ISIN 중 최소 하나와 exact match해야 함
- 반환 row에 명시된 다른 CUSIP/ISIN가 요청 identity와 충돌하면 hard fail

즉 built-in provider뿐 아니라 `HistoricalBondMarketProvider`를 구현한 외부 provider도 잘못된 security row를 stable ID에 붙일 수 없다.

Workspace에서는 bond-market 실행 시 단순 수동 debt JSON fallback을 허용하지 않는다. frozen `debt_instrument_source_packet`과 verifier가 재오픈한 snapshot verification이 필요하며, snapshot 승인은 다음 두 fingerprint에 묶인다.

```text
frozen source-packet fingerprint
exact finalized snapshot-row fingerprint
```

승인 후 principal, maturity, `as_of_date`, identifier 또는 cited evidence가 바뀌면 기존 승인은 무효다.

## Provider contract

`bond_market.py`의 `HistoricalBondMarketProvider`는 다음 두 작업만 요구한다.

```text
observations(cusip, isin, start, end)
benchmark_on_or_before(as_of, remaining_years)
```

공통 관측치:

```text
BondMarketObservation
  date
  cusip / isin
  price_pct_par
  yield_pct
  volume
  benchmark_yield_pct   # optional
  spread_bps            # optional
  source
```

Price는 par=100 기준이다. Yield/benchmark는 percentage point 단위다.

## Provider 1: normalized CSV

`CsvBondMarketProvider`는 FINRA TRACE, WRDS 또는 다른 vendor export를 작은 명시적 schema로 normalize한 결과를 읽는다.

필수:

```text
date
cusip 또는 isin
```

각 row에는 최소 하나가 필요하다.

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

### TRACE / WRDS를 CSV 우선 경로로 둔 이유

FINRA TRACE는 transaction-level corporate-bond execution price/yield/volume의 권위 있는 원천이다. 하지만 Enhanced Historical data는 별도 agreement/fee와 지연 구간이 있고, WRDS TRACE 역시 기관 라이선스 환경을 전제로 한다.

따라서 repository는 FINRA/WRDS credential이나 원본 layout에 종속되지 않고, 사용 권한을 가진 export를 명시적 schema로 normalize해서 받는다.

원본 TRACE의 거래가 여러 건이면 사용자는 목적에 따라 daily VWAP, 마지막 disseminated trade 등 aggregation policy를 먼저 정해야 한다. Repository는 raw transaction rows를 임의로 closing price로 만들지 않는다.

동일 채권·동일 날짜에 서로 다른 observation이 둘 이상 들어오면 hard fail한다. Exact duplicate만 collapse하며, transaction-level input의 daily aggregation은 upstream에서 명시적으로 수행해야 한다.

## Provider 2: EODHD

`EodhdBondProvider`는 single-company deep dive용 targeted provider다.

- explicit CUSIP/ISIN로 US corporate-bond EOD history 조회
- price / yield / volume
- `/ust/yield-rates`로 US Treasury nominal par curve 조회
- bond observation date를 넘지 않는 최신 curve에서 remaining maturity와 가장 가까운 tenor 선택
- per-bond API이므로 `bulk_safe=False`

환경변수:

```text
EODHD_API_KEY
```

EODHD bond adapter는 `.BOND` compatibility surface를 **strict하게** 다룬다.

- stock-like `close`를 bond price로 reinterpret하지 않음
- in-range row는 모두 explicit `price` + `yield` shape를 만족해야 함
- valid row와 generic OHLC row가 섞인 partial response도 전체 거부
- ISIN route가 incompatible response를 내고 CUSIP fallback이 empty여도 compatibility error를 숨기지 않음

이 adapter는 대규모 historical universe replay용이 아니다. 그 용도는 TRACE/WRDS 같은 bulk export가 우선이다.

## Credit spread

우선순위:

```text
1. provider가 명시적으로 준 spread_bps
2. yield_pct - observation의 benchmark_yield_pct
3. yield_pct - tenor-matched Treasury benchmark
4. 없으면 unresolved
```

Benchmark maturity가 year-only precision이면 그 사실을 warning으로 남긴다. 만기 자체가 없으면 Treasury tenor를 임의로 고르지 않는다.

### Treasury benchmark freshness

외부 Treasury curve는 미래값만 막는 것으로 충분하지 않다. 너무 오래된 과거 curve도 spread를 크게 왜곡할 수 있다.

따라서 assessment는 `benchmark_days_stale`를 기록하고, 기본적으로 Treasury benchmark가 bond observation보다 **7일 초과** 오래됐으면:

```text
raw benchmark evidence: 보존
spread_bps: unresolved
implied_credit_risk: 계산하지 않음
warning: stale benchmark 명시
```

기본값은 CLI에서 조절할 수 있다.

```text
standalone: --max-benchmark-staleness-days
workspace:  --bond-max-benchmark-staleness-days
```

Observation 자체가 explicit `benchmark_yield_pct` 또는 provider `spread_bps`를 갖고 있으면 그 값을 우선하므로 외부 Treasury freshness gate가 필요하지 않다.

## Spread-implied credit-risk proxy

Repository는 spread에서 물리적(real-world) default probability를 추정한다고 주장하지 않는다.

다음 reduced-form 근사를 사용한다.

```math
spread \approx hazard \times (1 - recovery)
```

따라서:

```math
hazard = max(spread, 0) / (1 - recovery)
```

horizon `T`의 cumulative probability proxy:

```math
PD_Q(T) = 1 - exp(-hazard * T)
```

기본 recovery scenario:

```text
20%
40%
60%
```

기본 horizon:

```text
1 year
```

결과 필드 `implied_credit_risk`는 **risk-neutral stress equivalent**다. 이것이 실제 부도 빈도 예측치가 아닌 이유:

- corporate spread에는 liquidity premium이 포함됨
- risk aversion / risk premium 포함
- recovery 가정에 민감함
- flat hazard 가정
- callable/convertible/structural option이 yield에 영향을 줄 수 있음
- distressed bond에서는 conventional YTM 자체의 경제적 해석이 약해질 수 있음

따라서 이 값은 "시장이 얼마나 심하게 pricing하고 있었는가"의 비교 지표로 쓰고, 기존 base-rate / survival 모델과 별도로 둔다.

## 가격 스트레스

가격은 확률로 변환하지 않고 그대로 보존한다.

현재 deterministic signal:

```text
price_below_80_pct_par
price_below_60_pct_par
price_below_40_pct_par
spread_at_or_above_1000_bps
spread_at_or_above_2000_bps
stale_bond_market_observation
```

또한 lookback window 내:

- peak price (% par)
- peak 대비 price drawdown
- 현재 yield - lookback 최저 yield
- observation count
- cutoff 대비 bond observation staleness days
- bond observation 대비 benchmark staleness days

을 기록한다.

## Stale trade boundary

회사채는 매일 거래되지 않을 수 있다.

따라서 `price_on_or_before`처럼 마지막 값을 조용히 cutoff 값으로 취급하지 않고 `days_stale`를 기록한다. 기본 30일보다 오래됐으면 signal/warning을 붙인다.

Stale observation은 삭제하지 않는다. 실제 유동성 부족 자체가 투자적으로 중요한 정보일 수 있기 때문이다. 대신 workspace summary에서 다음을 분리한다.

```text
lookback 안에 observation이 있는 채권
fresh observation으로 현재 stress를 셀 수 있는 채권
stale observation이라 현재 stress headline에서 제외된 채권
```

따라서 months-old trade가 50 cents on par였다는 이유만으로 cutoff 현재의 `<80% par` headline count에 들어가지 않는다.

## Standalone CLI

Stable ledger 파일을 이미 만들었다면:

```bash
distressed-equity-bond-market \
  --ledger workspace/debt_instrument_ledger.json \
  --analysis-date 2022-12-31 \
  --provider csv \
  --bond-observations-csv trace_normalized.csv \
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

`distressed-equity-research`에서는 bond provider가 반드시 `--debt-instruments`와 함께 와야 하며, bond-market path에서는 frozen debt source packet과 fingerprint-bound verified snapshots가 필요하다.

```bash
distressed-equity-research \
  --ticker TEST \
  --analysis-date 2022-12-31 \
  --workspace ./work/TEST-2022-12-31 \
  --debt-instruments ./verified_debt.json \
  --bond-market-provider csv \
  --bond-observations-csv ./trace_normalized.csv \
  --bond-max-staleness-days 30 \
  --bond-max-benchmark-staleness-days 7
```

생성:

```text
debt_instrument_ledger.json
bond_market.json
summary.md
```

Debt ledger를 다시 만들면 과거 stable-ID basis에 의존하는 derived artifact를 자동 무효화한다.

```text
debt_lineage.json
debt_lineage_verification_template.json
bond_market.json
merged.json
```

새 stable IDs에 과거 lineage/market/merged 결과가 잘못 붙어 남는 것을 막기 위해서다.

## 투자 연구에서의 사용법

이 layer의 목적은 하나의 확률 숫자를 만드는 것이 아니다.

예를 들어 2022년 말 어떤 기업에서:

```text
equity: -95% drawdown
senior unsecured bond: 42 cents on par
yield: 30%
Treasury-matched spread: 2,500 bps
recovery=20/40/60%에 따라 hazard proxy가 크게 변함
```

이라면 다음 질문을 분리할 수 있다.

1. 시장은 common equity만 망가진다고 봤나, 채권까지 심각한 손실을 가격했나?
2. 가장 안전한 채권도 distressed였나, junior claim만 distressed였나?
3. debt price가 암시하는 recovery expectation과 우리가 계산한 liquidation/reorganization recovery가 얼마나 다른가?
4. equity Required Probability와 bond-market stress가 동시에 과도했는가?
5. 시간이 지나며 bond spread가 equity보다 먼저 정상화했는가?

Carvana형 발굴에서는 특히 **"equity가 실패를 가격한다"와 "credit market도 같은 실패를 얼마나 강하게 가격한다"를 교차검증**하는 역할을 한다.

## 남은 확장

- raw TRACE transaction ingestion + configurable daily aggregation
- TRACE/WRDS security master 자동 normalization
- bid/ask 또는 evaluated-price source
- CDS curve가 있을 때 bond spread와 분리 비교
- structural/reduced-form calibration을 이용한 richer market-implied probability distribution
- CRSP/TRACE-scale historical distress population replay
