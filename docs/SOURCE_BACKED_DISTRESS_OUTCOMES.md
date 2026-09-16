# Source-Backed Distress Outcomes

## 목적

주가와 채권 가격만으로는 다음을 확정할 수 없다.

- 회사가 실제로 12개월 생존했는가
- Chapter 11 / restructuring이 발생했는가
- 분석 당시의 기존 보통주가 법적으로 살아남았는가
- 영업이 정상화됐는가
- 기존 보통주의 최종 경제적 payoff가 얼마였는가

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

Chapter 11 filing
    != permanent company failure

cash acquisition closed
    != automatically inferred equity_multiple_3y
```

시장 데이터와 일반 사건명은 탐색/evidence다. 법률적·사업적 outcome과 최종 주주 payoff는 source-backed metric/fact여야 한다.

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

Metric-specific date를 쓰지 않으면:

```text
metric known date = outcome_known_date
metric evidence refs = evidence_refs
```

를 사용한다.

기존 CSV는 그대로 호환된다.

### Metric-specific date를 앞당길 때

명시적 `*_known_date`를 넣으면 대응하는 `*_evidence_refs`도 반드시 있어야 한다.

```csv
survived_12m,survived_12m_known_date,survived_12m_evidence_refs
true,2021-12-31,SEC:2021-10-K:001
```

날짜만 과거로 앞당기고 미래 general evidence를 재사용하는 backdating은 허용하지 않는다.

Outcome이 blank인데 metric-specific date/ref를 넣는 것도 거부한다.

## Cutoff-safe partial view

`known_labels(cutoff)`는 단순히 미래 metric 값만 지우지 않는다.

Cutoff까지 admitted된 metric의 evidence refs만 다시 합쳐 row-level `evidence_refs`를 재구성하고, 미래 outcome을 요약할 가능성이 있는 free-form `notes`는 제거한다.

따라서 2024 최종 문서가 row-level evidence에 있어도 2022 cutoff-safe label의 provenance에는 남지 않는다.

Terminal/payoff fact도 해당 fact가 cutoff 이전에 알려졌고, cutoff에서 실제로 admitted된 metric을 뒷받침할 때만 partial view에 남는다.

## 조기 terminal outcome provenance

Nominal horizon 전에 `false`가 **돌이킬 수 없이 확정된 경우**를 지원하되, 사건명을 보고 자동 추론하지 않는다.

두 종류의 explicit terminal fact를 지원한다.

### Company terminal fact

Optional fields:

```text
company_terminal_event_type
company_terminal_event_date
company_terminal_event_known_date
company_terminal_event_evidence_refs
```

허용 type:

```text
liquidation
dissolution
permanent_cessation
```

이 fact는 다음 조기 `false`를 뒷받침할 수 있다.

```text
survived_12m = false
normalized_within_3y = false
```

### Existing-common terminal fact

Optional fields:

```text
common_terminal_event_type
common_terminal_event_date
common_terminal_event_known_date
common_terminal_event_evidence_refs
```

허용 type:

```text
cancellation
extinguishment
cash_acquisition_closed
final_liquidation_distribution
```

이 fact는 다음 조기 `false`만 뒷받침한다.

```text
existing_common_survived_12m = false
```

### Terminal fact 검증 규칙

Terminal fact를 쓰려면 type/date/known-date/evidence-refs가 모두 필요하다.

```text
terminal event date >= analysis date
terminal known date >= terminal event date
terminal event date <= 해당 metric horizon
terminal known date <= metric known date
terminal evidence refs ⊆ metric evidence refs
```

그리고 terminal fact는 실제로 관련된 `false` outcome이 있는 경우에만 허용한다.

예를 들어 회사 terminal fact를 넣고 `survived_12m=true`만 주장하는 row는 거부한다.

### 중요한 비추론 원칙

```text
bankruptcy filing
```

같은 일반 사건은 자동으로 `company_terminal_fact`가 되지 않는다. 청산·해산·영구 영업중단처럼 metric을 실제로 불가역적으로 확정하는 사실을 검토자가 명시적으로 입증해야 한다.

마찬가지로 company terminal fact가 있다고 해서 기존 common extinction을 자동으로 만들지 않는다. 기존 common의 종료는 별도 common terminal fact가 필요하다.

## Final shareholder payoff provenance

`equity_multiple_3y`는 원칙적으로 +3년 이후 확정한다. 다만 +3년 전에 T0 기존 보통주의 **전체 최종 payoff가 법적으로·경제적으로 고정**된 경우에는 조기 확정을 허용한다.

Optional fields:

```text
final_shareholder_payoff_event_type
final_shareholder_payoff_event_date
final_shareholder_payoff_known_date
final_shareholder_payoff_multiple
final_shareholder_payoff_evidence_refs
```

허용 type:

```text
fixed_cash_acquisition_closed
final_liquidation_distribution
common_extinguished_no_distribution
```

이 계층은 사건명에서 payoff를 계산하지 않는다. 검토자가 **최종 payoff multiple 자체**를 source-backed 값으로 명시해야 한다.

### 검증 규칙

```text
payoff event date >= analysis date
payoff known date >= payoff event date
payoff event date <= analysis date + 3y
payoff multiple is finite and >= 0
payoff multiple == equity_multiple_3y
payoff known date <= equity_multiple_3y_known_date
payoff evidence refs ⊆ equity_multiple_3y_evidence_refs
```

`common_extinguished_no_distribution`이면 payoff multiple은 반드시 `0`이어야 한다.

Final payoff fact가 있으면서 `equity_multiple_3y`가 blank인 row도 거부한다.

Final payoff fact가 없다면 +3년 이전 `equity_multiple_3y_known_date`는 계속 거부한다.

### Terminal common-survival fact와의 분리

Final payoff provenance와 common-survival terminal provenance는 서로 다른 증명 층이다.

```text
final payoff known
    != automatically existing_common_survived_12m=false

