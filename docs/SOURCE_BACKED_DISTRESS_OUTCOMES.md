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

예:

```csv
security_id,ticker,analysis_date,outcome_known_date,survived_12m,existing_common_survived_12m,normalized_within_3y,equity_multiple_3y,industry_group,impairment_type,leverage_bucket,evidence_refs,verification_status,verifier,verified_on,evidence_reopened,notes
12345,TEST,2022-12-31,2025-12-31,true,true,true,4.2,autos,temporary,high,SEC:8-K:0001;COURT:case-1,verified,reviewer,2026-01-03,true,reviewed sources
```

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

`evidence_refs`는 `;`로 여러 source를 구분한다. Repository가 source text를 이 CSV만으로 다시 검증한다고 주장하지는 않는다. 이 계층은 **검토 provenance가 없는 임의 outcome row가 자동 base rate에 들어가는 것을 막는 최소 구조적 경계**다.

## 시간 경계

### outcome_known_date

Outcome이 연구자가 실제로 알 수 있게 된 날짜다.

```text
outcome_known_date >= analysis_date
```

Base-rate replay에서는:

```text
outcome_known_date <= knowledge cutoff
```

인 label만 사용할 수 있다.

따라서 2026년에 알려진 결과를 2024년 당시 base-rate library에 넣는 look-ahead를 막는다.

### 12개월 outcome

다음 필드가 resolved면:

```text
survived_12m
existing_common_survived_12m
```

`outcome_known_date`가 최소 +12개월 horizon 이후여야 한다.

### 3년 equity multiple

`equity_multiple_3y`가 있으면 `outcome_known_date`가 +3년 이후여야 한다.

`normalized_within_3y=true`는 실제 정상화 사건이 더 일찍 발생했다면 3년 전에 알 수도 있으므로 무조건 +3년을 요구하지 않는다. 반대로 `false` 라벨의 신뢰성은 source reviewer가 evidence로 책임져야 한다.

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
```

예를 들어 cohort 100개 중 verified legal outcome이 35개뿐이면 survival rate의 사실상 coverage는 35개다. 나머지 65개를 실패로 넣지 않는다.

## CLI

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

`--source-outcomes-csv`는 `--outcome-cutoff` 없이는 실행할 수 없다.

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

## 실제 probability calibration으로 가는 길

이제 데이터가 충분하다면 다음 조건부 경험률을 계산할 수 있다.

```text
P(company survives 12m | equity distress, credit distress)
P(existing common survives 12m | equity distress, credit distress)
P(normalizes within 3y | equity distress, credit distress, impairment type)
P(3x endpoint within 3y | verified common-survival cohort)
```

그 다음 단계는 liquidity runway, maturity wall, covenant headroom, impairment type 같은 T0 feature를 붙여 strata를 세분화하는 것이다.

중요한 것은 probability를 LLM이 정하는 게 아니라 **point-in-time input + verified outcome population에서 계산한다는 점**이다.
