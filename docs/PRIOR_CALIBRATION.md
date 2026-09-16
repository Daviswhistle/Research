# Walk-forward Prior Calibration

## 목적

`distressed-equity-walk-forward-priors`는 target T0에서 실제로 사용할 수 있었던 경험적 prior를 만든다.

그 다음 질문은 별개다.

> 그 당시의 prior는 이후 실제 outcome에 대해 얼마나 잘 calibrated되어 있었는가?

`distressed-equity-prior-calibration`은 여러 historical cutoff를 순차 재생하고, 각 target T0의 ex-ante prior와 이후 source-backed outcome을 비교한다.

```text
historical replay grid
  -> cutoff-safe prior at each later T0
  -> later verified outcomes
  -> Brier score + calibration bins
```

## Ex-ante와 ex-post 분리

예:

```text
2018 T0 case A
2019 outcome A known

2020 T0 case B
  prior uses A only
2021 outcome B known

2022 T0 case C
  prior uses A + B
2023 outcome C known
```

2020 prior를 평가할 때 2021에 알려진 B 자신의 결과를 prior 생성에 사용하지 않는다.

2022 prior에는 A와 B 중 2022 T0까지 실제로 알려진 metric만 들어간다.

Evaluation cutoff는 **사후 평가에 허용할 최신 knowledge date**일 뿐, 과거 prior 생성의 cutoff를 바꾸지 않는다.

## CLI

```bash
distressed-equity-prior-calibration \
  --analysis-date 2018-12-31 \
  --analysis-date 2020-12-31 \
  --analysis-date 2022-12-31 \
  --analysis-date 2024-12-31 \
  --evaluation-cutoff 2026-09-17 \
  --securities-csv securities.csv \
  --prices-csv prices.csv \
  --credit-links-csv credit_links.csv \
  --bond-observations-csv trace_daily.csv \
  --source-outcomes-csv verified_outcomes.csv \
  --survival-features-csv verified_t0_features.csv \
  --output calibration.json \
  --markdown-output calibration.md
```

최소 두 개의 서로 다른 `--analysis-date`가 필요하다.

각 날짜는 한 번만 point-in-time joint replay되고 메모리에서 재사용된다.

## Walk-forward 구성

정렬된 analysis date가:

```text
D1 < D2 < D3 < D4
```

라면:

```text
D2 prior <- D1 history
D3 prior <- D1 + D2 history
D4 prior <- D1 + D2 + D3 history
```

각 target prior는 여전히 다음 규칙을 지킨다.

- historical T0는 target보다 과거여야 함
- source outcome metric은 metric-specific known date가 target T0 이전이어야 함
- target future outcome은 prior에 사용하지 않음
- repeated same-security distress는 connected episode로 collapse
- target과 이어진 같은-security episode는 self-prior에서 제외

## Forecast family를 합치지 않음

한 target에는 여러 prior가 동시에 존재한다.

```text
credit_group
liquidity_runway
nearest_maturity
covenant_headroom
net_leverage
impairment_type
```

이들은 서로 겹치는 조건부 경험률이다.

따라서 calibration에서도:

```text
credit_group / survived_12m
liquidity_runway / survived_12m
nearest_maturity / survived_12m
...
```

를 **별도 forecast family**로 평가한다.

단순 평균·가중평균·곱셈으로 하나의 합성 확률을 만들지 않는다.

## Outcome metric

현재 binary calibration 대상:

```text
survived_12m
existing_common_survival
normalized_within_3y
three_x_3y
```

실제값은 `CsvSourceBackedOutcomeIndex`에서 metric-specific knowledge cutoff를 적용해 가져온다.

Evaluation cutoff까지 아직 알려지지 않은 metric은 평가 분모에 들어가지 않는다.

## Brier score

각 forecast:

```math
Brier_i = (p_i - y_i)^2
```

여기서:

```text
p_i = target T0에서 산출된 raw empirical prior
y_i = 이후 source-backed realized outcome (0 또는 1)
```

