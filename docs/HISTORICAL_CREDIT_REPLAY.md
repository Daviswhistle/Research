# Historical Equity + Credit Distress Replay

## 목적

단일 기업에서 당시 채권 가격을 복원하는 것과, **과거 distress population 전체에서 equity와 credit이 동시에 무엇을 가격했는지 재생하는 것**은 다른 문제다.

이 layer는 다음 질문을 위해 존재한다.

> 주가가 이미 크게 붕괴한 기업 중에서, 당시 corporate credit도 실제로 심각한 distress를 가격한 사례는 얼마나 많았는가?

그 다음 단계에서 실제 생존/기존 보통주 생존/정상화/3년 수익률 outcome을 붙여 base rate를 만들 수 있다.

현재 구현은 outcome을 아직 추정하지 않는다. 먼저 **point-in-time candidate cohort와 contemporaneous credit evidence를 정확하게 만드는 단계**다.

## 전체 구조

```text
point-in-time equity universe
    -> adjusted-price distress replay
    -> equity distress seeds

point-in-time security_id -> CUSIP/ISIN links
    + normalized daily corporate-bond observations
    -> contemporaneous credit evidence

combine
    -> joint equity / credit distress cohort
```

핵심은 join key다.

```text
permanent equity security_id
    -> point-in-time debt identifier membership
       CUSIP / ISIN
```

Ticker, issuer name, bond name, coupon, maturity similarity로 fuzzy join하지 않는다.

## 입력 1: equity security master

기존 `CsvMarketProvider` schema를 사용한다.

필수:

```text
security_id
symbol
name
```

Survivorship-safe replay에는 다음이 필요하다.

```text
start_date
end_date
```

`security_id`는 가능하면 CRSP PERMNO 같은 permanent security identity여야 한다. Ticker는 display/routing 값이지 역사적 join key가 아니다.

## 입력 2: equity price history

기존 normalized CSV schema:

```text
security_id
date
close
adjusted_close
```

Adjusted history로 prior peak 대비 drawdown을 계산한다. 이 값은 **distress candidate generator**이고 peak market cap / EV distress gate를 대체하지 않는다.

## 입력 3: point-in-time equity ↔ debt links

`--credit-links-csv` schema:

```text
security_id
cusip 또는 isin
start_date
end_date
source   # optional
```

예:

```csv
security_id,cusip,isin,start_date,end_date,source
12345,14167LAG2,,2021-10-01,2024-05-31,WRDS/TRACE security link
```

`start_date`와 `end_date`는 필수 컬럼이다. `end_date` 값 자체는 blank일 수 있다.

이 강제 조건의 이유는 단순하다.

현재 알고 있는 issuer-bond mapping을 2022년으로 소급하면 survivorship / look-ahead 오류가 생길 수 있다. 따라서 cutoff에 실제로 active였던 identifier membership만 사용한다.

## 입력 4: daily bond observations

`CsvBondMarketProvider`가 읽는 normalized daily schema를 사용한다.

```text
date
cusip 또는 isin
price_pct_par
yield_pct
benchmark_yield_pct   # optional
spread_bps            # optional
volume                 # optional
source                 # optional
```

Raw TRACE transaction은 먼저 `distressed-equity-trace-normalize`로 정규화해야 한다. 같은 채권/날짜에 서로 다른 transaction-level row를 그대로 넣으면 joint replay는 hard fail한다.

## Equity distress candidate

기존 `run_historical_replay()`를 그대로 사용한다.

기본:

```text
adjusted drawdown >= 60%
lookback = 5 years
raw price >= $0.10
point-in-time universe membership required
```

이는 equity side 후보 생성이다.

## Credit evidence

각 equity distress seed의 cutoff에서 active한 CUSIP/ISIN link를 찾고, 해당 채권의 configured lookback 내 마지막 daily observation을 가져온다.

기본:

```text
credit lookback = 365 days
freshness = 30 days
price stress < 80% par
yield stress >= 15%
spread stress >= 1,000 bps
```

중요하게도 세 signal을 하나의 score로 합치지 않는다.

```text
price_credit_stress
yield_credit_stress
spread_credit_stress
```

`any_credit_stress`는 위 signal 중 하나라도 있는지를 보여주는 **research gate**일 뿐, default probability나 종합평가가 아니다.

## Fresh / stale / missing 분리

Credit coverage는 세 경우를 구분한다.

### 1. No point-in-time link

```text
linked_credit_instrument_count = 0
```

이것은 "채권이 없었다"가 아니다. Dataset link coverage가 없다는 뜻이다.

### 2. Linked but stale/no observation

과거 trade가 있더라도 cutoff에서 30일 초과 stale이면 historical/liquidity evidence로 보존하되 current-stress gate에서는 제외한다.

```text
observed_credit_instrument_count > 0
fresh_credit_instrument_count = 0
stale_credit_instrument_count > 0
```

### 3. Fresh evidence

Fresh instrument에 대해서만 current-stress threshold를 센다.

