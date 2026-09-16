# Walk-forward empirical priors for distress research

## 목적

사후적으로 2022년 distress cohort의 2025년 결과를 붙여 base rate를 계산하는 것과, **2022년 12월 31일 당시 실제로 사용할 수 있었던 prior**를 계산하는 것은 다른 문제다.

이 문서는 두 시간축을 분리한다.

```text
ex-post evaluation
  T0 cohort
    -> future outcomes
    -> "이 전략/조건이 실제로 어떻게 끝났나?"

walk-forward prior
  historical T0 cases only
    + outcomes already known by target T0
    -> "target T0 당시 경험적으로 무엇을 알 수 있었나?"
```

`distressed-equity-walk-forward-priors`는 두 번째 문제만 다룬다.

## 핵심 anti-look-ahead 규칙

Target T0가 2022-12-31이라면 calibration case는 다음 조건을 모두 만족해야 한다.

```text
historical analysis_date < 2022-12-31
outcome_known_date <= 2022-12-31
```

따라서 2019년에 발생한 distress라도 결과가 2023년에야 확인됐다면 2022 T0 prior에는 들어가지 않는다.

Target cohort 자체의 미래 outcome도 prior에 들어가지 않는다.

## 같은 distress episode 중복 방지

한 기업이 1년 이상 distress 상태에 있으면 여러 historical replay date에서 같은 기업이 반복 포착될 수 있다.

그대로 세면 한 episode가 여러 독립 사례처럼 가중된다.

기본:

```text
episode_gap_days = 365
```

동일 permanent `security_id`의 반복 관측이 이 간격 안에 있으면 historical calibration 전에 collapse한다.

또한 target case와 같은 `security_id`의 과거 관측이 target T0로부터 episode gap 안에 있으면, 그 과거 관측은 해당 target case의 prior에서 제외한다.

즉 ongoing distress episode가 자기 자신의 prior가 되는 것을 막는다.

## 입력

### 1. Point-in-time equity security master

기존 `CsvMarketProvider` schema:

```text
security_id
symbol
name
start_date
end_date
```

Permanent security identity가 핵심이다. Ticker로 episode를 연결하지 않는다.

### 2. Equity prices

```text
security_id
date
close
adjusted_close
```

각 historical T0와 target T0는 자기 cutoff 이전 row만 사용한다.

### 3. Point-in-time equity ↔ debt links

```text
security_id
cusip 또는 isin
start_date
end_date
```

Historical T0에 active한 credit identity만 사용한다.

### 4. Normalized bond observations

`distressed-equity-trace-normalize` 또는 동등한 daily-normalized corporate-credit history를 사용한다.

### 5. Verified source-backed outcomes

`docs/SOURCE_BACKED_DISTRESS_OUTCOMES.md`의 검증 schema를 사용한다.

Walk-forward에서 가장 중요한 필드는:

```text
analysis_date
outcome_known_date
verification_status=verified
evidence_refs
verifier
verified_on
evidence_reopened=true
```

### 6. Verified T0 survival features

`docs/SURVIVAL_FEATURE_STRATA.md` schema를 사용한다.

```text
liquidity_runway_months
nearest_maturity_months
covenant_headroom_pct
net_leverage
impairment_type
```

각 feature는 자기 historical T0 이전에 알려진 증거만 허용한다.

## CLI

예를 들어 2022-12-31 target에 대해 2018, 2019, 2020, 2021 연말 cohort를 calibration history로 쓰려면:

```bash
distressed-equity-walk-forward-priors \
  --analysis-date 2022-12-31 \
  --prior-analysis-date 2018-12-31 \
  --prior-analysis-date 2019-12-31 \
  --prior-analysis-date 2020-12-31 \
  --prior-analysis-date 2021-12-31 \
  --securities-csv crsp_like_security_master.csv \
  --prices-csv crsp_like_prices.csv \
  --credit-links-csv security_to_bond_membership.csv \
  --bond-observations-csv trace_daily.csv \
  --source-outcomes-csv verified_outcomes.csv \
  --survival-features-csv verified_t0_features.csv \
  --output priors_2022-12-31.json \
  --markdown-output priors_2022-12-31.md
```

