# Debt Exchange / Modification / Extinguishment Lineage

## 목적

Stable debt ID만으로는 distressed capital structure의 핵심 사건을 충분히 표현할 수 없다.

예를 들어:

```text
Old unsecured notes (CUSIP A)
  -> exchange offer
  -> New first-lien notes (CUSIP B)
     + New second-lien notes (CUSIP C)
     + cash
     + equity
```

A/B/C를 같은 stable ID로 합치면 법적 instrument identity가 망가지고, 모두 별개로만 두면 old debt가 무엇으로 바뀌었는지 잃는다.

따라서 두 레이어를 분리한다.

```text
stable instrument ledger
  = 각 법적 debt instrument의 version identity

lineage graph
  = 서로 다른 instrument 사이의 exchange / refinancing / redemption / conversion event
```

핵심은 **stable ID를 보존한 채 event를 사이에 두는 것**이다.

## Graph model

Debt lineage는 pairwise identity가 아니라 event node를 둔 hyperedge projection이다.

```text
instrument A ----> EVENT:exchange-2023 ----> instrument B
                                  |
                                  +--------> instrument C
```

따라서 1→N, N→1, N→M restructuring을 표현하면서 `A -> B`와 `A -> C`가 각각 독립적인 1:1 법적 승계라는 잘못된 의미를 만들지 않는다.

## Event semantics

지원 event:

- `amendment`
- `exchange`
- `refinancing`
- `redemption`
- `repurchase`
- `conversion`
- `issuance`
- `termination`
- `other`

의미 경계:

- `amendment`: 정확히 1 predecessor + 1 successor, 동일 stable ID
- `exchange` / `refinancing` / `conversion`: 양쪽이 있어야 하며 predecessor/successor stable ID 집합은 서로 겹치지 않음
- `redemption` / `repurchase` / `termination`: predecessor만 존재. replacement debt가 있으면 refinancing/exchange로 표현
- `issuance`: predecessor 없이 successor만 존재

Partial exchange 후 남아 있는 old note 잔액은 successor에 다시 넣지 않는다. 그 잔액은 같은 stable instrument에 그대로 남아 있고, event에는 실제 참여 principal만 기록한다.

## Candidate → verified 경계

Event 입력 파일은 **항상 `status = candidate`**다.

Event JSON에 직접:

```json
{"status": "verified"}
```

라고 쓰는 것은 거부한다.

Confirmed lineage가 되려면 별도 verification artifact가 필요하다.

```text
candidate event
  -> source/cutoff validation
  -> semantic fingerprint 생성
  -> verifier가 cited evidence를 다시 엶
  -> verification artifact
       event_fingerprint = exact fingerprint
       status = verified
       verifier = ...
       verified_on = ...
       evidence_reopened = true
  -> fingerprint 재확인
  -> verified event promotion
```

Event의 principal, predecessor/successor, source refs, accounting assertion 등 의미 필드가 하나라도 바뀌면 fingerprint가 달라져 기존 verification을 재사용할 수 없다.

`verified`만 confirmed root/terminal topology에 사용한다. Candidate는 output과 impact 계산에는 남지만 confirmed lineage가 아니며 unresolved로 표시된다.

## Partial exchange와 event amount

Snapshot principal과 event 참여 principal은 다를 수 있다.

```text
Old Notes outstanding:  $1,000m
Tendered into exchange:   $600m
New Notes issued:         $550m
```

Event predecessor:

```json
{
  "stable_id": "CUSIP:OLD",
  "amount": 600000000
}
```

`amount`는 **해당 event에 참여한 principal**이지 instrument 전체 outstanding가 아니다.

따라서 impact는 $1,000m → $550m가 아니라 $600m → $550m, 즉 participating principal 기준 −$50m다.

명시 event amount가 없으면 nearest ledger snapshot principal을 fallback으로 사용한다. 둘 다 없으면 principal impact는 unresolved다.

### Snapshot 날짜가 event 날짜와 다를 때

다른 날짜의 snapshot principal을 event amount의 hard ceiling으로 사용하지 않는다.

예를 들어 issuance 직후 $600m였으나 다음 분기말 snapshot에서 $550m로 줄었을 수 있다. 이때 source-backed event amount $600m를 단순히 “snapshot보다 큼”이라는 이유로 거부하면 안 된다.

규칙:

- snapshot date == event effective date: event amount가 snapshot principal을 초과하면 오류
- snapshot date != event effective date: hard reject하지 않고 observation-date mismatch + amount mismatch를 unresolved warning으로 남김

Maturity/security impact도 nearest observation이 event date와 다르면 그 사실을 unresolved evidence로 보존한다.

## Root / terminal semantics

Confirmed topology는 verified event만 사용한다.

Same-ID amendment는 legal instrument를 새 instrument로 바꾸는 topology가 아니다. 따라서 amendment만 있는 stable ID는 root이면서 terminal일 수 있다.

Cross-ID event만 old/new instrument 사이의 lineage topology를 바꾼다.

## Investment-facing impact

각 event는 deterministic하게 다음을 계산하거나 표시한다.

