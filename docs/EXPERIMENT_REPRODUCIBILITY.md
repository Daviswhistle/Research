# Experiment Reproducibility Manifest

## 목적

실제 CRSP/TRACE population 실험에서 가장 위험한 운영 오류 중 하나는 서로 다른 데이터·threshold·코드로 돌린 결과를 같은 실험처럼 비교하는 것이다.

예:

```text
prices.csv가 한 행 수정됨
min_drawdown 0.60 -> 0.65
credit staleness 30d -> 60d
코드 revision 변경
```

이 차이를 기록하지 않으면 Brier/coverage 변화가 모델 때문인지 데이터 때문인지 구분하기 어렵다.

`experiment_manifest.py`는 population coverage와 prior calibration 실행에 입력 파일 내용·normalized config·code revision을 묶은 fingerprint를 붙인다.

## 두 fingerprint

### data_config_fingerprint

항상 계산한다.

포함:

```text
schema version
pipeline name
input role + file byte size + SHA-256
normalized analysis config
```

파일 시스템 경로는 제외한다.

따라서 같은 bytes를 다른 디렉터리/파일명으로 옮겨도 fingerprint는 같다.

### experiment_fingerprint

다음이 확인될 때만 계산한다.

```text
data_config_fingerprint
+ exact code revision
```

Code revision을 찾지 못하면:

```text
reproducibility_complete = false
experiment_fingerprint = null
```

로 둔다.

코드 revision이 불명인데도 exact experiment identity를 발급하지 않는다.

## Code revision 탐색 순서

```text
1. --code-revision
2. GITHUB_SHA environment variable
3. local git rev-parse HEAD
4. unresolved
```

실제 보존용 population run에서는 명시적으로:

```bash
--code-revision <commit-sha>
```

를 넣는 것을 권장한다.

## Input file identity

각 file은 다음을 기록한다.

```text
role
path
size_bytes
sha256
```

`path`는 사람이 audit하기 위한 표시값이다.

Canonical fingerprint에는:

```text
role
size_bytes
sha256
```

만 들어간다.

따라서:

```text
/data/run1/prices.csv
/home/user/copy/prices-renamed.csv
```

가 byte-identical하고 같은 role이면 동일 input으로 취급한다.

반대로 한 byte라도 바뀌면 fingerprint가 달라진다.

## Population coverage manifest

`population_coverage_cli`는 다음 6개 input role을 해시한다.

```text
securities
prices
credit_links
bond_observations
source_outcomes
survival_features
```

Config identity에는 다음이 포함된다.

```text
analysis_dates
outcome_coverage_cutoff
exchanges
min_drawdown
min_price
lookback_years
max_securities
credit_lookback_days
credit_max_staleness_days
credit_price_below
credit_yield_at_or_above
credit_spread_at_or_above
```

JSON output:

```text
experiment_manifest
```

Markdown header에도 experiment/data-config fingerprint와 code revision이 표시된다.

## Prior calibration manifest

`prior_calibration_cli`도 같은 6개 input role을 해시한다.

추가 config identity:

```text
evaluation_cutoff
feature_dimensions
stability_dimensions
episode_gap_days
small_sample_n
calibration_bin_width
```

즉 feature strata나 reliability-bin 폭이 바뀌어도 다른 experiment identity가 된다.

## Canonicalization

Fingerprint용 JSON은:

```text
UTF-8
sorted keys
compact separators
NaN/Infinity disallowed
```

로 직렬화한다.

Input role도 정렬하므로 CLI argument 순서가 fingerprint에 영향을 주지 않는다.

Config list처럼 순서 자체에 의미가 있는 값은 순서를 보존한다.

예를 들어 `feature_dimensions` 순서를 바꾸면 다른 config identity로 취급한다.

## 무엇을 보장하지 않는가

Manifest는 다음을 자동 보장하지 않는다.

```text
licensed dataset의 법적 entitlement
upstream vendor의 내부 생성 과정
OS / Python / dependency lock 전체 환경
외부 API response의 장기 재현성
```

현재 목적은 repository의 population research 결과끼리 최소한 다음을 정확히 구분하는 것이다.

```text
같은 input bytes인가?
같은 research config인가?
같은 code revision인가?
```

필요해지면 차기 단계에서 dependency/environment lock fingerprint를 추가할 수 있다.

## 권장 실험 보존 방식

실제 population run은 결과 JSON/Markdown과 함께 다음을 보존한다.

```text
experiment_manifest
coverage report
calibration report
source input files 또는 vendor snapshot identifiers
commit SHA
```

같은 `experiment_fingerprint`가 아니면 결과 숫자를 직접 전후 비교할 때 먼저 변경 원인을 확인한다.

## 왜 투자 연구에 중요한가

Distress 연구는 small changes에 민감하다.

```text
survivorship-safe universe revision
corporate-action adjusted-price revision
TRACE correction handling change
one outcome label added
T0 feature bucket rule changed
```

이런 변경이 base rate와 Brier를 바꿀 수 있다.

Reproducibility manifest는 “결과가 달라졌다”를 곧바로 투자 논리 변화로 오해하지 않고, 먼저 데이터/config/code change인지 추적하게 하는 provenance 계층이다.
