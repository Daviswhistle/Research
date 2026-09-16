# Historical Equity + Credit Distress Replay

## 목적

단일 기업에서 당시 채권 가격을 복원하는 것과, **과거 distress population 전체에서 equity와 credit이 동시에 무엇을 가격했는지 재생하는 것**은 다른 문제다.

이 layer는 다음 질문을 위해 존재한다.

> 주가가 이미 크게 붕괴한 기업 중에서, 당시 corporate credit도 실제로 심각한 distress를 가격한 사례는 얼마나 많았고, 그 뒤 동일 permanent security의 시장 경로는 어땠는가?

구조는 candidate 생성과 outcome 관측을 명시적으로 분리한다.

```text
T0 cutoff data only
  -> point-in-time equity distress seeds
  -> contemporaneous credit evidence
  -> joint distress cohort

optional later outcome cutoff
  -> same permanent security membership at +12m / +3y
  -> adjusted-price endpoint multiples
  -> max observed adjusted-price multiple within 3y
```

미래 outcome 데이터는 T0 후보 선정에 들어가지 않는다.

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

optional --outcome-cutoff
    -> market-observable outcome layer
```

핵심 join key:

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

Survivorship-safe replay와 market outcome membership에는 다음이 필요하다.

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

T0에서는 cutoff 이전 adjusted history만 사용해 prior peak 대비 drawdown을 계산한다. 미래 price row가 같은 파일에 존재해도 candidate generator는 cutoff 이후 row를 읽지 않는다.

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

현재 알고 있는 issuer-bond mapping을 2022년으로 소급하지 않는다. Cutoff에 실제로 active였던 identifier membership만 사용한다.

## 입력 4: daily bond observations

`CsvBondMarketProvider` normalized daily schema:

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

이는 equity side 후보 생성이다. Peak market cap / EV distress gate를 대체하지 않는다.

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

세 signal은 별도로 유지한다.

```text
price_credit_stress
yield_credit_stress
spread_credit_stress
```

`any_credit_stress`는 research gate일 뿐 default probability나 종합평가가 아니다.

## Fresh / stale / missing 분리

### No point-in-time link

```text
linked_credit_instrument_count = 0
```

이것은 "채권이 없었다"가 아니라 dataset link coverage가 없다는 뜻이다.

### Linked but stale/no observation

30일 초과 stale trade는 historical/liquidity evidence로 보존하지만 current-stress gate에서는 제외한다.

```text
observed_credit_instrument_count > 0
fresh_credit_instrument_count = 0
stale_credit_instrument_count > 0
```

### Fresh evidence

Fresh instrument에 대해서만 current-stress threshold를 센다.

## Multi-bond issuer

한 equity security에 여러 채권이 연결될 수 있다.

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

Instrument-level `credit_evidence`도 모두 보존한다.

## Spread 의미

Bulk replay에서는 다음 두 경우에만 spread를 사용한다.

```text
1. observation.spread_bps가 명시됨
2. observation.yield_pct - observation.benchmark_yield_pct
```

외부 Treasury API를 종목마다 호출하지 않는다. Spread threshold는 market stress signal이며 물리적 부도확률로 변환하지 않는다.

## CLI — cutoff cohort만 생성

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

Threshold:

```bash
--min-drawdown 0.70
--credit-lookback-days 365
--credit-max-staleness-days 30
--credit-price-below 70
--credit-yield-at-or-above 20
--credit-spread-at-or-above 1500
```

## Optional market-observable outcomes

미래 시장 데이터를 명시적으로 outcome용으로만 쓰려면:

```bash
distressed-equity-credit-replay \
  --analysis-date 2022-12-31 \
  --securities-csv crsp_like_security_master.csv \
  --prices-csv crsp_like_prices.csv \
  --credit-links-csv security_to_bond_membership.csv \
  --bond-observations-csv trace_daily.csv \
  --outcome-cutoff 2026-01-31 \
  --output joint_with_outcomes.json
