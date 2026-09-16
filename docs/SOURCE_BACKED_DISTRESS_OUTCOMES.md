# Source-Backed Distress Outcomes

## 목적

주가와 채권 가격만으로는 다음을 확정할 수 없다.

- 회사가 실제로 12개월 생존했는가
- Chapter 11 / restructuring이 발생했는가
- 분석 당시의 기존 보통주가 법적으로 살아남았는가
- 영업이 정상화됐는가

따라서 `distressed-equity-credit-replay`의 market-observable outcome과 **legal/business outcome**을 분리한다.

Legal/business outcome은 별도 `--source-outcomes-csv`로만 들어온다.

## 절대 자동 추정하지 않는 규칙

다음 변환은 금지한다.

```text
same_security_active_12m = false
    != existing_common_survived_12m = false

price disappears
    != bankruptcy

bond price < 40
    != company failed
```

시장 데이터는 탐색/market-path evidence다. 법률적·사업적 outcome은 source-backed label이어야 한다.

## CSV schema

필수 컬럼:

```text
security_id
ticker
analysis_date
outcome_known_date
survived_12m
existing_common_survived_12m
normalized_within_3y
equity_multiple_3y
evidence_refs
verification_status
verifier
verified_on
evidence_reopened
```

선택 분류 컬럼:

```text
industry_group
impairment_type
leverage_bucket
notes
```

Outcome 값 자체는 blank일 수 있다. 단, 한 row에 최소 하나의 outcome은 resolved여야 한다.

## Metric-specific knowledge provenance

한 row의 outcome들이 서로 다른 날짜에 알려질 수 있다.

예:

```text
12m survival          -> 2021-12-31에 이미 확정
existing common 12m   -> 2021-12-31에 이미 확정
3y equity multiple    -> 2023-12-31에야 확정
```

이를 하나의 `outcome_known_date=2023-12-31`로만 표현하면 2022년 walk-forward prior에서 이미 알려져 있던 12개월 생존 결과까지 잃는다.

따라서 다음 optional pair를 지원한다.

```text
survived_12m_known_date
survived_12m_evidence_refs

existing_common_survived_12m_known_date
existing_common_survived_12m_evidence_refs

normalized_within_3y_known_date
normalized_within_3y_evidence_refs

equity_multiple_3y_known_date
equity_multiple_3y_evidence_refs
```

### Legacy fallback

Metric-specific date를 쓰지 않으면 기존 방식과 동일하게:

```text
metric known date = outcome_known_date
metric evidence refs = evidence_refs
```

를 사용한다.

따라서 기존 CSV는 그대로 호환된다.

### Metric-specific date를 앞당길 때

명시적 `*_known_date`를 넣으면 대응하는 `*_evidence_refs`도 반드시 있어야 한다.

예:

```csv
survived_12m,survived_12m_known_date,survived_12m_evidence_refs
true,2021-12-31,SEC:2021-10-K:001
```

날짜만 2021년으로 앞당기고 2024년 general evidence를 그대로 쓰는 식의 backdating은 허용하지 않는다.

Outcome이 blank인데 metric-specific date/ref를 넣는 것도 거부한다.

## 예시

```csv
security_id,ticker,analysis_date,outcome_known_date,survived_12m,survived_12m_known_date,survived_12m_evidence_refs,existing_common_survived_12m,existing_common_survived_12m_known_date,existing_common_survived_12m_evidence_refs,normalized_within_3y,equity_multiple_3y,equity_multiple_3y_known_date,equity_multiple_3y_evidence_refs,evidence_refs,verification_status,verifier,verified_on,evidence_reopened
12345,TEST,2020-12-31,2024-01-31,true,2021-12-31,SEC:2021-10-K:001,true,2021-12-31,SEC:2021-10-K:001,,4.2,2023-12-31,SEC:2023-10-K:004,SEC:2024-8-K:FINAL,verified,reviewer,2024-02-01,true
```

2022-06-30 knowledge cutoff에서는 이 row에서:

```text
survived_12m = true
existing_common_survived_12m = true
equity_multiple_3y = unresolved
```

만 보인다.

2024 cutoff에서는 3년 배수도 보인다.

## Verification boundary

Label은 다음 조건을 모두 통과해야 한다.

```text
verification_status = verified
verifier != blank
verified_on != blank
evidence_reopened = true
evidence_refs != blank
```

하나라도 빠지면 ingestion을 거부한다.

`evidence_refs`와 metric-specific evidence refs는 `;`로 여러 source를 구분한다.

Repository가 source text를 이 CSV만으로 다시 검증한다고 주장하지는 않는다. 이 계층은 **검토 provenance가 없는 임의 outcome row가 자동 base rate에 들어가는 것을 막는 구조적 경계**다.

