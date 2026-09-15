# Covenant EBITDA / Headroom Model

Distressed equity에서 `debt maturity`만 보는 것으로는 부족하다. maintenance covenant나 springing covenant가 recovery window보다 먼저 common equity를 압박할 수 있기 때문이다.

이 모듈은 covenant 산술을 에이전트의 자유문장 판단에서 분리한다.

```text
source filing / credit agreement
        ↓
contractual covenant definition
        ↓
base EBITDA + permitted add-backs - required deductions
        ↓
covenant EBITDA
        ↓
ratio / liquidity test
        ↓
headroom / minimum EBITDA required
        ↓
screening CovenantRisk
```

## 지원하는 covenant

- `max_net_leverage`
- `max_total_leverage`
- `min_interest_coverage`
- `min_fixed_charge_coverage`
- `min_liquidity`

## 중요한 원칙

### Add-back은 자동 추정하지 않는다

`CovenantEbitdaBridge`의 `add_backs`와 `deductions`는 반드시 계약 정의를 조사한 뒤 입력한다.

```text
Covenant EBITDA
= Base EBITDA
+ permitted add-backs
- required deductions
```

`disputed_add_backs`는 계산에는 들어가더라도 결과 warning에 노출된다. 실제로 허용되는지 불명확한 add-back을 숨기지 않기 위한 장치다.

### Springing covenant는 적용 여부부터 계산한다

예를 들어 revolver 사용률이 35% 이상일 때만 테스트하는 covenant라면:

```text
usage = revolver_drawn / revolver_commitment
applies = usage >= 35%
```

trigger나 revolver 숫자가 없으면 `applies=None`으로 남긴다. 자동으로 비적용이라고 보지 않는다.

### Headroom 방향을 통일한다

- maximum covenant: `threshold - actual`
- minimum covenant: `actual - threshold`

따라서 양수는 cushion, 음수는 breach 방향이다.

Leverage/coverage covenant는 **최소 covenant EBITDA**와 현재 covenant EBITDA의 차이도 계산한다.

```text
EBITDA cushion = Covenant EBITDA - Minimum EBITDA to comply
```

이 숫자가 distressed investing에서 특히 유용하다. 매출이 몇 % 줄면 breach하는지를 bottom-up 시나리오와 연결할 수 있기 때문이다.

## 입력 예시

```json
{
  "analysis_date": "2022-12-31",
  "evidence": [
    {
      "evidence_id": "e1",
      "claim": "Maximum net leverage is 5.0x and springs at 35% revolver usage",
      "source": "SEC credit agreement exhibit",
      "published_on": "2022-11-01",
      "evidence_type": "fact",
      "locator": "https://www.sec.gov/..."
    }
  ],
  "models": [
    {
      "definition": {
        "name": "Maximum Net Leverage",
        "kind": "max_net_leverage",
        "threshold": 5.0,
        "test_month": 3,
        "springing": true,
        "springing_revolver_drawn_pct": 0.35,
        "source_refs": ["e1"]
      },
      "ebitda_bridge": {
        "base_ebitda": 100,
        "add_backs": {"permitted cost savings": 20},
        "deductions": {"non-permitted gain": 10}
      },
      "inputs": {
        "gross_debt": 600,
        "unrestricted_cash": 50,
        "cash_netting_cap": 75,
        "revolver_drawn": 50,
        "revolver_commitment": 100
      }
    }
  ]
}
```

실행:

```bash
distressed-equity-covenant covenant_input.json -o covenant_result.json
```

출력에는:

- 계산된 covenant headroom
- minimum EBITDA to comply
- EBITDA cushion
- springing 적용 여부
- screening용 `CovenantRisk`
- `distressed-equity-ingest`가 바로 받을 수 있는 `capital_stack_extractor` agent result

이 들어간다.

## Filing amendment diff

`distressed-equity-sec-debt --diff-output ...`은 인접 filing의 capital-stack snippet을 비교한다.

비교하는 값:

- dollar amount candidates
- maturity/year candidates
- percentage/rate candidates
- category appeared/disappeared

이 결과는 **confirmed amendment가 아니다.** 금액이나 연도가 달라졌다는 탐색 신호일 뿐이며 source filing 또는 credit-agreement exhibit에서 instrument 단위로 확인해야 한다.
