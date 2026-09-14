# Source-Confirmed Named External Entity Resolution

## 목적

SEC filing의 계약 설명에는 외부 borrower / issuer / guarantor의 법인명은 나오지만 CIK, accession, SEC Archives URL이 전혀 없을 수 있다.

예:

```text
Credit Agreement dated May 3, 2019 among
Target Finance LLC, as Borrower.
```

기존 explicit cross-CIK resolver는 이런 문자열만으로 CIK 경계를 넘지 않는다. 이 문서는 그 보수적 원칙을 유지하면서 false negative를 줄이기 위한 마지막 fallback을 정의한다.

## 핵심 원칙

```text
candidate generation != identity confirmation != contract resolution
```

현재 SEC registry에서 이름이 같다는 이유만으로 CIK를 확정하지 않는다.

자동 resolution에는 세 단계가 모두 필요하다.

```text
role-labelled contract party + contract identity
    -> candidate CIK generation
    -> source-date SEC legal-name confirmation
    -> unique historical contract exhibit confirmation
```

## 1. 입력 조건

일반적인 회사명 언급은 resolver를 시작하지 않는다.

다음 두 정보가 같은 source context에서 추출돼야 한다.

1. canonical contract kind + execution date
2. 명시적 role을 가진 legal entity

예:

```text
Target Finance LLC, as Borrower
Credit Agreement dated May 3, 2019
```

지원 role은 기존 party-aware contract identity와 동일하게 borrower / issuer / guarantor 계열이다.

## 2. Candidate generation

후보 생성은 CIK를 확정하는 증거가 아니다.

현재 후보 source:

- 이미 SEC evidence로 확보된 `legal_name_alias_graph`
- SEC `company_tickers.json`에서 **exact normalized legal-name**이 일치하는 title

`company_tickers.json`은 현재 시점 lookup aid이므로 historical identity authority로 취급하지 않는다.

후보 source는 명시적으로:

```text
sec_company_tickers_candidate_only
```

로 기록된다.

다음은 허용하지 않는다.

```text
Target Finance LLC
≈ Target Finance Holdings LLC
≈ Target Financial LLC
≈ Target Finance Inc.
```

문장 유사도, token similarity, edit distance, embedding, parent/subsidiary 추론은 사용하지 않는다.

## 3. Source-date SEC confirmation

후보 CIK마다 source filing date를 cutoff로 다시 검증한다.

후보가 살아남으려면:

1. source date까지 해당 CIK의 SEC filing이 최소 하나 존재하고
2. 그 source date 기준 cutoff-safe SEC legal-name graph가 정확한 normalized party name을 포함해야 한다.

Legal-name graph는 기존과 동일하게 다음을 사용한다.

- SEC submissions `name` / `formerNames`
- cutoff-safe complete-submission `<SEC-HEADER>`
- `FORMER CONFORMED NAME`
- `DATE OF NAME CHANGE`

따라서 오늘의 SEC registry title이 과거 source date에는 아직 사용되지 않았던 이름이면 후보는 확인되지 않는다.

## 4. Contract confirmation

Name-confirmed CIK 각각에 대해 그 CIK의 point-in-time filing universe 안에서 기존 `reverse_search_contract_identity`를 실행한다.

기존 hard guard가 그대로 적용된다.

- exact canonical contract kind
- exact execution date
- original vs amended-and-restated state
- borrower / issuer / guarantor role
- source-backed same-CIK legal-name aliases
- amendment / waiver / supplement false-positive guard
- source filing date 이후 filing 금지
- analysis cutoff 이후 filing 금지

Name confirmation은 contract party mismatch를 override하지 않는다.

## 5. Unique-only resolution

모든 후보 CIK의 contract search 결과를 합친 뒤 정확히 하나의:

```text
(CIK, contract exhibit URL)
```

만 남을 때 자동 resolution한다.

상태는 다음과 같다.

```text
no_candidate_cik
name_not_confirmed_at_source_date
contract_not_found_in_confirmed_cik
ambiguous_named_entity_contract
resolved_named_entity_contract
```

두 CIK가 같은 legal name을 쓰고 각각 동일 계약을 제출했거나, 한 CIK에서 복수 exhibit가 살아남으면 `ambiguous_named_entity_contract`로 남는다. 점수로 winner를 고르지 않는다.

## Provenance separation

이 fallback은 explicit SEC locator가 아니다.

따라서 성공해도 top-level explicit cross-CIK resolution을:

```text
resolved_cross_cik
```

로 조작하지 않는다.

별도 artifact에 보존한다.

```text
named_entity_contract_graph.json
  candidates[]
  resolutions[]
  resolved_documents[]
  warnings[]
```

동일 payload는 `research_packet.json`의 `named_entity_contract_graph`와 cross-CIK research artifact 내부에도 저장된다.

## Debt candidate flow

유일하게 확인된 exhibit는 기존 source-backed debt pipeline으로만 들어간다.

```text
confirmed external contract exhibit
  -> source spans
  -> field proposals
  -> debt instrument verification template
  -> provenance validation
  -> stable debt ledger
```

Named-entity resolution 자체는 debt principal, commitment, maturity 등을 authoritative value로 확정하지 않는다.

## Resolution priority

강한 locator 경로가 항상 먼저다.

```text
1. explicit same-CIK locator
2. same-CIK locator-less contract identity
3. explicit cross-CIK locator
4. foreign-CIK locator-less contract + fixed-point closure
5. source-confirmed named-entity contract fallback
```

따라서 이름 기반 후보 생성은 마지막 fallback이며 explicit provenance를 대체하지 않는다.

## Depth

Workspace에서는 root source nodes와 foreign contract nodes를 global depth로 재기준화한 뒤 named resolver에 전달한다.

Foreign local graph node는:

```text
global_depth = entry_global_depth + local_node.depth
```

를 사용한다. `reference_depth` 밖의 node는 named-entity scan 대상이 아니다.

## Current false-negative boundary

현재 SEC `company_tickers.json`은 candidate generation source 중 하나일 뿐이며 전체 SEC filer registry가 아니다. 따라서 다음은 의도적으로 놓칠 수 있다.

- 비상장 private filer
- ticker mapping에 없는 finance subsidiary
- 과거에는 filing했지만 현재 ticker registry에서 사라진 entity
- 이름이 변경돼 현재 title과 source name이 직접 일치하지 않는 후보 중 아직 legal-name graph를 확보하지 못한 CIK

이를 해결하기 위해 fuzzy web/company search를 도입하지 않는다.

다음 확장은 **SEC 공식 source에서 비상장/비ticker filer candidate CIK를 생성하는 방법**이어야 하며, source-date name confirmation + unique contract confirmation 규칙은 그대로 유지해야 한다.

## Safety properties

- 일반 회사명 언급만으로 CIK 경계를 넘지 않음
- fuzzy name matching 없음
- current ticker mapping 단독으로 CIK 확정하지 않음
- source-date filing existence 필요
- source-date legal-name confirmation 필요
- contract identity + party hard-filter 필요
- 복수 결과는 ambiguous
- explicit cross-CIK provenance와 별도 보존
- resolved exhibit도 authoritative debt row로 직접 승격하지 않음

이 설계의 목표는 recall을 최대화하는 것이 아니라, SEC provenance를 유지하면서 name-only external contract party 때문에 생기는 고가치 false negative를 제한적으로 제거하는 것이다.
