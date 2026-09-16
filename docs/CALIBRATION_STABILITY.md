# Calibration Stability Diagnostics

## 목적

전체 Brier score 하나가 좋아도 특정 시기·credit state·표본 크기·T0 stratum에서만 심하게 깨질 수 있다.

예를 들어:

```text
all forecasts Brier = 0.16

2020 target year Brier = 0.08
2022 target year Brier = 0.31
```

이라면 전체 평균만으로는 구조적 불안정성을 놓친다.

`calibration_stability.py`는 이미 생성되고 사후 outcome까지 확인된 **out-of-sample forecast observation을 재집계**한다.

새 확률 모델을 만들지 않고 기존 raw empirical prior를 그대로 진단한다.

## 가장 중요한 경계

다음은 하지 않는다.

```text
credit prior + liquidity prior -> 합성 확률

small-n prior -> 임의 shrinkage

2020/2021 -> “COVID regime” 자동 명명

stability 결과 -> ex-ante forecast 재학습
```

각 forecast는 원래의:

```text
basis
metric
```

family 안에 그대로 남는다.

Credit-group prior와 liquidity prior가 같은 case를 예측했더라도 독립 예측처럼 합치지 않는다.

## 지원 slice dimensions

기본값:

```text
target_date
target_year
credit_group
calibration_n_band
basis_bucket
```

CLI에서는:

```bash
--stability-dimensions target_date,target_year,credit_group,calibration_n_band,basis_bucket
```

으로 선택할 수 있다.

### target_date

각 historical target T0별 stability를 본다.

### target_year

calendar year 단위로 묶는다.

이것은 단순한 deterministic calendar partition이다.

```text
2020 != 자동으로 “COVID regime”
2022 != 자동으로 “rate-hike regime”
```

경제 regime 이름은 별도 외부 evidence/model이 있을 때만 붙인다.

### credit_group

각 forecast family 안에서:

```text
fresh_credit_stress
fresh_credit_no_stress
no_fresh_credit
```

상태별 realized calibration을 비교한다.

### calibration_n_band

각 forecast가 만들어질 당시 historical resolved denominator를 고정 구간으로 나눈다.

```text
1-4
5-9
10-29
30-99
100+
```

이 구간은 descriptive partition이다. 자동으로 usable/unusable 판정을 내리지 않는다.

### basis_bucket

원래 prior가 사용한 T0 bucket별 stability를 본다.

예:

```text
basis = liquidity_runway
bucket = <6m
bucket = 6-<12m
bucket = 12-<24m
```

다른 basis의 bucket은 서로 합치지 않는다.

## Slice output

각 slice는 다음을 보존한다.

```text
dimension
value
basis
metric
forecast_count
unique_target_date_count
unique_security_count
small_sample_forecast_count
mean_predicted_probability
observed_rate
calibration_gap
absolute_calibration_gap
brier_score
median_calibration_resolved_n
min_calibration_resolved_n
max_calibration_resolved_n
```

## Calibration gap

정의:

```text
calibration_gap = observed_rate - mean_predicted_probability
```

따라서:

```text
gap > 0 -> 해당 slice에서 평균적으로 underprediction
gap < 0 -> 해당 slice에서 평균적으로 overprediction
```

이다.

이 값도 표본 수와 함께 읽어야 한다.

```text
forecast_count = 2
gap = +40%p
```

를 안정적인 구조적 신호로 해석하지 않는다.

## Brier score

각 slice에서 기존 forecast observation의 squared error를 그대로 평균낸다.

```text
Brier = mean((p - y)^2)
```

Probability를 다시 fit하거나 recalibrate하지 않는다.

## CLI 통합

`distressed-equity-prior-calibration` JSON에는 이제:

```text
stability
```

section이 추가된다.

예:

```bash
distressed-equity-prior-calibration \
  --analysis-date 2018-12-31 \
  --analysis-date 2020-12-31 \
  --analysis-date 2022-12-31 \
  --evaluation-cutoff 2026-09-17 \
  ...
```

출력 구조:

```text
summaries            -> basis/metric 전체 calibration
reliability bins     -> probability bin calibration
stability.slices     -> date/year/credit/n/bucket별 stability
```

Markdown에도 `Stability slices` 표가 포함된다.

## 어떻게 읽을 것인가

실제 population에서 우선 다음을 본다.

```text
1. 전체 basis/metric Brier
2. target_date / target_year stability
3. credit_group stability
4. calibration_n_band stability
5. basis_bucket stability
6. 각 slice forecast_count / unique dates / unique securities
```

예를 들어:

```text
liquidity_runway / survived_12m

12-<24m bucket:
  n forecasts = 120
  Brier = 0.12

<6m bucket:
  n forecasts = 9
  Brier = 0.38
```

이면 `<6m` prior가 나쁘다고 즉시 결론내리는 것이 아니라:

- 표본이 매우 작은가
- 특정 target date에 몰렸는가
- 특정 credit state에만 존재하는가
- source-outcome coverage가 다르게 형성됐는가

를 함께 본다.

## Coverage audit와의 관계

Stability는 `POPULATION_COVERAGE.md` 다음 단계다.

```text
coverage / censoring 문제
    -> 먼저 해결 또는 명시

그 뒤
    -> calibration stability 평가
```

Outcome coverage가 선택적으로 낮은 slice에서 좋은 Brier가 나온다면 selection bias 가능성을 먼저 의심한다.

## Shrinkage 전 단계

Hierarchical / empirical-Bayes shrinkage는 이 stability audit 이후에만 검토한다.

필요한 질문:

```text
raw prior의 불안정성이 실제 small-n 때문인가?
특정 calendar period에만 깨지는가?
특정 credit state에만 깨지는가?
특정 T0 bucket 정의가 잘못됐는가?
```

이 질문을 먼저 답하지 않고 shrinkage를 넣으면 구조적 misspecification을 단순 smoothing으로 가릴 수 있다.
