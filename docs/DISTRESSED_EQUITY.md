# Distressed Equity Convexity Engine

## 목적

이 도구는 단순 저평가주를 찾지 않는다. 목표는 다음 두 조건이 동시에 성립하는 후보를 구조적으로 걸러내는 것이다.

1. 시장이 암시하는 실패확률이 현실의 실패확률보다 높을 가능성이 있다.
2. 생존·정상화 시 **기존 보통주**의 회수배수가 현재 가격 대비 비대칭적으로 크다.

Carvana 2022의 업종·주가하락률 같은 표면적 특징을 복제하지 않는다. 핵심 메커니즘은 `real business + distressed equity + survival path + large residual upside`다.

## 설계 원칙

### 계산과 판단을 분리한다

결정론적 엔진이 계산하는 것:

- Time to Death
- 희석 후 기존주주 가치
- 시나리오별 equity multiple
- target CAGR을 만족시키는 Required Probability
- Common Equity Capture Ratio
- Critical Assumption 수
- recovery 전 cash debt payment의 liquidity impact
- recovery 전 refinancing/covenant blocker의 존재

에이전트가 맡는 것:

- 구조적 훼손 vs 순환적 훼손
- 누락된 covenant/refinancing/senior claim 검증
- 정상화 economics 반증
- 현실적 성공확률 **범위**의 근거 수집
- lease/SBC/희석 이중계산 검증
- historical/base-rate analogue 탐색

에이전트는 임의의 성공확률을 생성해 엔진을 덮어쓰지 않는다.

### Missing data를 좋은 뉴스로 해석하지 않는다

Universe scanner는 `PASS / FAIL / RESEARCH / UNKNOWN`을 구분한다.

- `PASS`: 해당 gate를 통과할 근거가 입력되어 있다.
- `FAIL`: 하드 필터를 명백히 통과하지 못한다.
- `RESEARCH`: refinancing/covenant 등 해결해야 할 조건이 있다.
- `UNKNOWN`: 필요한 point-in-time 데이터가 아직 없다.

`UNKNOWN`을 0이나 PASS로 치환하지 않는다. 특히 critical assumptions 목록이 완성되지 않았다면 0개로 계산해 후보를 좋게 보이게 하지 않는다.

## 단일 기업 정밀 엔진

### 1. Time to Death

월별 현금흐름과 강제지급을 누적해 유동성이 0 아래로 내려가는 최초 월을 찾는다.

```text
available liquidity
= starting liquidity
+ available credit
+ realizable asset monetization
- restricted cash

liquidity[t]
= liquidity[t-1]
+ FCF[t]
- mandatory cash payment[t]
```

`recovery_month`보다 먼저 소진되면 기본적으로 PASS(투자 후보 탈락)다. 입력한 현금흐름 전망 자체가 recovery window보다 짧으면 생존 여부는 `unresolved`로 둔다.

Debt maturity는 두 종류를 구분한다.

- `cash_payment_required=true`: liquidity에서 실제 차감한다.
- `refinancing_required=true`: refinancing 성공을 자동 가정하지 않고 recovery 전이면 생존을 `REVIEW`로 남긴다.

Covenant도 `breach_month_if_unremedied`가 recovery 전이면 자동 생존으로 처리하지 않는다.

### 2. 기존 common shareholder 가치

```text
Total Equity = max(EV - Net Debt - Senior Claims, 0)
New Shares = Capital Raised / Issue Price
Existing Holder Fraction = Old Shares / (Old Shares + New Shares)
Existing Common Value = Total Equity * Existing Holder Fraction
```

회사 생존과 기존 보통주 생존을 구분한다.

### 3. Required Probability

선택한 downside와 success 시나리오의 회수배수를 이용한다.

```text
Target Multiple = (1 + target CAGR) ^ years
Required Probability =
    (Target Multiple - Downside Multiple)
    / (Success Multiple - Downside Multiple)
```

