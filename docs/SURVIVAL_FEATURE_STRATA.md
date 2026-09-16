# Point-in-Time Survival Feature Strata

## 목적

`equity distress + credit distress`만으로는 실제 실패위험 차이를 충분히 설명하지 못한다.

예를 들어 둘 다 채권이 60 cents인 기업이라도:

```text
A: liquidity runway 3개월 / 만기 6개월 / covenant breach
B: liquidity runway 30개월 / 만기 48개월 / covenant headroom 40%
```

은 전혀 다른 생존 집단이다.

이 layer는 T0에서 이미 알려져 있던 source-backed survival feature를 joint replay cohort에 붙이고, verified legal/business outcome과 교차해 조건부 경험률을 계산한다.

## 입력 원칙

Repository는 여기서 liquidity runway, covenant headroom, net leverage를 새로 추정하지 않는다.

```text
upstream source-backed calculation
    -> verified feature snapshot
    -> deterministic descriptive bucket
    -> source-backed outcome rate
```

즉 회사별 covenant 정의를 무시하고 generic formula를 적용하지 않는다.

## CSV schema

`--survival-features-csv` 필수 컬럼:

```text
security_id
analysis_date
evidence_known_date
liquidity_runway_months
nearest_maturity_months
covenant_headroom_pct
net_leverage
impairment_type
evidence_refs
verification_status
verifier
verified_on
evidence_reopened
```

선택:

```text
notes
```

Feature 값 자체는 blank일 수 있다. 단 한 row에는 최소 하나의 feature가 resolved여야 한다.

예:

```csv
security_id,analysis_date,evidence_known_date,liquidity_runway_months,nearest_maturity_months,covenant_headroom_pct,net_leverage,impairment_type,evidence_refs,verification_status,verifier,verified_on,evidence_reopened,notes
12345,2022-12-31,2022-12-29,18,30,15,5.5,temporary,SEC:10-Q:001;CREDIT:001,verified,reviewer,2026-01-03,true,reviewed T0 features
```

## Anti-look-ahead boundary

가장 중요한 규칙:

```text
evidence_known_date <= analysis_date
```

2023년에 처음 공개된 refinancing이나 covenant waiver를 2022-12-31 liquidity/headroom에 소급해서 넣지 않는다.

`verified_on`은 오늘 리뷰한 날짜여도 괜찮다. 중요한 것은 feature를 뒷받침하는 evidence가 T0에 이미 알려져 있었는가다.

## Verification boundary

다음을 모두 요구한다.

```text
verification_status = verified
verifier != blank
verified_on != blank
evidence_reopened = true
evidence_refs != blank
```

검증 provenance가 없는 숫자는 strata base-rate에 들어가지 않는다.

## Feature definitions

### liquidity_runway_months

Upstream에서 검증된 liquidity runway의 개월 수다.

Repository는 이 값의 계산식을 여기서 강제하지 않는다. 기존 distressed-equity engine처럼 cash, revolver availability, mandatory payments, burn/FCF assumptions을 어떤 기준으로 사용했는지는 upstream evidence에 남아 있어야 한다.

### nearest_maturity_months

T0에서 가장 가까운 material debt maturity까지의 개월 수.

Springing maturity / mandatory repayment / refinancing dependency를 어떤 방식으로 반영했는지는 upstream source가 책임진다.

### covenant_headroom_pct

Upstream에서 검증된 covenant headroom의 **percentage-point descriptive value**.

```text
< 0   = breach / negative headroom
0~10  = thin headroom
10~25 = moderate headroom
>=25  = larger headroom
```

회사별 covenant 정의가 다르므로 Repository가 generic EBITDA나 leverage definition을 다시 계산하지 않는다.

### net_leverage

Upstream verified net leverage multiple.

Net cash 기업은 음수가 가능하므로 non-negative constraint를 걸지 않는다.

### impairment_type

T0에서 분류한 impairment type 문자열. 예:

```text
temporary
permanent
cyclical
execution
liquidity
regulatory
```

Repository는 임의 taxonomy를 강제하지 않는다. 소문자 normalized string을 그대로 strata label로 사용한다.

## Deterministic descriptive buckets

### Liquidity runway

```text
<6m
6-<12m
12-<24m
>=24m
unknown
```

### Nearest maturity

```text
<12m
12-<24m
24-<36m
>=36m
unknown
```

### Covenant headroom

```text
breach/<0%
0-<10%
10-<25%
>=25%
unknown
```

### Net leverage

```text
<2x
2-<4x
4-<6x
>=6x
unknown
```

이 경계들은 **설명용 strata**다. 보편적 인과 threshold라고 주장하지 않는다.

## Credit group과 교차

각 feature bucket은 credit evidence group과 함께 집계한다.

```text
fresh_credit_stress
fresh_credit_no_stress
no_fresh_credit
```

따라서 다음처럼 읽을 수 있다.

```text
liquidity_runway=<6m + fresh_credit_stress
liquidity_runway=12-<24m + fresh_credit_stress
covenant_headroom=breach/<0% + fresh_credit_stress
```

## Outcome denominator

Legal/business outcome은 `SOURCE_BACKED_DISTRESS_OUTCOMES.md`의 verified label만 사용한다.

각 stratum은 다음을 모두 보존한다.

```text
cohort_case_count
feature_snapshot_count
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

즉:

```text
cohort 20개
feature snapshot 18개
verified outcome 7개
survival 6/7
```

이라면 6/20으로 계산하지도 않고, 13개 missing outcome을 실패로 넣지도 않는다.

## Unknown bucket

Feature snapshot이 없거나 특정 feature만 비어 있으면 `unknown` bucket에 남긴다.

Missing feature를 좋은 상태나 나쁜 상태로 자동 분류하지 않는다.

이 bucket 자체가 dataset coverage를 보여주는 중요한 신호다.

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
  --survival-features-csv verified_t0_features.csv \
  --output cohort.json \
  --markdown-output cohort.md
```

Dimensions를 제한하려면:

```bash
--feature-strata-dimensions liquidity_runway,nearest_maturity,covenant_headroom
```

`--survival-features-csv`는 verified outcome 없이 단독으로 base-rate를 만들 수 없다. 현재 CLI는 `--source-outcomes-csv`와 `--outcome-cutoff`를 요구한다.

## 투자 연구에서의 의미

이제 경험률을 다음 순서로 좁혀갈 수 있다.

```text
P(common survives | equity distress)

-> P(common survives | equity distress, fresh credit stress)

-> P(common survives |
     equity distress,
     fresh credit stress,
     liquidity runway 12-24m)

-> P(common survives |
     equity distress,
     fresh credit stress,
     liquidity runway 12-24m,
     no covenant breach,
     impairment type=temporary)
```

현재 구현은 1차원 feature strata × credit group까지 자동 계산한다. 여러 feature를 동시에 교차하는 고차원 strata는 표본 수가 급격히 줄어드므로 기본값으로 만들지 않는다.

다음 단계에서 충분한 실제 population이 확보되면 minimum sample-size guard, empirical-Bayes shrinkage, hierarchical calibration을 추가할 수 있다.