- participating predecessor principal
- participating successor principal
- principal delta
- nearest maturity before / after
- maturity extension / acceleration
- secured participating principal before / after
- creditor cash outflow
- transaction fees
- equity issued shares/value
- explicit accounting treatment

파생 signal 예:

```text
principal_reduced
principal_increased
nearest_maturity_extended
nearest_maturity_accelerated
secured_principal_increased
secured_principal_decreased
cash_outflow_to_creditors
transaction_fee_outflow
existing_common_dilution
debt_terminated_without_successor
accounting_extinguishment
accounting_modification
```

이 signal은 valuation verdict가 아니다. 예를 들어 principal이 감소하면서 maturity가 연장됐더라도 secured claim이 늘고 cash가 빠지고 common이 희석될 수 있다. Lineage layer는 이 trade-off를 한 event 안에 함께 보존한다.

## Accounting boundary

**Modification / extinguishment는 자동 추론하지 않는다.**

다음만으로 회계처리를 결정하지 않는다.

- CUSIP 변경
- coupon 변경
- maturity 변경
- `exchange`라는 이름
- principal 감소
- secured/unsecured 변경

Accounting object:

```json
{
  "treatment": "extinguishment",
  "standard": "US_GAAP",
  "basis": "issuer_disclosed",
  "quantitative_test_pct": null,
  "source_refs": ["span-id"]
}
```

지원 treatment:

```text
undetermined
modification
extinguishment
partial_extinguishment
not_applicable
```

지원 basis:

```text
undetermined
issuer_disclosed
manual_analysis
```

`modification`, `extinguishment`, `partial_extinguishment`를 주장하려면 basis와 exact source refs가 모두 필요하다. Quantitative test 결과도 동일하다.

Lineage verification과 accounting verification은 같은 것이 아니다. Event 자체가 verified여도 accounting treatment에는 별도의 cited evidence가 필요하다.

## Source provenance

Workspace에서는 candidate event를 frozen `debt_instrument_source_packet`과 대조한다.

검증 항목:

- event source ref가 실제 frozen span인지
- source accession이 cited span accession과 일치하는지
- event effective date가 research cutoff 이후가 아닌지
- cited source가 cutoff 이후 공개된 자료가 아닌지
- predecessor/successor stable ID가 실제 ledger에 존재하는지
- participant/accounting source refs가 event source refs 안에 포함되는지

그 뒤 별도의 fingerprint-bound verification을 거쳐야 confirmed lineage가 된다.

## Standalone CLI

### 1. Event template

```bash
distressed-equity-debt-lineage \
  debt_instruments.json \
  --source-packet debt_instrument_sources.json \
  --template-output debt_lineage_events.json
```

### 2. Candidate events 작성 + verification template 생성

```bash
distressed-equity-debt-lineage \
  debt_instruments.json \
  debt_lineage_events.json \
  --source-packet debt_instrument_sources.json \
  --verification-template-output debt_lineage_verification.json
```

Verifier가 cited evidence를 다시 확인한 뒤 각 review에:

```json
{
  "status": "verified",
  "verifier": "reviewer-id",
  "verified_on": "2026-09-16",
  "evidence_reopened": true
}
```

를 채운다. `event_fingerprint`는 변경하지 않는다.

### 3. Promotion + graph

```bash
distressed-equity-debt-lineage \
  debt_instruments.json \
  debt_lineage_events.json \
  --source-packet debt_instrument_sources.json \
  --verification debt_lineage_verification.json \
  -o debt_lineage_output.json
```

Verification이 없으면 graph는 candidate-only 상태로 생성되며 confirmed roots/terminals는 만들어지지 않는다.

## Research workspace

Debt snapshots를 넣으면:

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments debt_instruments.json
```

생성물:

```text
debt_instrument_ledger.json
debt_lineage_template.json
```

Candidate lineage를 넣으면:

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments debt_instruments.json \
  --debt-lineage debt_lineage_events.json
```

추가 생성물:

```text
debt_lineage_verification_template.json
debt_lineage.json   # candidate-only until verification
```

Evidence를 재확인한 verification artifact를 넣으면:

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments debt_instruments.json \
  --debt-lineage debt_lineage_events.json \
  --debt-lineage-verification debt_lineage_verification.json
```

Fingerprint가 일치하는 verified event만 confirmed root/terminal topology에 들어간다. Agent-result ingestion과 같이 실행하면 `merged.json`에도 lineage graph가 첨부된다.

## 설계상 하지 않는 것

- 비슷한 old/new note를 보고 exchange라고 추측해 verified 처리
- event JSON에 `status=verified`를 직접 적어 verification 우회
- event를 수정한 뒤 예전 verification fingerprint 재사용
- 다른 CUSIP을 같은 stable ID로 합침
- unchanged residual debt를 exchange successor로 중복 표기
- event 이름만으로 modification/extinguishment 판정
- 다른 날짜 snapshot principal을 event amount의 절대 상한으로 사용
- full outstanding principal을 partial tender amount로 간주
- candidate event를 confirmed root/terminal graph에 포함
- secured debt 증가를 무조건 긍정/부정으로 가치평가

목표는 구조조정 사건을 **정확히 연결하고, 원금·만기·담보·현금·희석의 교환관계를 provenance와 함께 보존하는 것**이다.