이 값은 예측이 아니라 **현재 가격이 요구하는 허들**이다. downside만으로 목표수익률을 넘으면 0%로 둔다.

### 4. Common Equity Capture Ratio

기본값은 weak/reference → base 변화에서 계산한다.

```text
ΔExisting Common Value / ΔEnterprise Value
```

부채 감소가 동시에 일어나면 1을 넘을 수 있다. 따라서 사업가치 상승분 중 common이 얼마나 가져가는지 볼 때 자본구조 레버리지까지 함께 드러난다.

### 5. Critical Assumptions

각 시나리오의 `critical_assumptions`를 사람이 명시한다. 독립적인 가정을 여러 개 동시에 맞혀야 하는 후보를 조기에 식별하기 위한 장치다.

`base multiple / assumption count`도 계산할 수 있지만 정밀한 통계 점수로 해석하지 않는다.

## Universe scanner

`distressed-equity-screen`은 정밀 모델을 모든 상장사에 돌리는 대신 값싼 1차 gate로 조사량을 줄인다.

```text
Distressed universe
  ↓
Real business gate
  ↓
Time-to-Death / recovery gate
  ↓
Base existing-common payoff gate
  ↓
Critical-assumption completeness/burden
  ↓
Required Probability + Common Equity Capture
  ↓
DEEP_DIVE_CANDIDATE / RESEARCH / DROP
```

기본 하드 필터는 다음과 같다.

- distress: peak market cap 대비 60% 이상 하락 또는 equity/EV가 충분히 얇음
- real business: pre-revenue가 아니고 실제 매출/고객 근거가 존재
- survival: recovery 전에 liquidity exhaustion이 없어야 함
- base payoff: 기존주주 기준 3배 이상
- critical assumptions: 목록이 완성되어 있고 기본 2개 이하이면 가장 강한 후보

수치는 CLI 옵션이나 `ScreeningConfig`로 바꿀 수 있다. 이것은 통계적으로 최적화된 임계값이 아니라 현재 연구 철학을 코드로 명시한 초기값이다.

### 왜 종합점수를 만들지 않는가

CABO형 후보는 payoff가 크지만 refinancing/구조적 훼손이 클 수 있고, 다른 후보는 payoff는 작지만 생존이 쉬울 수 있다. 이를 한 점수로 상쇄하면 중요한 실패 경로가 가려진다. 따라서 결과표에 다음 다섯 값을 그대로 노출한다.

1. Time to Death
2. Base-case Equity Multiple
3. Required Probability
4. Critical Assumption Count
5. Common Equity Capture Ratio

그리고 `distress / real business / survival / base payoff / assumption` gate를 별도 열로 보존한다.

## 에이전트 구조

단일 기업은 `distressed_equity.agents.build_research_tasks()`가 다섯 개 검증 태스크를 만든다.

1. `survival_auditor`
2. `impairment_classifier`
3. `normalization_critic`
4. `probability_calibrator`
5. `model_verifier`

Universe scanner는 `build_screening_tasks()`로 **미해결 gate만** 조사한다. 예를 들어 recovery 전 debt wall이 있는 후보에는 survival/capital-stack auditor를 추가하고, 이미 하드 탈락한 후보에는 불필요한 probability research를 하지 않는다.

`AgentRunner`는 Protocol만 정의한다. 특정 LLM SDK에 저장소를 묶지 않으며 davis-agent-kit이나 다른 harness에서 주입할 수 있다.

### Agent guardrails

- 사실 / 전망 / 추론 / 미확인을 구분한다.
- 중요한 주장마다 source, publication date, event/effective date, evidence type을 남긴다.
- historical replay에서는 `analysis_date` 이후 공개된 정보를 사용하지 않는다.
- point probability를 정성 근거에서 만들어내지 않는다.
- 반증 자료를 적극적으로 찾는다.
- base rate는 anchor로만 쓰고 기업별 인과분석을 대체하지 않는다.

