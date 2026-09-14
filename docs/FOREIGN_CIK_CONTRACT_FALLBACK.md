# Foreign-CIK Locator-Less Contract Fallback

## 목적

Cross-CIK resolver가 SEC Archives URL, 명시 CIK, exact accession으로 foreign filer를 확인해도 그 foreign filing 안의 2차 계약 참조에는 locator가 없을 수 있다.

예:

```text
Parent filing
  -> explicit SEC link -> Borrower CIK 8-K / EX-10.2

Borrower EX-10.2
  -> "Credit Agreement dated May 3, 2019"
     (accession / exhibit / SEC URL 없음)
```

이 단계는 이미 증명된 foreign CIK 내부에서만 locator-less contract identity fallback을 다시 실행한다.

## Resolution order

```text
root source
  -> explicit foreign CIK locator
  -> foreign entry exhibit
  -> foreign CIK point-in-time legal-name graph
  -> foreign CIK same-CIK source graph
  -> locator-less contract identity search
  -> unique older foreign exhibit
```

Foreign CIK를 찾기 위해 회사명을 검색하지 않는다. CIK 경계를 넘는 권한은 기존 cross-CIK resolver가 먼저 증명해야 한다.

## CIK별 identity isolation

각 foreign contract search는 다음 두 입력을 target CIK별로 분리한다.

1. `filings_as_of(target_cik, analysis_date)`
2. `legal_name_alias_graph[target_cik]`

따라서 root 회사의 alias graph가 foreign borrower 계약을 연결하는 데 사용될 수 없다.

예를 들어 root CIK의 alias graph에 우연히 다음 이름이 있어도:

```text
Borrower Old LLC
Borrower New LLC
```

foreign CIK의 authoritative alias graph에 그 관계가 없으면 foreign contract party bridge는 일어나지 않는다.

## Legal-name rename bridge

Foreign CIK가 다음 point-in-time identity를 갖는 경우:

```text
2019 contract: Borrower Old LLC, as Borrower
2022 entry:    Borrower New LLC, as Borrower
```

그리고 foreign CIK의 reconciled SEC legal-name graph가 cutoff 이전 rename을 확인하면 동일 borrower로 비교할 수 있다.

다음 조건은 그대로 hard guard다.

- borrower / issuer / guarantor role
- canonical contract kind
- exact execution date
- original vs amended-and-restated state
- candidate-side party evidence
- amendment / waiver / supplement exclusion

## Depth budget

Cross-CIK hop과 foreign CIK 내부 source hop은 하나의 global depth budget을 공유한다.

```text
local_depth_budget = max_depth - cross_cik_hops
```

예:

```text
max_depth = 3
root -> foreign CIK = 1 hop
foreign local depth budget = 2
```

`max_depth=1`이면 direct foreign entry document까지 도달한 시점에 budget이 소진되므로 locator-less fallback을 실행하지 않는다.

Nested foreign CIK의 hop 수는 resolved cross-CIK graph에서 root부터의 최소 CIK-hop distance로 계산한다.

## Node budget

Foreign contract graphs 전체가 하나의 `max_nodes` budget을 공유한다. 각 CIK/entry별 local graph node는 `(target_cik, node.url)`로 dedupe한다.

Budget을 소진하면 더 이상 자동 확장하지 않고 warning을 남긴다.

## Output provenance

`cross_cik_graph.json`에 다음이 추가된다.

```text
foreign_contract_graphs[]
  target_cik
  entry_document_url
  entry_accession
  cross_cik_hops
  local_depth_budget
  graph
    nodes
    references
    edges
    resolved_documents
    warnings
```

성공 edge는 기존 contract identity resolver와 동일하게:

```text
status = resolved_by_contract_identity
```

를 사용한다.

`ambiguous_contract_identity`, `unresolved_contract_identity`, `blocked_depth`도 그대로 보존한다.

## Debt candidate flow

Foreign locator-less fallback으로 새 exhibit가 확인되면 해당 문서는 기존 `SecInstrumentPacket`에 합쳐진다.

따라서 이후 경로는 새 특수경로가 아니라 기존 경로다.

```text
foreign resolved exhibit
  -> source span
  -> field proposal
  -> debt_instrument_template
  -> provenance validation
  -> stable debt ledger
```

즉 locator-less resolution 자체가 debt row를 확정하지 않는다.

## CLI semantics

기존 contract fallback 옵션이 root와 foreign CIK 양쪽에 동일하게 적용된다.

```text
--no-contract-identity-fallback
--contract-search-max-filings
--contract-days-before-execution
--contract-days-after-execution
```

`--no-contract-identity-fallback`을 사용하면 foreign fallback도 비활성화된다.

`--cross-cik-max-nodes`는 explicit cross-CIK traversal과 foreign-CIK contract expansion의 bounded research budget으로 사용된다.

## Safety properties

- name-only reference로 CIK를 추정하지 않음
- root alias를 foreign CIK에 적용하지 않음
- foreign search universe는 해당 CIK filing으로 제한
- source filing보다 미래 filing을 선택하지 않음
- analysis cutoff 이후 filing을 선택하지 않음
- ambiguous contract 후보를 점수로 억지 선택하지 않음
- global depth / node budget을 초과하지 않음
- resolved contract를 곧바로 authoritative debt row로 승격하지 않음

## Known limitations

1. locator-less fallback으로 새로 발견한 계약에서 또 다른 **새 cross-CIK locator**가 나타날 경우, 현재 pass는 그 새 CIK까지 다시 cross-CIK closure를 수행하지 않는다.
2. 매우 오래된 계약은 현재 bounded filing search window 밖에 있을 수 있다.
3. PDF/image/table-heavy exhibit는 별도 extraction 개선이 필요하다.
4. merger / novation / successor 관계는 same-CIK rename alias와 다른 legal succession 문제다.

현재 목표는 explicit foreign identity를 훼손하지 않으면서, foreign filer 내부의 locator-less 2차 계약 참조 때문에 생기는 false negative를 제거하는 것이다.