## 시간 경계

### outcome_known_date

Row에 포함된 모든 resolved outcome이 최종적으로 알려진 날짜의 상한이다.

```text
outcome_known_date >= analysis_date
outcome_known_date >= every resolved metric known date
```

Metric-specific known date가 없으면 해당 metric은 `outcome_known_date`를 사용한다.

### 12개월 outcome

다음 필드가 resolved면:

```text
survived_12m
existing_common_survived_12m
```

현재 보수적 기본 규칙은 각 metric known date가 최소 +12개월 horizon 이후여야 한다.

파산·청산·common cancellation처럼 horizon 전에 irreversible failure가 확정되는 예외는 추후 terminal-event provenance를 별도 모델링하기 전까지 조기 확정으로 처리하지 않는다.

### 3년 equity multiple

`equity_multiple_3y`가 있으면 해당 metric known date가 +3년 이후여야 한다.

조기 인수/소각으로 최종 주주 outcome이 확정되는 예외도 terminal-event provenance가 생기기 전까지 자동 조기 확정하지 않는다.

### normalized_within_3y

```text
true
```

는 실제 정상화 사건이 3년 전에 발생하면 그 시점에 알 수 있다.

반대로:

```text
false
```

는 원칙적으로 +3년 horizon 전에는 확정할 수 없으므로 현재 parser는 +3년 이전 `false` known date를 거부한다.

## Knowledge cutoff 적용

Base-rate / walk-forward replay에서는 metric별로:

```text
metric_known_date <= knowledge cutoff
```

인 값만 사용한다.

한 row 전체를 all-or-nothing으로 숨기지 않는다.

따라서 미래 3년 outcome이 같은 row에 있더라도 이미 알려진 12개월 결과는 과거 prior에서 사용할 수 있다.

## Group comparison

Source-backed outcome은 joint replay의 credit evidence group에 붙는다.

```text
fresh_credit_stress
fresh_credit_no_stress
no_fresh_credit
```

각 group은 다음을 별도 denominator와 함께 저장한다.

```text
cohort_case_count
source_labeled_case_count
survived_12m_resolved_count
survived_12m_rate
common_12m_resolved_count
existing_common_survival_rate
normalized_3y_resolved_count
normalized_within_3y_rate
equity_multiple_3y_resolved_count
three_x_3y_rate
median_equity_multiple_3y
```

가장 중요한 원칙:

```text
missing source label != failure
missing future metric != failure
```

예를 들어 cohort 100개 중 12m survival이 60개, 3y multiple이 30개만 resolved라면 두 지표의 분모는 서로 다르다.

## CLI — ex-post outcome comparison

```bash
distressed-equity-credit-replay \
  --analysis-date 2022-12-31 \
  --securities-csv securities.csv \
  --prices-csv prices.csv \
  --credit-links-csv credit_links.csv \
  --bond-observations-csv trace_daily.csv \
  --outcome-cutoff 2026-01-31 \
  --source-outcomes-csv verified_outcomes.csv \
  --output cohort.json \
  --markdown-output cohort.md
```

`--source-outcomes-csv`는 ex-post 비교에서 `--outcome-cutoff` 없이는 실행할 수 없다.

Output에는 세 층이 따로 존재한다.

```text
market_outcomes
market_base_rates
source_outcome_base_rates
```

### market_outcomes

동일 permanent security membership과 adjusted-price path.

### market_base_rates

same-security active / market-price multiple의 conditional rate.

### source_outcome_base_rates

verified legal/business source에 기반한 company survival / common survival / normalization rate.

세 층을 서로 대체하지 않는다.

## Walk-forward prior에서의 사용

`distressed-equity-walk-forward-priors`는 target T0를 knowledge cutoff로 사용한다.

```text
historical analysis_date < target T0
metric_known_date <= target T0
```

인 metric만 calibration에 들어간다.

즉 사후에 완성된 outcome row를 저장해 두어도, target 시점에서 아직 알려지지 않았던 metric은 자동으로 masking된다.

자세한 내용은 `docs/WALK_FORWARD_PRIORS.md`를 본다.

## 실제 probability calibration으로 가는 길

데이터가 충분하다면 다음 조건부 경험률을 계산할 수 있다.

```text
P(company survives 12m | equity distress, credit distress)
P(existing common survives 12m | equity distress, credit distress)
P(normalizes within 3y | equity distress, credit distress, impairment type)
P(3x within 3y | verified common-survival cohort)
```

그 다음 liquidity runway, maturity wall, covenant headroom, impairment type 같은 T0 feature를 붙여 strata를 세분화한다.

중요한 것은 probability를 LLM이 정하는 게 아니라 **point-in-time input + 당시 알려져 있던 verified outcome population에서 계산한다는 점**이다.