`--tasks-output`은 이 태스크들을 JSON manifest로 내보내므로 외부 agent harness가 그대로 실행할 수 있다.

## Point-in-time evidence guard

`distressed_equity.evidence.EvidenceRecord`는 주장별 공개일과 event date를 기록한다. `validate_point_in_time()`은 분석일 뒤 공개된 자료가 historical replay에 섞이면 violation을 반환한다.

이 레이어는 현재 최소 구현이다. 향후 SEC/DART/IR/채권 데이터 수집기가 evidence ledger를 채우도록 확장한다.

## 입력

### 단일 기업

`examples/distressed_equity_case.json`을 복사해 사례별로 수정한다.

중요한 원칙:

- 모든 금액 단위는 한 사례 안에서 통일한다.
- `monthly_free_cash_flow`는 이미 interest, maintenance capex, cash lease 등 선택한 회계정책을 일관되게 반영해야 한다.
- lease를 FCF에서 비용 처리했다면 EV bridge에서 다시 debt처럼 차감하지 않는다.
- SBC를 현금흐름에서 가산하더라도 예상 희석은 주식 수에 반영한다.
- `reasonable_probability_low/high`는 조사 결과이며 엔진이 자동 생성하지 않는다.
- refinancing을 성공했다고 가정해 liquidity를 부풀리지 않는다. 필요한 만기는 `debt_obligations`에 명시한다.

### Universe

`examples/distressed_equity_universe.jsonl`은 세 가지 의도적 예시를 포함한다.

- 높은 base payoff + 긴 runway → `DEEP_DIVE_CANDIDATE`
- 더 높은 payoff지만 recovery 전 refinancing → `RESEARCH`
- 생존은 쉬워도 base payoff < 3x → `DROP`

실제 데이터가 아니라 동작을 설명하기 위한 illustrative input이다.

## 실행

단일 기업:

```bash
distressed-equity examples/distressed_equity_case.json -o output/case.md
```

Universe:

```bash
distressed-equity-screen examples/distressed_equity_universe.jsonl \
  -o output/universe.md \
  --json-output output/universe.json \
  --tasks-output output/research_tasks.json
```

주요 스크리너 옵션:

```text
--min-drawdown 0.60
--min-base-multiple 3.0
--max-critical-assumptions 2
--runway-months 60
```

## 판정

단일 기업:

- `PASS`: recovery 전에 liquidity가 소진되거나 선택한 성공 경로로도 요구수익률을 달성하기 어렵다.
- `DEEP DIVE`: 보수적인 현실적 성공확률 하단이 Required Probability를 넘는다.
- `REVIEW`: 확률 우위 또는 recovery 전 refinancing/covenant가 아직 해결되지 않았다.

Universe:

- `DROP`: real business / distress / survival / base payoff 중 하드 gate를 실패하거나 target return 자체가 불가능하다.
- `RESEARCH`: 자료가 미완성이거나 refinancing/covenant/assumption 문제가 남아 있다.
- `DEEP_DIVE_CANDIDATE`: 핵심 하드 gate를 모두 통과하고 assumption burden도 낮다. 이후 probability calibration이 필요하다.

이 판정은 매수·매도 신호가 아니라 연구 우선순위다.

## 다음 단계

1. SEC/DART/회사 IR/채권가격 수집기를 evidence ledger에 연결
2. unit economics template을 업종별 adapter로 분리
3. 과거 distress 사례를 point-in-time으로 재생하는 base-rate 라이브러리 구축
4. agent 결과를 구조화 JSON으로 받아 evidence ledger와 probability range proposal을 자동 생성
5. 실제 글로벌 universe 데이터 provider를 연결해 `screen → evidence → deep dive → memo`를 완전 자동화
6. historical replay에서 look-ahead contamination과 survivorship bias를 검사하는 백테스트 harness 추가
