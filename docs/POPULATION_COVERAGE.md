# Historical Distress Population Coverage Audit

## 목적

실제 CRSP-scale equity + TRACE/WRDS-scale credit population을 넣기 전에 가장 먼저 확인해야 하는 것은 전략 성과가 아니라 **데이터 coverage**다.

예를 들어 1,000개 equity-distress case가 있어도:

```text
point-in-time debt links = 420
fresh credit observations = 260
verified legal/business outcomes = 180
verified T0 liquidity features = 90
```

이면 이후 경험률은 서로 다른 coverage population 위에서 계산된다.

이를 숨긴 채 `31% survived`, `18% 3x` 같은 숫자만 보면 selection bias를 실제 확률로 착각하기 쉽다.

`population_coverage_cli`는 여러 historical cutoff를 동일한 replay logic으로 돌려 이 coverage funnel을 먼저 노출한다.

## 실행

현재 module entrypoint:

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

## Date별 funnel

각 replay date마다 다음을 보존한다.

```text
historical_universe_size
scanned_security_count
equity_distress_case_count
credit_linked_case_count
fresh_credit_case_count
credit_stress_case_count
```

즉:

```text
historical universe
  -> scanned securities
  -> equity distress
  -> point-in-time debt linked
  -> fresh credit observable
  -> fresh credit stress
```

순서가 보인다.

## Credit coverage ratios

```text
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

## Source-backed outcome coverage

`outcome_coverage_cutoff`까지 실제로 알려진 verified metric만 센다.

```text
source_labeled_case_count
survived_12m_labeled_count
existing_common_12m_labeled_count
normalized_3y_labeled_count
equity_multiple_3y_labeled_count
source_label_coverage_of_distress
```

Metric-specific known date가 있는 경우 metric마다 cutoff를 따로 적용한다.

따라서 한 case에서:

```text
12m survival known
3y multiple unknown
```

이면 source-labeled case에는 포함되지만 3y multiple coverage에는 들어가지 않는다.

조기 terminal/final-payoff provenance로 horizon 전에 확정된 metric도 그 known date가 coverage cutoff 이전이면 정확히 포함된다.

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

### Missing credit link

```text
unknown credit coverage
```

이지:

```text
healthy credit
```

이 아니다.

### Missing legal/business outcome

```text
unlabeled
```

이지:

```text
failed
survived
```

어느 쪽도 아니다.

### Missing T0 feature

```text
unknown feature
```

이지 favorable/adverse bucket으로 자동 이동하지 않는다.

## Coverage audit와 calibration의 관계

Calibration 결과가 좋아 보여도 coverage가 낮으면 일반화가 제한된다.

예를 들어:

```text
all distress = 1,000
source outcomes = 120
```

에서 Brier가 좋다면 먼저 120개가 어떤 이유로 labelable했는지 봐야 한다.

따라서 실제 population research 순서는:

```text
1. coverage audit
2. missingness pattern 확인
3. walk-forward prior 생성
4. Brier / calibration curve
5. regime / strata별 stability
6. 그 뒤에만 shrinkage / hierarchical model 검토
```

가 안전하다.

## 왜 자동 coverage threshold를 두지 않는가

현재 구현은:

```text
coverage < 70% -> unusable
```

같은 임의 threshold를 만들지 않는다.

Dataset, 기간, 채권 발행 여부, issuer type에 따라 attainable coverage가 다르기 때문이다.

대신 raw numerator / denominator를 노출하고, 실제 population을 본 뒤 연구자가 필요한 minimum coverage criterion을 별도 validation protocol로 정하도록 한다.

## 투자 연구에서의 의미

Carvana형 기업을 찾는 로직의 가장 위험한 실패는 모델 수식보다 **보이지 않는 selection bias**일 수 있다.

```text
채권 데이터가 있는 기업만 남음
법원/SEC outcome을 쉽게 검증한 기업만 남음
liquidity feature를 추출하기 쉬운 기업만 남음
```

이런 subset에서 나온 경험률을 전체 distress universe의 확률처럼 쓰면 현실성이 무너진다.

Coverage audit는 그 오류를 가장 먼저 드러내는 계층이다.
