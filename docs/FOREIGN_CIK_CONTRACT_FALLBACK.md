# Foreign-CIK Locator-Less Contract Fallback

## 목적

Cross-CIK resolver가 SEC Archives URL, 명시 CIK, exact accession으로 foreign filer를 확인해도 그 foreign filing 안의 2차 계약 참조에는 locator가 없을 수 있다.

예:

```text
Parent filing
  -> explicit SEC link -> Borrower A CIK 8-K / EX-10.2

Borrower A EX-10.2
  -> "Credit Agreement dated May 3, 2019"
     (accession / exhibit / SEC URL 없음)

Borrower A old Credit Agreement
  -> explicit SEC link -> Borrower/Guarantor B CIK exhibit
```

이 레이어는 이미 증명된 foreign CIK 내부에서 locator-less contract fallback을 실행하고, 그 결과 새로 발견된 문서를 다시 cross-CIK scanner에 넣어 bounded fixed-point closure를 수행한다.

## Resolution / closure order

```text
root source
  -> explicit foreign A CIK locator
  -> foreign A entry exhibit
  -> foreign A reconciled legal-name graph
  -> foreign A same-CIK source graph
  -> locator-less contract identity search
  -> unique older A exhibit
  -> scan newly resolved A exhibit for explicit foreign locators
  -> explicit foreign B CIK locator
  -> foreign B entry exhibit
  -> repeat until fixed point or budget exhaustion
```

Foreign CIK를 찾기 위해 회사명을 검색하지 않는다. 새로운 CIK 경계를 넘는 권한은 매번 explicit SEC identity evidence가 먼저 증명해야 한다.

## CIK별 identity isolation

각 foreign contract search는 다음 두 입력을 target CIK별로 분리한다.

1. `filings_as_of(target_cik, analysis_date)`
2. `legal_name_alias_graph[target_cik]`

따라서 root 회사 또는 이전 foreign CIK의 alias graph가 다음 CIK의 borrower/issuer/guarantor를 연결하는 데 재사용되지 않는다.

새 CIK가 closure 도중 발견되면 그 CIK에 대해서도 별도의 cutoff-safe legal-name graph를 수집한다.

## Legal-name rename bridge

Foreign CIK가 다음 point-in-time identity를 갖는 경우:

```text
2019 contract: Borrower Old LLC, as Borrower
2022 entry:    Borrower New LLC, as Borrower
```

그리고 그 **동일 CIK**의 reconciled SEC legal-name graph가 cutoff 이전 rename을 확인하면 동일 borrower로 비교할 수 있다.

다음 조건은 그대로 hard guard다.

- borrower / issuer / guarantor role
- canonical contract kind
- exact execution date
- original vs amended-and-restated state
- candidate-side party evidence
- amendment / waiver / supplement exclusion

## Global depth budget

CIK hop 수만 세지 않는다. Locator-less same-CIK contract hop도 global depth를 소비한다.

예:

```text
root source                         depth 0
  -> foreign A entry               depth 1
  -> A locator-less old agreement  depth 2
  -> explicit foreign B entry      depth 3
```

따라서:

```text
local_depth_budget = max_depth - entry_global_depth
```

을 사용한다.

예를 들어 `max_depth=4`라면 foreign B entry의 `entry_global_depth=3`이고 B 내부에는 정확히 1단계만 남는다. `entry_global_depth >= max_depth`이면 추가 locator-less fallback은 실행하지 않는다.

Closure가 새 cross-CIK resolution을 만들 때 source node의 global depth를 알고 있는 경우에만 다음 target depth를 확정한다. Global depth를 확정할 수 없는 새 target에 대해서는 보수적으로 추가 locator-less fallback을 하지 않고 warning을 남긴다.

## Fixed-point termination

Closure는 다음 중 하나가 발생할 때 종료한다.