Forecast family의 Brier score는 해당 forecast들의 평균 squared error다.

예:

```text
B: p=1.0, outcome=0 -> 1.00
C: p=0.5, outcome=1 -> 0.25

mean Brier = 0.625
```

Brier가 낮을수록 해당 forecast family의 확률 예측이 실제 binary outcome과 가까웠다는 뜻이다.

단, 작은 표본에서 Brier 자체도 불안정할 수 있으므로 forecast count와 calibration denominator를 함께 본다.

## Reliability / calibration bins

기본:

```bash
--calibration-bin-width 0.10
```

예측을:

```text
[0.0, 0.1)
[0.1, 0.2)
...
[0.9, 1.0]
```

로 나눈 뒤 각 bin에서:

```text
forecast_count
mean_predicted_probability
observed_rate
brier_score
```

를 계산한다.

완벽히 calibrated된 모델이라면 충분한 표본에서:

```text
mean predicted ~= observed rate
```

에 가까워져야 한다.

Bin width는 1.0을 정확히 나눌 수 있어야 한다.

예:

```text
0.05
0.10
0.20
0.25
0.50
1.00
```

## Calibration denominator 품질

각 forecast observation은 prior를 만들 때 사용한 calibration population의 진단값을 그대로 보존한다.

```text
calibration_resolved_n
calibration_successes
calibration_wilson_95_low
calibration_wilson_95_high
calibration_small_sample
```

따라서 Brier가 좋아도 실제 prior가 `1/1` 같은 sparse estimate에서 왔는지 구분할 수 있다.

Summary에는:

```text
small_sample_forecast_count
median_calibration_resolved_n
```

도 들어간다.

## JSON output

Top-level:

```text
evaluation_cutoff
analysis_dates
prior_run_count
target_case_count
forecast_observation_count
walk_forward_prior_runs
summaries
observations
semantics
warnings
```

각 summary:

```text
basis
metric
forecast_count
small_sample_forecast_count
mean_predicted_probability
observed_rate
brier_score
median_calibration_resolved_n
bins
```

각 observation은 target case별 raw forecast와 actual, squared error, calibration denominator provenance를 남긴다.

## 해석할 때 하지 말아야 할 것

### 1. 여러 basis Brier를 한 순위로 과대해석

Feature coverage와 표본 구성이 다를 수 있다.

예를 들어 liquidity prior가 더 좋은 Brier를 보여도:

- 더 쉬운 case만 feature가 채워졌을 수 있음
- forecast count가 더 작을 수 있음
- 같은 underlying cases가 중복 평가됨

따라서 forecast count와 coverage를 같이 본다.

### 2. Ex-post outcome을 prior로 역주입

Calibration command는 같은 source outcome 파일을 사용하지만, prior 생성 시점에는 항상 target T0 knowledge cutoff를 적용한다.

Evaluation cutoff는 realized outcome 평가에만 사용한다.

### 3. Sparse 0%/100% prior를 정밀 확률로 취급

Raw prior는 그대로 평가한다.

Small-sample flag와 Wilson interval을 보존하고, 임의 shrinkage를 자동 적용하지 않는다.

## 다음 단계

실제 licensed CRSP + TRACE/WRDS population을 넣으면 다음을 볼 수 있다.

```text
Brier score by prior basis
calibration curve
sample-size bucket별 reliability
credit-stress state별 calibration
liquidity / maturity / covenant strata별 calibration
calendar regime별 stability
```

그 다음에야 empirical-Bayes / hierarchical shrinkage가 정말 개선하는지 walk-forward로 비교할 수 있다.

Shrinkage를 도입하더라도:

- hyperparameter는 calibration/training 과거 구간 안에서만 학습
- future outcome leakage 금지
- raw empirical prior를 항상 함께 보존
- 동일 out-of-sample target에서 Brier/calibration 개선 여부로 평가

를 지켜야 한다.
