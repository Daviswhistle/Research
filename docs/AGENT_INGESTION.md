# Structured Agent Result Ingestion

## 목적

에이전트는 조사와 반증을 맡지만 회계·산술이나 기존 point-in-time 사실을 임의로 덮어쓰지 않는다. 따라서 agent output은 자유 텍스트를 곧바로 모델 입력으로 사용하지 않고 다음 경로를 거친다.

```text
Agent research
  ↓
structured result JSON
  ↓
schema / task permission validation
  ↓
point-in-time validation
  ↓
evidence-reference validation
  ↓
conflict detection
  ↓
validated candidate patch
  ↓
deterministic screener
```

## Result schema

현재 schema version은 `1`이다.

```json
{
  "schema_version": "1",
  "task_name": "capital_stack_extractor",
  "analysis_date": "2022-12-31",
  "status": "complete",
  "evidence": [
    {
      "evidence_id": "e1",
      "claim": "$500m revolving facility matures in 2027",
      "source": "SEC 10-Q",
      "published_on": "2022-11-01",
      "event_on": "2022-09-30",
      "evidence_type": "fact",
      "locator": "https://www.sec.gov/...",
      "notes": "Debt footnote"
    }
  ],
  "patches": [
    {
      "path": "debt_obligations",
      "value": [
        {
          "name": "Revolving credit facility",
          "amount": 500000000,
          "due_month": 58,
          "cash_payment_required": false,
          "refinancing_required": true,
          "secured": true
        }
      ],
      "evidence_refs": ["e1"],
      "confidence": "high",
      "rationale": "Instrument, amount and maturity are identified in the filing."
    }
  ],
  "unresolved": [],
  "warnings": []
}
```

## Evidence rules

모든 patch는 하나 이상의 `evidence_refs`를 가져야 한다.

- evidence ID가 result 안에 존재하지 않으면 거부한다.
- `published_on > analysis_date`이면 result를 무효화한다.
- evidence type은 `fact / estimate / inference / forecast / unknown` 중 하나다.
- source와 claim은 비어 있을 수 없다.
- low-confidence patch는 기본적으로 evidence ledger에는 남기되 candidate를 변경하지 않는다.

## Task별 patch 권한

에이전트가 모든 필드를 수정할 수 없도록 task별 whitelist를 둔다.

### `capital_stack_extractor` / `capital_stack_auditor`

- `capital_structure.current_shares`
- `capital_structure.net_debt`
- `capital_structure.other_senior_claims`
- `starting_liquidity`
- `available_credit`
- `asset_monetization`
- `restricted_cash`
- `debt_obligations`
- `covenants`

### `historical_market_reconstructor`

- `capital_structure.current_price`
- `peak_market_cap`
- `current_enterprise_value`

### `impairment_and_normalization_researcher` / `business_and_normalization_auditor`

- `ttm_revenue`
- `has_real_customers`
- `is_pre_revenue`
- `has_historical_operating_profit`
- `monthly_cash_burn`
- `recovery_month`
- `base_scenario`
- `reference_scenario`
- `downside_scenario`
- `critical_assumptions_complete`

### `survival_auditor`

유동성·runway·debt/covenant 관련 필드만 수정할 수 있다.

### `probability_calibrator`

스크리닝 candidate를 직접 수정할 권한이 없다. probability range는 별도 review/calibration layer에서 다룬다.

## Conflict rule

기존 SEC prefill이나 이미 승인된 agent result가 non-null 값을 가지고 있는데 새 결과가 다른 값을 제안하면 기본적으로 conflict다.

```text
existing value != proposed value
→ do not overwrite
→ record conflict
→ human/research reconciliation required
```

`--allow-overwrite`는 명시적으로 켰을 때만 이 규칙을 우회한다.

## 실행

SEC packet은 result template을 함께 생성한다.

```bash
distressed-equity-sec \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  -o output/cvna_packet.json \
  --tasks-output output/cvna_tasks.json
```

특정 task template만 만들 수도 있다.

```bash
distressed-equity-ingest output/cvna_packet.json \
  --template-task capital_stack_extractor \
  --analysis-date 2022-12-31
```

여러 agent 결과를 검증·병합한다.

```bash
distressed-equity-ingest output/cvna_packet.json \
  --result output/capital_stack_result.json \
  --result output/market_result.json \
  --result output/normalization_result.json \
  -o output/cvna_merged.json
```

필수 deterministic 필드가 모두 채워지면 같은 실행에서 `screening_result`도 자동 생성한다.

## 중요한 비대칭

이 레이어는 false negative보다 false precision을 더 경계한다.

- 값이 없으면 `UNKNOWN`으로 둔다.
- low-confidence 값은 자동 반영하지 않는다.
- conflicting primary-source 해석은 덮어쓰지 않는다.
- 에이전트가 계산한 성공확률로 Required Probability를 대체하지 않는다.
- historical replay에서 미래 자료가 하나라도 섞이면 해당 result를 무효화한다.