- 새 cross-CIK resolution이 더 이상 없음
- 새 foreign contract entry가 없음
- `max_depth` 소진
- `max_nodes` 소진
- 내부 closure iteration cap 도달

이미 본 foreign entry `(target_cik, entry_document_url)`은 다시 처리하지 않는다.

Cross-CIK reference/resolution merge도 semantic key로 dedupe하므로 같은 SEC locator가 한 문서에 반복돼도 동일 resolution이 중복 누적되지 않는다.

## Node budget

Foreign contract graphs와 closure scan은 bounded `max_nodes` budget 안에서 동작한다. Local graph scan은 `(target_cik, node.url)`로 dedupe한다.

Budget을 소진하면 더 이상 자동 확장하지 않고 warning을 남긴다.

## Output provenance

`cross_cik_graph.json`의 `foreign_contract_graphs[]`에는 최초 foreign fallback과 closure에서 새로 처리한 CIK graph가 함께 보존된다.

Closure 단계의 graph는 다음 추가 provenance를 가질 수 있다.

```text
foreign_contract_graphs[]
  target_cik
  entry_document_url
  entry_accession
  entry_global_depth
  local_depth_budget
  legal_name_aliases
  graph
    nodes
    references
    edges
    resolved_documents
    warnings
  closure_cross_cik_graph
```

새 explicit foreign resolution은 최종 `ForeignContractExpansion.cross_expanded.cross_cik_graph`에도 semantic merge된다.

Locator-less 성공 edge는 기존 contract identity resolver와 동일하게:

```text
status = resolved_by_contract_identity
```

를 사용한다. `ambiguous_contract_identity`, `unresolved_contract_identity`, `blocked_depth`도 그대로 보존한다.

## Debt candidate flow

Foreign locator-less fallback 또는 closure로 새 exhibit가 확인되면 해당 문서는 기존 `SecInstrumentPacket`에 합쳐진다.

```text
foreign resolved exhibit
  -> source span
  -> field proposal
  -> debt_instrument_template
  -> provenance validation
  -> stable debt ledger
```

즉 locator-less resolution이나 cross-CIK resolution 자체가 debt row를 확정하지 않는다.

## CLI semantics

기존 contract fallback 옵션이 root와 foreign CIK 양쪽에 동일하게 적용된다.

```text
--no-contract-identity-fallback
--contract-search-max-filings
--contract-days-before-execution
--contract-days-after-execution
```

`--no-contract-identity-fallback`을 사용하면 foreign fallback과 그 뒤의 closure도 비활성화된다.

`--cross-cik-max-nodes`는 explicit traversal, foreign-CIK contract expansion, closure의 bounded research budget으로 사용된다.

## Safety properties

- name-only reference로 CIK를 추정하지 않음
- root/이전 foreign alias를 다음 CIK에 적용하지 않음
- foreign search universe는 해당 CIK filing으로 제한
- source filing보다 미래 filing을 선택하지 않음
- analysis cutoff 이후 filing을 선택하지 않음
- ambiguous contract 후보를 점수로 억지 선택하지 않음
- locator-less same-CIK hop도 global depth를 소비
- repeated explicit locator는 semantic dedupe
- global depth / node / iteration budget을 초과하지 않음
- resolved contract를 곧바로 authoritative debt row로 승격하지 않음

## Known limitations

1. 매우 오래된 계약은 현재 bounded filing search window 밖에 있을 수 있다.
2. PDF/image/table-heavy exhibit는 별도 extraction 개선이 필요하다.
3. merger / novation / successor 관계는 same-CIK rename alias와 다른 legal succession 문제다.
4. Global depth를 deterministic하게 재구성할 수 없는 비정형 legacy path는 자동 locator-less continuation을 거부한다.

현재 목표는 explicit foreign identity와 point-in-time provenance를 훼손하지 않으면서, locator-less 계약과 cross-CIK source chain이 번갈아 나타나는 경우의 false negative를 bounded closure로 제거하는 것이다.