Historical dates는 target보다 반드시 이전이어야 한다.

## 생성되는 prior

Target case마다 두 층을 만든다.

### Credit-group prior

```text
fresh_credit_stress
fresh_credit_no_stress
no_fresh_credit
```

Target과 같은 credit evidence group의 과거 사례를 calibration population으로 쓴다.

### Feature-stratum priors

기본 dimension:

```text
liquidity_runway
nearest_maturity
covenant_headroom
net_leverage
impairment_type
```

예:

```text
target:
  credit_group = fresh_credit_stress
  liquidity_runway = 12-<24m

prior population:
  historical fresh_credit_stress
  AND historical liquidity_runway = 12-<24m
```

## 각 prior가 보존하는 것

각 outcome metric은 단순 point estimate만 내지 않는다.

```text
successes
resolved_n
rate
Wilson 95% interval
small_sample flag
```

Outcome metric:

```text
company survived 12m
existing common survived 12m
normalized within 3y
3x within 3y
```

예:

```text
1 / 1 = 100%
```

이라도 output은 대략 다음 의미를 함께 보존한다.

```text
rate = 100%
resolved_n = 1
Wilson interval = very wide
small_sample = true
```

따라서 100%라는 숫자 자체를 정밀한 확률로 오해하지 않는다.

## Small-sample threshold

기본:

```bash
--small-sample-n 30
```

`resolved_n < 30`이면 small-sample flag를 켠다.

이 숫자는 통계적 진리나 투자 임계치가 아니라 **검토 경고 기준**이다.

## 여러 prior를 합치는 법

중요: credit-group prior와 feature prior들은 서로 독립이 아니다.

예를 들어:

```text
credit stress prior
liquidity-runway prior
maturity prior
covenant prior
```

를 단순 평균하거나 서로 곱하면 안 된다.

이들은 현재 단계에서 **겹치는 조건부 경험률의 여러 관점**이다.

표본이 충분히 커지기 전에는:

1. raw denominator와 interval을 본다.
2. target과 가장 직접적으로 유사한 strata를 본다.
3. 서로 다른 strata가 같은 방향인지 확인한다.
4. sparse strata의 극단적 rate를 자동 결론으로 사용하지 않는다.

## 왜 아직 empirical-Bayes shrinkage를 기본값으로 쓰지 않는가

Sparse strata를 전체 평균 쪽으로 shrink하는 것은 유용할 수 있다.

하지만 prior strength를 임의로 정하면 또 다른 주관적 숫자가 생긴다.

현재 기본 출력은:

```text
raw empirical rate
+ denominator
+ Wilson interval
+ small-sample warning
```

을 그대로 보존한다.

추후 실제 population이 충분히 커지면, training/calibration period 안에서만 hyperparameter를 추정하는 hierarchical / empirical-Bayes 모델을 별도 계층으로 추가할 수 있다. 그 경우에도 raw empirical 결과는 삭제하지 않는다.

## Ex-post evaluation과 혼동하지 않기

`distressed-equity-credit-replay --outcome-cutoff ...`가 만드는 future outcome/base-rate 표는 **사후 평가**다.

`distressed-equity-walk-forward-priors`가 만드는 값은 **target T0 시점의 ex-ante prior**다.

두 값을 같은 필드로 합치지 않는다.

```text
ex-ante prior:
  당시 투자자가 이용 가능했던 경험적 정보

ex-post result:
  실제로 이후 무슨 일이 벌어졌는지
```

이 둘의 차이가 실제 calibration error다.

## 다음 단계

충분한 역사 population이 쌓이면 각 target T0마다:

```text
walk-forward prior
  vs
realized source-backed outcome
```

을 저장해 다음을 측정할 수 있다.

```text
Brier score
calibration curve
reliability by sample size
credit-stress / liquidity / maturity strata별 calibration
```

그 단계가 되어야 "시장이 암시한 실패확률보다 현실의 실패확률이 낮았다"는 전략을 population level에서 정량적으로 검증할 수 있다.
