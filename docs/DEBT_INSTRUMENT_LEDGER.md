# Stable Debt Instrument Ledger

`distressed-equity-debt-ledger`는 filing마다 표현이 달라지는 동일 채무를 가능한 범위에서 하나의 stable ID로 연결하고, 그 instrument의 조건 변화 후보를 보여준다.

## 목적

단순 snippet diff는 같은 filing 안의 모든 금액과 연도를 비교하므로 `2027 Notes`가 `2028 Notes`로 연장됐는지, 전혀 다른 채무가 등장했는지 구분하지 못한다.

이 레이어는 agent가 source filing에서 검증한 instrument snapshot을 입력으로 받아:

- 동일 instrument의 시계열 identity
- principal 변화
- maturity 변화
- coupon / benchmark / spread 변화
- seniority / security 변화
- revolver commitment / drawn / available 변화

를 구조화한다.

## 입력 예시

```json
{
  "instruments": [
    {
      "as_of_date": "2022-09-30",
      "source_accession": "0000000001-22-000001",
      "name": "5.00% Senior Notes due 2027",
      "instrument_type": "senior_notes",
      "principal": 500000000,
      "maturity_year": 2027,
      "coupon_pct": 5.0,
      "seniority": "senior_unsecured",
      "secured": false,
      "cusip": "123456AB7",
      "source_refs": ["e12"]
    },
    {
      "as_of_date": "2023-03-31",
      "source_accession": "0000000001-23-000010",
      "name": "Senior Notes due 2028",
      "instrument_type": "senior_notes",
      "principal": 450000000,
      "maturity_year": 2028,
      "coupon_pct": 5.5,
      "seniority": "senior_unsecured",
      "secured": false,
      "cusip": "123456AB7",
      "source_refs": ["e33"]
    }
  ]
}
```

## Identity hierarchy

자동 matching은 다음 우선순위를 사용한다.

1. 동일 CUSIP / ISIN
2. instrument type
3. 정규화된 이름 token 유사도
4. maturity
5. coupon
6. seniority / secured state
7. currency

명시 identifier가 같으면 조건이 바뀌어도 같은 instrument로 연결한다.

명시 identifier가 없으면 heuristic score를 사용한다. 기본 threshold는 `55`, best candidate와 second-best candidate의 차이가 `8` 미만이면 ambiguous로 보고 **자동 match하지 않는다**.

이는 false negative보다 false positive identity가 더 위험하다는 설계다.

## 출력

```bash
distressed-equity-debt-ledger debt_instruments.json \
  -o debt_instrument_ledger.json
```

출력에는:

- `versions`
- `changes`
- `unmatched_versions`
- `warnings`

가 포함된다.

변화 classification 예:

- `maturity_extended`
- `maturity_accelerated`
- `principal_changed`
- `pricing_changed`
- `ranking_or_security_changed`
- `facility_capacity_or_usage_changed`

## Research workspace 연결

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna \
  --debt-instruments debt_instruments.json
```

workspace에 `debt_instrument_ledger.json`이 생성된다. Agent-result ingestion이 수행되는 경우 `merged.json`에도 같은 ledger가 첨부된다.

중요하게, 이 artifact는 frozen `research_packet.json`을 수정하지 않는다. Historical research packet과 후속 해석 결과를 분리하기 위해서다.

## 한계

Stable ID는 법률적 동일성을 선언하지 않는다.

- debt exchange로 CUSIP이 바뀔 수 있다.
- 여러 tranche가 같은 maturity/coupon을 가질 수 있다.
- partial redemption 뒤 name이 달라질 수 있다.
- amendment가 신규 instrument로 회계/법률 처리될 수 있다.

따라서 material amendment는 credit agreement, indenture, amendment exhibit 또는 회사의 debt note 원문으로 최종 확인해야 한다.
