# Debt Exchange / Modification / Extinguishment Lineage

## 목적

Stable debt ID만으로는 distressed capital structure의 핵심 사건을 충분히 표현할 수 없다.

예를 들어 다음은 같은 instrument의 단순 amendment가 아니다.

```text
Old unsecured notes (CUSIP A)
  -> exchange offer
  -> New first-lien notes (CUSIP B)
     + New second-lien notes (CUSIP C)
     + cash
     + equity
```

CUSIP A/B/C를 억지로 하나의 stable ID로 합치면 법적 instrument identity가 망가지고, 반대로 모두 별개로 두기만 하면 old debt가 무엇으로 바뀌었는지 알 수 없다.

따라서 이 repository는 두 레이어를 분리한다.

```text
stable instrument ledger
  = 각 법적 debt instrument의 version identity

lineage graph
  = 서로 다른 instrument 사이에서 발생한 exchange / refinancing / redemption / conversion event
```

핵심 원칙은 **stable ID를 보존한 채 event를 사이에 둔다**는 것이다.

## Graph model

Debt lineage는 단순 pairwise edge가 아니라 event node를 둔 hyperedge projection이다.

```text
instrument A ----> EVENT:exchange-2023 ----> instrument B
                                  |
                                  +--------> instrument C
```

JSON edge 관점에서는:

```text
A -> EVENT:exchange-2023
EVENT:exchange-2023 -> B
EVENT:exchange-2023 -> C
```

이 구조를 사용하면 1→N, N→1, N→M restructuring을 표현하면서 `A -> B`, `A -> C`가 각각 독립적인 1:1 법적 승계라는 잘못된 의미를 만들지 않는다.

## Event types

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

`amendment`는 동일 stable ID를 유지해야 한다.

서로 다른 CUSIP/ISIN 사이의 연결은 `exchange`, `refinancing`, `conversion` 등 명시적 cross-ID event로 표현한다.

## Candidate와 verified

각 event는:

```text
status = candidate | verified
```

을 가진다.

`candidate`는 조사 우선순위일 뿐 확정 계보가 아니다.

따라서 candidate event는:

- root instrument를 확정하지 않는다.
- terminal instrument를 확정하지 않는다.
- output에는 남지만 unresolved event로 표시된다.
- 투자 결론에서 confirmed restructuring으로 취급하면 안 된다.

`verified`만 confirmed lineage 계산에 사용한다.

## Partial exchange

Snapshot principal과 실제 exchange 참여 principal은 다를 수 있다.

예:

```text
Old Notes outstanding: $1,000m
Tendered into exchange: $600m
New Notes issued:       $550m
```

이 경우 predecessor에:

```json
{
  "stable_id": "CUSIP:OLD",
  "amount": 600000000
}
```

를 넣는다.

`amount`는 **해당 event에 참여한 principal**이다. instrument 전체 outstanding principal이 아니다.

따라서 lineage impact는 $1,000m → $550m로 잘못 계산하지 않고 $600m → $550m, 즉 participating principal 기준 −$50m로 계산한다.

명시 event amount가 없으면 ledger snapshot principal을 fallback으로 사용한다. 둘 다 없으면 principal impact는 unresolved다.

Event amount가 관측 snapshot principal보다 크면 입력 오류로 거부한다.

## Investment-facing impact

각 event는 deterministic하게 다음을 계산하거나 표시한다.

- participating predecessor principal
- participating successor principal
- principal delta
- nearest maturity before / after
- nearest maturity extension / acceleration
- secured participating principal before / after
- cash paid to creditors
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

이 signal은 valuation 자체가 아니다. Survival/common-equity 연구에서 어떤 방향의 capital-structure 변화가 있었는지 빠르게 찾기 위한 구조화 결과다.

예를 들어:

```text
principal -50m
nearest maturity +3y
secured principal +550m
cash outflow 25m
equity issued
```

라면 단순히 "debt reduced"라고 결론 내리면 안 된다.

- principal은 줄었지만
- creditor ranking은 강화됐고
- cash는 빠져나갔고
- 기존 common은 희석됐으며
- maturity wall은 뒤로 밀렸을 수 있다.

Lineage layer는 이 trade-off를 한 event에 함께 보존한다.

## Accounting boundary

**Modification / extinguishment는 자동 추론하지 않는다.**

다음 정보만으로 회계처리를 결정하지 않는다.

- CUSIP 변경
- coupon 변경
- maturity 변경
- 이름에 `exchange`가 포함됨
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

`modification`, `extinguishment`, `partial_extinguishment`를 주장하려면:

1. `basis`가 있어야 하고
2. exact `source_refs`가 있어야 한다.

Quantitative test 결과를 넣는 경우에도 basis + source evidence가 필요하다.

Repository는 현재 accounting standard의 법칙을 이름/조건 변화만으로 자동 적용하지 않는다. 이는 false precision을 피하기 위한 의도적인 경계다.

## Source provenance

Workspace에서는 lineage event를 frozen `debt_instrument_source_packet`과 대조한다.

검증 항목:

- event source ref가 실제 frozen span인지
- source accession이 cited span accession과 일치하는지
- event effective date가 research cutoff 이후가 아닌지
- cited source가 cutoff 이후 공개된 자료가 아닌지
- predecessor/successor stable ID가 실제 ledger에 존재하는지
- participant/accounting source refs가 event source refs 안에 포함되는지

검증 실패 event는 workspace lineage graph로 승격되지 않는다.

## CLI

먼저 verified debt snapshots로 stable ledger와 lineage template을 만든다.

```bash
distressed-equity-debt-lineage \
  debt_instruments.json \
  --source-packet debt_instrument_sources.json \
  --template-output debt_lineage_events.json
```

Template에는 현재 ledger의 stable ID가 들어간다.

Event를 채운 뒤:

```bash
distressed-equity-debt-lineage \
  debt_instruments.json \
  debt_lineage_events.json \
  --source-packet debt_instrument_sources.json \
  -o debt_lineage_output.json
```

출력:

```text
ledger
lineage.events
lineage.edges
lineage.impacts
lineage.root_instruments
lineage.terminal_instruments
lineage.unresolved_events
lineage.warnings
```

## Research workspace

기존 research workspace에서도 바로 사용할 수 있다.

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments debt_instruments.json
```

이 시점에:

```text
debt_instrument_ledger.json
debt_lineage_template.json
```

이 생성된다.

Lineage event 검증 후:

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
debt_lineage.json
```

Agent-result ingestion과 같이 실행하면 `merged.json`에도 `debt_lineage`가 첨부된다.

## 설계상 하지 않는 것

현재 lineage layer는 다음을 의도적으로 자동화하지 않는다.

- 비슷한 old/new note를 보고 exchange라고 추측해 verified 처리
- 다른 CUSIP을 같은 stable ID로 합침
- event 이름만으로 modification/extinguishment 판정
- full outstanding principal을 partial tender amount로 간주
- candidate event를 confirmed root/terminal graph에 포함
- secured debt 증가를 무조건 긍정/부정으로 가치평가

목표는 구조조정 사건을 **정확히 연결하고, 원금·만기·담보·현금·희석의 교환관계를 보존하는 것**이다.