따라서 오래된 40-cent trade 하나 때문에 cutoff 당시 credit distress가 있었던 것처럼 cohort가 오염되지 않는다.

## Multi-bond issuer

한 equity security에 여러 채권이 연결될 수 있다.

Aggregate output은 다음을 보존한다.

```text
linked_credit_instrument_count
observed_credit_instrument_count
fresh_credit_instrument_count
stale_credit_instrument_count
min_fresh_price_pct_par
max_fresh_yield_pct
max_fresh_spread_bps
price_stress_instrument_count
yield_stress_instrument_count
spread_stress_instrument_count
```

즉 "가장 나쁜 채권 하나"만 남기지 않고 coverage와 signal count를 함께 보여준다.

Instrument-level `credit_evidence`도 모두 보존한다.

## Spread 의미

Bulk replay에서는 다음 두 경우에만 spread를 사용한다.

```text
1. observation.spread_bps가 명시됨
2. observation.yield_pct - observation.benchmark_yield_pct
```

이 단계는 외부 Treasury API를 종목마다 호출하지 않는다. Bulk cohort는 reproducible normalized dataset을 전제로 한다.

Spread threshold는 market stress signal이다. 물리적 부도확률로 변환하지 않는다.

## CLI

```bash
distressed-equity-credit-replay \
  --analysis-date 2022-12-31 \
  --securities-csv crsp_like_security_master.csv \
  --prices-csv crsp_like_prices.csv \
  --credit-links-csv security_to_bond_membership.csv \
  --bond-observations-csv trace_daily.csv \
  --output joint_distress_2022-12-31.json \
  --markdown-output joint_distress_2022-12-31.md
```

Threshold를 조절하려면:

```bash
--min-drawdown 0.70
--credit-lookback-days 365
--credit-max-staleness-days 30
--credit-price-below 70
--credit-yield-at-or-above 20
--credit-spread-at-or-above 1500
```

## 출력

Cohort summary:

```text
equity_candidate_count
candidates_with_credit_links
candidates_with_fresh_credit
candidates_with_credit_stress
```

각 candidate에는 equity replay seed와 credit aggregate, instrument-level evidence가 함께 저장된다.

이를 이용하면 coverage bias도 눈에 보인다.

예를 들어:

```text
100 equity-distress candidates
  62 point-in-time credit links
  47 fresh credit observations
  31 credit-stress signals
```

이라면 `31 / 100`을 곧바로 "credit distress rate"라고 해석하면 안 된다.

먼저:

```text
link coverage = 62%
fresh-observation coverage = 47%
credit stress among fresh = 31 / 47
```

를 분리해야 한다.

## 아직 outcome이 아닌 것

이 layer는 다음을 자동으로 라벨링하지 않는다.

- 회사가 12개월 생존했는가
- Chapter 11 / restructuring 여부
- 기존 보통주가 살아남았는가
- 3년 안에 정상화했는가
- 회생 후 기존 보통주가 몇 배가 되었는가

주가 데이터만으로 business survival이나 legal restructuring을 추정하면 안 된다.

다음 단계에서는 market-observable outcome과 source-backed legal/business outcome을 분리한다.

### market-observable outcome으로 deterministic하게 만들 수 있는 것

예:

```text
same permanent security_id가 +12m에도 거래되는가
+12m / +3y adjusted return multiple
+3y max subsequent multiple
```

### 별도 source가 필요한 것

```text
bankruptcy filing
common cancellation / old equity extinction
reorganization effective date
business operating survival
```

이 구분이 base rate를 현실에 맞추는 핵심이다.

## 보수적 경계

- Current issuer/debt mapping의 historical backfill 금지
- ticker/name fuzzy join 금지
- stale credit를 current stress로 세지 않음
- transaction-level TRACE를 implicit close로 처리하지 않음
- slow per-bond API를 cohort replay에 사용하지 않음
- price/yield/spread를 하나의 default-probability 숫자로 합치지 않음
- missing link를 no debt / no distress로 해석하지 않음

## 투자 연구에서의 의미

Carvana형 발굴 로직에서 중요한 질문은 단순히 "주가가 90% 빠졌나"가 아니다.

```text
Equity market:
  기존 보통주가 거의 실패를 가격했는가?

Credit market:
  senior claim도 실제로 distress를 가격했는가?

Reality:
  liquidity / maturity / covenant / unit economics를 보면
  실제 실패확률은 시장의 공동 가격보다 낮았는가?
```

Joint replay는 두 번째 질문을 population scale에서 복원하는 계층이다.

그 위에 outcome label을 붙여야 비로소 다음과 같은 경험적 base rate를 계산할 수 있다.

```text
P(common survives | equity distress, credit distress, liquidity profile)
P(3x within 3y | common survives, credit distress)
P(normalizes | specific impairment type)
```

그 단계에서도 probability는 데이터에서 계산하고, LLM이 임의로 만들어내지 않는다.
