# Historical Distress Population Coverage Audit

## 목적

실제 CRSP-scale equity + TRACE/WRDS-scale credit population을 넣기 전에 가장 먼저 확인해야 하는 것은 전략 성과가 아니라 **데이터 coverage**다.

예를 들어 1,000개 historical universe observation이 있어도:

```text
scanned = 900
evaluable market histories = 760
equity distress cases = 140
point-in-time debt links = 90
fresh credit observations = 55
verified legal/business outcomes = 40
verified T0 liquidity features = 28
```

이면 각 단계의 분모가 다르다.

특히 다음 두 오류를 막아야 한다.

```text
scanned but price/history unavailable
    != evaluated and non-distress

3y outcome not yet matured by coverage cutoff
    != matured but source outcome missing
```

이를 숨긴 채 `31% survived`, `18% 3x` 같은 숫자만 보면 selection bias와 right censoring을 실제 확률로 착각하기 쉽다.

`population_coverage_cli`는 여러 historical cutoff를 동일한 replay logic으로 돌려 이 coverage funnel을 먼저 노출한다.

## 실행

```bash
python -m distressed_equity.population_coverage_cli \
  --analysis-date 2018-12-31 \
  --analysis-date 2020-12-31 \
  --analysis-date 2022-12-31 \
  --outcome-coverage-cutoff 2026-09-17 \
  --securities-csv securities.csv \
  --prices-csv prices.csv \
  --credit-links-csv credit_links.csv \
  --bond-observations-csv trace_daily.csv \
  --source-outcomes-csv verified_outcomes.csv \
  --survival-features-csv verified_t0_features.csv \
  --output coverage.json \
  --markdown-output coverage.md
```

`outcome-coverage-cutoff`은 **ex-post dataset completeness를 어느 knowledge date까지 측정할지** 정하는 값이다.

이 cutoff는 과거 T0 prior 생성용 cutoff와 다르다. Coverage audit는 사후 데이터 품질 진단이다.

## Market evaluability funnel

각 replay date마다 다음을 보존한다.

```text
historical_universe_size
scanned_security_count
evaluable_security_count
unevaluable_security_count
evaluable_non_candidate_count
equity_distress_case_count
```

즉:

```text
historical universe
  -> scanned securities
      -> evaluable
          -> distress candidate
          -> evaluable non-candidate
      -> unevaluable
```

으로 나눈다.

### 왜 scanned와 evaluable을 분리하는가

`run_historical_replay`에서 다음과 같은 이유는 시장 상태를 판정할 수 없는 **unevaluable**이다.

예:

```text
no cutoff-date raw price
no price history in lookback window
history is not split/dividend adjusted
unable to compute adjustment-safe drawdown
```

반면 다음은 필요한 데이터는 있었지만 configured screen을 통과하지 않은 **evaluable non-candidate**다.

```text
raw price below configured minimum
drawdown below configured distress threshold
```

알 수 없는 새로운 skip reason은 보수적으로 `unevaluable`로 분류한다.

따라서 distress prevalence를 볼 때:

```text
distress_rate_of_evaluable
```

의 분모는 `evaluable_security_count`다. `scanned_security_count`를 그대로 분모로 쓰지 않는다.

추가로:

```text
evaluable_coverage_of_scanned
skip_reason_counts[]
```

를 내보내 어떤 이유로 evaluation coverage가 떨어지는지 확인할 수 있다.

## Credit coverage ratios

Distress candidate 안에서 다음을 별도로 센다.

```text
credit_linked_case_count
fresh_credit_case_count
credit_stress_case_count
credit_link_coverage_of_distress
fresh_credit_coverage_of_distress
fresh_credit_coverage_of_linked
```

예:

```text
distress cases = 100
linked = 60
fresh = 45
```

이면:

```text
link / distress = 60%
fresh / distress = 45%
fresh / linked = 75%
```

를 따로 본다.

중요:

```text
no link != no debt
no fresh trade != healthy credit
```

## Source-backed outcome coverage와 right censoring

`outcome_coverage_cutoff`까지 실제로 알려진 verified metric만 센다.

Raw label count:

```text
survived_12m_labeled_count
existing_common_12m_labeled_count
normalized_3y_labeled_count
equity_multiple_3y_labeled_count
```

하지만 이것만으로는 coverage를 판단하지 않는다. 각 metric마다 다음을 추가로 보존한다.

```text
*_horizon_eligible_count
*_early_resolved_count
*_right_censored_count
*_missing_among_horizon_eligible_count
*_label_coverage_of_horizon_eligible
```

### Horizon eligible

Nominal horizon이 coverage cutoff까지 이미 지났으면 그 distress case는 outcome coverage의 정상 분모에 들어간다.

```text
12m metrics: analysis_date + 1y <= coverage cutoff
3y metrics:  analysis_date + 3y <= coverage cutoff
```

이 경우:

```text
missing_among_horizon_eligible
```

은 실제 source-label coverage gap을 의미한다.

### Right-censored

Nominal horizon이 아직 오지 않았다면 unresolved case를 missing label로 세지 않는다.

