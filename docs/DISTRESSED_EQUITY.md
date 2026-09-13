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

에이전트가 맡는 것:

- 구조적 훼손 vs 순환적 훼손
- 누락된 covenant/refinancing/senior claim 검증
- 정상화 economics 반증
- 현실적 성공확률 **범위**의 근거 수집
- lease/SBC/희석 이중계산 검증

에이전트는 임의의 성공확률을 생성해 엔진을 덮어쓰지 않는다.

## 핵심 계산

### 1. Time to Death

월별 현금흐름과 강제지급을 누적해 유동성이 0 아래로 내려가는 최초 월을 찾는다.

```text
liquidity[t] = liquidity[t-1] + FCF[t] - mandatory_payment[t]
```

`recovery_month`보다 먼저 소진되면 기본적으로 PASS다. 단, 입력한 현금흐름 전망 자체가 recovery window보다 짧으면 생존 여부는 `unresolved`로 둔다.

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

기본값은 weak → base 변화에서 계산한다.

```text
ΔExisting Common Value / ΔEnterprise Value
```

부채 감소가 동시에 일어나면 1을 넘을 수 있다. 따라서 사업가치 상승분 중 common이 얼마나 가져가는지 볼 때 자본구조 레버리지까지 함께 드러난다.

### 5. Critical Assumptions

각 시나리오의 `critical_assumptions`를 사람이 명시한다. 독립적인 가정을 여러 개 동시에 맞혀야 하는 후보를 조기에 식별하기 위한 장치다.

`base multiple / assumption count`도 계산할 수 있지만 정밀한 통계 점수로 해석하지 않는다.

## 에이전트 구조

`distressed_equity.agents.build_research_tasks()`는 다섯 개 검증 태스크를 만든다.

1. `survival_auditor` — 만기, covenant, springing maturity, revolver, 강제지급, senior claims
2. `impairment_classifier` — temporary vs permanent impairment
3. `normalization_critic` — volume × price − variable cost − fixed cost 관점에서 정상화 공격
4. `probability_calibrator` — point probability가 아니라 방어 가능한 범위와 근거
5. `model_verifier` — lease/SBC/TRA/earn-out/dilution 이중계산 검증

`AgentRunner`는 Protocol만 정의한다. 특정 LLM SDK에 저장소를 묶지 않으며, 향후 davis-agent-kit이나 다른 harness에서 주입할 수 있다. `run_research_tasks()`는 주입된 runner로 다섯 태스크를 실행한다.

## 입력

`examples/distressed_equity_case.json`을 복사해 사례별로 수정한다.

중요한 원칙:

- 모든 금액 단위는 한 사례 안에서 통일한다.
- `monthly_free_cash_flow`는 이미 interest, maintenance capex, cash lease 등 선택한 회계정책을 일관되게 반영해야 한다.
- lease를 FCF에서 비용 처리했다면 EV bridge에서 다시 debt처럼 차감하지 않는다.
- SBC를 현금흐름에서 가산하더라도 예상 희석은 주식 수에 반영한다.
- `reasonable_probability_low/high`는 조사 결과이며 엔진이 자동 생성하지 않는다.

## 실행

```bash
python -m distressed_equity examples/distressed_equity_case.json
```

또는:

```bash
distressed-equity examples/distressed_equity_case.json -o output/example.md
```

## 1차 판정

- `PASS`: recovery 전에 liquidity가 소진되거나, 선택한 성공 경로로도 요구수익률을 달성하기 어렵다.
- `DEEP DIVE`: 보수적인 현실적 성공확률 하단이 Required Probability를 넘는다.
- `REVIEW`: 확률 우위가 아직 해결되지 않았다.

이 판정은 매수·매도 신호가 아니라 연구 우선순위다.

## 다음 단계

1. SEC/DART/회사 IR/채권가격을 point-in-time으로 수집하는 evidence layer
2. debt maturity/covenant 표준 스키마와 자동 survival schedule
3. 사업별 unit economics template
4. 과거 distress 사례 기반 base-rate 라이브러리
5. agent output을 JSON schema로 강제하고 사람 승인 후 probability interval에 반영
6. 후보 universe scanner와 연결해 `screen → deep dive → memo` 파이프라인 완성