common cancelled
    != automatically a particular equity_multiple_3y
```

필요하면 두 fact를 각각 별도로 입증한다. 한쪽에서 다른 쪽을 추론하지 않는다.

자세한 설계는 `docs/FINAL_SHAREHOLDER_PAYOFFS.md`를 본다.

## Horizon 경계

### 12개월 outcome

일반적으로:

```text
survived_12m
existing_common_survived_12m
```

의 known date는 +12개월 이후여야 한다.

예외적으로 horizon 전에 `false`가 irreversibly 확정됐다면 해당 metric에 맞는 explicit terminal fact로 조기 확정할 수 있다.

`true`를 horizon 전에 확정하는 것은 terminal fact가 있어도 허용하지 않는다.

### normalized_within_3y

```text
true
```

는 실제 정상화가 발생한 시점에 조기 확정할 수 있다.

```text
false
```

는 원칙적으로 +3년 이후에만 확정하지만, company terminal fact가 3년 전에 정상화 가능성을 불가역적으로 소멸시켰다면 조기 확정할 수 있다.

### equity_multiple_3y

원칙적으로 +3년 horizon 이후 확정한다.

예외적으로 +3년 이내 발생한 verified final shareholder payoff fact가 전체 payoff를 고정했고 그 fact가 metric known date까지 알려졌다면 조기 확정할 수 있다.

사건명만으로 배수를 추정하지 않는다.

## Verification boundary

Label은 다음 조건을 모두 통과해야 한다.

```text
verification_status = verified
verifier != blank
verified_on != blank
evidence_reopened = true
evidence_refs != blank
```

Metric-specific provenance, terminal provenance, final-payoff provenance도 각각 자기 evidence refs와 knowledge date를 가져야 한다.

Repository가 source text를 이 CSV만으로 다시 검증한다고 주장하지는 않는다. 이 계층은 **검토 provenance가 없는 임의 outcome row가 자동 base rate에 들어가는 것을 막는 구조적 경계**다.

## Knowledge cutoff 적용

Base-rate / walk-forward replay에서는 metric별로:

```text
metric_known_date <= knowledge cutoff
```

인 값만 사용한다.

한 row 전체를 all-or-nothing으로 숨기지 않는다.

Terminal fact로 조기 확정된 `false`는 terminal fact와 metric이 당시 이미 알려졌을 때만 들어간다.

Final-payoff fact로 조기 확정된 3년 배수도 payoff fact와 metric이 target T0까지 이미 알려졌을 때만 들어간다. 사후에 알게 된 acquisition/liquidation payoff는 과거 prior에 역류하지 않는다.

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

Output에는 시장 관측치와 source-backed legal/business outcome이 서로 다른 계층으로 존재한다.

## Walk-forward prior에서의 사용

`distressed-equity-walk-forward-priors`는 target T0를 knowledge cutoff로 사용한다.

```text
historical analysis_date < target T0
metric_known_date <= target T0
```

인 metric만 calibration에 들어간다.

Terminal fact로 조기 확정된 `false`와 final-payoff fact로 조기 확정된 3년 배수도 각각 그 provenance가 target T0까지 이미 알려졌을 때만 calibration population에 들어간다.

따라서 2020 T0 기업이 2021년에 fixed-cash acquisition으로 최종 4.0x payoff가 확정됐다면, 2022 target prior에서는 사용할 수 있지만 2021 payoff 확정 전 target prior에는 사용할 수 없다.

자세한 내용은 `docs/WALK_FORWARD_PRIORS.md`와 `docs/FINAL_SHAREHOLDER_PAYOFFS.md`를 본다.

## 실제 probability calibration으로 가는 길

데이터가 충분하다면 다음 조건부 경험률을 계산할 수 있다.

```text
P(company survives 12m | equity distress, credit distress)
P(existing common survives 12m | equity distress, credit distress)
P(normalizes within 3y | equity distress, credit distress, impairment type)
P(3x within 3y | T0 strata)
```

중요한 것은 probability를 LLM이 정하는 게 아니라 **point-in-time input + 당시 알려져 있던 verified outcome population에서 계산한다는 점**이다.