예:

```text
analysis T0 = 2024-12-31
coverage cutoff = 2026-09-17
3y horizon = 2027-12-31
```

이면 일반적인 unresolved 3y outcome은:

```text
right_censored = true
```

이지:

```text
missing source outcome
```

이 아니다.

### Early resolved

Horizon 전이라도 irreversible terminal event나 verified final shareholder payoff 때문에 metric이 이미 확정될 수 있다.

예:

```text
company liquidation -> normalized_within_3y=false 조기 확정
common cancellation -> existing_common_survived_12m=false 조기 확정
fixed cash acquisition close -> equity_multiple_3y 조기 확정
```

이런 값은:

```text
*_early_resolved_count
```

에 들어가며, 아직 nominal horizon이 오지 않은 다른 case들과 구분된다.

따라서 pre-horizon cohort에서:

```text
horizon eligible = 0
early resolved = 3
right censored = 17
```

처럼 볼 수 있다.

## Source-label case coverage

다음 top-level 값도 유지한다.

```text
source_labeled_case_count
source_label_coverage_of_distress
```

이 값은 “어떤 metric이든 cutoff까지 source-backed로 알려진 case”를 보여주는 broad coverage다.

Metric completeness 판단에는 반드시 위의 horizon-specific denominator를 사용한다.

## T0 survival-feature coverage

Distress case별 exact T0 snapshot을 기준으로 다음을 센다.

```text
t0_feature_snapshot_count
liquidity_runway_feature_count
nearest_maturity_feature_count
covenant_headroom_feature_count
net_leverage_feature_count
impairment_type_feature_count
feature_snapshot_coverage_of_distress
```

Feature row가 있어도 모든 숫자가 채워졌다고 가정하지 않는다.

예:

```text
T0 feature row exists
liquidity known
maturity unknown
covenant unknown
```

이면 각 field coverage가 따로 보인다.

## Repeated security observations

여러 replay date에서 같은 permanent `security_id`가 반복될 수 있다.

Top-level:

```text
total_case_observations
unique_security_count
repeated_case_observation_count
```

를 제공한다.

이 값은 **episode dedup 자체는 아니다**.

Walk-forward prior에서는 별도로 connected distress episode를 collapse한다. Coverage audit는 raw replay observation 중복을 보여주는 역할만 한다.

## Missingness 해석 규칙

### Unevaluable market observation

```text
unknown screen status
```

이지:

```text
non-distress
```

가 아니다.

### Missing credit link

```text
unknown credit coverage
```

이지:

```text
healthy credit
```

이 아니다.

### Missing legal/business outcome after horizon maturity

```text
unlabeled among horizon-eligible cases
```

이다. 이때만 source-outcome coverage gap으로 본다.

### Pre-horizon unresolved outcome

```text
right-censored
```

이지 missing label이 아니다.

### Missing T0 feature

```text
unknown feature
```

이지 favorable/adverse bucket으로 자동 이동하지 않는다.

## Coverage audit와 calibration의 관계

Calibration 결과가 좋아 보여도 coverage가 낮으면 일반화가 제한된다.

예를 들어:

```text
all evaluable distress = 1,000
3y horizon-eligible = 700
3y source outcomes = 120
```

에서 Brier가 좋다면 먼저 120개가 어떤 이유로 labelable했는지 봐야 한다.

반대로 3y horizon-eligible 자체가 0인 최근 cohort에서 label이 없다는 사실은 coverage 실패가 아니다.

따라서 실제 population research 순서는:

```text
1. market evaluability audit
2. credit / outcome / feature coverage audit
3. horizon maturity / censoring 확인
4. missingness pattern 확인
5. walk-forward prior 생성
6. Brier / calibration curve
7. regime / strata별 stability
8. 그 뒤에만 shrinkage / hierarchical model 검토
```

가 안전하다.

## 왜 자동 coverage threshold를 두지 않는가

현재 구현은:

```text
coverage < 70% -> unusable
```

같은 임의 threshold를 만들지 않는다.

Dataset, 기간, 채권 발행 여부, issuer type에 따라 attainable coverage가 다르기 때문이다.

대신 raw numerator / denominator와 censoring 상태를 노출하고, 실제 population을 본 뒤 연구자가 필요한 minimum coverage criterion을 별도 validation protocol로 정하도록 한다.

## 투자 연구에서의 의미

Carvana형 기업을 찾는 로직의 가장 위험한 실패는 모델 수식보다 **보이지 않는 selection bias와 censoring bias**일 수 있다.

```text
가격 history가 충분한 기업만 남음
채권 데이터가 있는 기업만 남음
법원/SEC outcome을 쉽게 검증한 기업만 남음
오래된 cohort만 3년 outcome이 충분히 성숙함
liquidity feature를 추출하기 쉬운 기업만 남음
```

이런 subset에서 나온 경험률을 전체 distress universe의 확률처럼 쓰면 현실성이 무너진다.

Coverage audit는 그 오류를 가장 먼저 드러내는 계층이다.