```

`--outcome-cutoff`을 생략하면 미래 outcome layer 자체가 실행되지 않는다.

### +12m / +3y membership

```text
same_security_active_12m
same_security_active_3y
```

이는 **동일 permanent security_id가 그 horizon의 point-in-time universe에 존재하는가**만 뜻한다.

다음과 동일하지 않다.

```text
회사 생존
파산 회피
기존 보통주의 법적 생존
reorganization 성공
```

Security가 M&A, recap, legal cancellation 등으로 사라지는 이유는 market membership만으로 구분할 수 없다.

### Horizon adjusted-price multiple

Security가 horizon에도 active라면 target date 이후 첫 adjusted price를 찾는다.

기본 grace window:

```text
31 days
```

```text
adjusted_price_multiple_12m
adjusted_price_multiple_3y
```

예를 들어 distress-date adjusted price가 5이고 +3y 첫 관측 adjusted price가 15이면:

```text
adjusted_price_multiple_3y = 3.0x
```

Security는 active인데 grace window 내 가격이 없으면 배수는 unresolved다.

### 3년 내 max observed multiple

```text
max_adjusted_price_multiple_within_3y
```

이 값은 endpoint return과 다르다.

예:

```text
T0: 5
18개월: 20  -> max observed = 4.0x
3년: 15     -> endpoint = 3.0x
```

두 값을 섞지 않는다.

또한 cash acquisition, security cancellation, reorganization distribution 등은 adjusted-price series만으로 완전한 shareholder total return을 보장하지 못하므로 `adjusted-price ratio`라고 명시한다.

### Outcome maturity gate

Outcome cutoff가 horizon에 도달하지 않았으면 `False`가 아니라 `None / unresolved`로 남긴다.

```text
analysis date: 2022-12-31
outcome cutoff: 2023-06-30

+12m active = unresolved
+3y active = unresolved
```

미성숙 case를 실패 case로 세지 않는 것이 중요하다.

## Cohort output

T0 coverage:

```text
equity_candidate_count
candidates_with_credit_links
candidates_with_fresh_credit
candidates_with_credit_stress
```

Optional outcome coverage:

```text
case_count
resolved_12m_membership_count
active_12m_count
resolved_3y_membership_count
active_3y_count
resolved_3y_endpoint_multiple_count
resolved_3y_max_multiple_count
```

Coverage bias를 분리해서 봐야 한다.

예:

```text
100 equity-distress candidates
  62 point-in-time credit links
  47 fresh credit observations
  31 credit-stress signals
```

`31 / 100`을 곧바로 "credit distress rate"라고 해석하지 않는다.

```text
link coverage = 62%
fresh-observation coverage = 47%
credit stress among fresh = 31 / 47
```

## Market outcome과 legal/business outcome 분리

현재 deterministic market layer가 만드는 것:

```text
same permanent security_id active at +12m/+3y
+12m/+3y adjusted-price multiple
max observed adjusted-price multiple within 3y
```

별도 source가 필요한 것:

```text
bankruptcy filing
common cancellation / old equity extinction
reorganization effective date
business operating survival
normalized operating economics
```

따라서 market membership `False`를 `existing_common_survived_12m=False`로 자동 변환하지 않는다.

이 구분이 base rate를 현실에 맞추는 핵심이다.

## 보수적 경계

- Current issuer/debt mapping historical backfill 금지
- ticker/name fuzzy join 금지
- stale credit를 current stress로 세지 않음
- transaction-level TRACE를 implicit close로 처리하지 않음
- slow per-bond API를 cohort replay에 사용하지 않음
- price/yield/spread를 하나의 default-probability 숫자로 합치지 않음
- missing link를 no debt / no distress로 해석하지 않음
- outcome cutoff 이전에 성숙하지 않은 horizon을 실패로 처리하지 않음
- same-security membership을 corporate/legal survival label로 재명명하지 않음
- max-within-3y multiple과 3y endpoint multiple을 혼합하지 않음

## 투자 연구에서의 의미

Carvana형 발굴 로직에서 중요한 질문은 단순히 "주가가 90% 빠졌나"가 아니다.

```text
Equity market:
  기존 보통주가 거의 실패를 가격했는가?

Credit market:
  senior claim도 실제로 distress를 가격했는가?

Observed market path:
  동일 permanent security는 이후에도 거래됐는가?
  +12m/+3y 가격경로는 어땠는가?

Reality:
  liquidity / maturity / covenant / unit economics와
  bankruptcy / restructuring source를 보면 실제 실패는 무엇이었는가?
```

Joint replay + market outcome layer는 앞의 세 질문을 population scale에서 결정론적으로 복원한다.

그 위에 source-backed legal/business outcome을 붙이면 비로소 다음과 같은 경험적 base rate를 만들 수 있다.

```text
P(common legally survives | equity distress, credit distress, liquidity profile)
P(3x endpoint within 3y | common survives, credit distress)
P(normalizes | specific impairment type)
```

그 단계에서도 probability는 데이터에서 계산하고, LLM이 임의로 만들어내지 않는다.
