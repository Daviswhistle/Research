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

현재 SEC registry나 누적 CIK/name 파일에서 이름이 같다는 이유만으로 CIK를 확정하지 않는다.

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
- SEC 공식 누적 CIK/name 파일 `https://www.sec.gov/Archives/edgar/cik-lookup-data.txt`에서 **exact normalized legal-name**이 일치하는 filer name

SEC는 `cik-lookup-data.txt`를 모든 CIK와 entity name의 현재 목록으로 제공하며, 이름 변경 때문에 하나의 CIK가 여러 역사적 이름과 연결될 수 있고 더 이상 filing하지 않는 entity도 포함할 수 있다고 설명한다. 이 특성은 ticker가 없는 finance subsidiary, private filer, historical filer의 후보 CIK를 생성하는 데 유용하지만 historical identity authority 그 자체는 아니다.

후보 source는 각각 명시적으로 구분한다.

```text
known_sec_legal_name_graph
sec_company_tickers_candidate_only
sec_cik_lookup_data_candidate_only
```

다음은 허용하지 않는다.

```text
Target Finance LLC
≈ Target Finance Holdings LLC
≈ Target Financial LLC
≈ Target Finance Inc.
```

문장 유사도, token similarity, edit distance, embedding, parent/subsidiary 추론은 사용하지 않는다.

### 누적 CIK 파일 파싱

SEC 파일은 기본적으로:

```text
ENTITY NAME:CIK:
```

형식이다. Entity name 자체에 colon이 포함될 수 있으므로 왼쪽 `split(":")`을 쓰지 않고 trailing colon을 제거한 뒤 오른쪽에서 CIK를 분리한다.

예:

```text
11:11 CAPITAL CORP.:0001463262:
```

또한 이 파일은 대용량이므로 전체 registry를 Python dict로 영구 확장하지 않는다. 먼저 research packet의 source documents에서 필요한 normalized party name 집합을 수집한 뒤, SEC 파일을 한 번 선형 스캔해 그 이름들만 추출한다. Raw response는 같은 `SecClient` 인스턴스에서 캐시해 반복 다운로드를 막는다.

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

따라서 오늘의 ticker registry나 누적 CIK/name 파일에 이름이 존재해도 과거 source date의 SEC evidence가 해당 이름을 확인하지 않으면 후보는 탈락한다.

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

따라서 성공해도 name-origin 경계를:

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

유일하게 확인된 exhibit는 기존 source-backed debt pipeline으로 들어간다.

```text
confirmed external contract exhibit
  -> source spans
  -> field proposals
  -> debt instrument verification template
  -> provenance validation
  -> stable debt ledger
```

Named-entity resolution 자체는 debt principal, commitment, maturity 등을 authoritative value로 확정하지 않는다.

## Resolution priority and alternating closure

강한 locator 경로가 항상 먼저다.

```text
1. explicit same-CIK locator
2. same-CIK locator-less contract identity
3. explicit cross-CIK locator
4. foreign-CIK locator-less contract + fixed-point closure
5. source-confirmed named-entity contract fallback
6. named-resolved exhibit에서 explicit cross-CIK closure 재시작
7. 6번으로 새로 도달한 문서에서만 named resolver 재실행
8. 5~7을 bounded fixed point까지 반복
```

핵심 안전 규칙은 **named hop 사이에 explicit SEC locator hop이 반드시 하나 이상 있어야 한다**는 것이다.

허용:

```text
name-only A
  -> explicit B
  -> name-only C
  -> explicit D
  -> name-only E
```

허용하지 않음:

```text
name-only A
  -> name-only C
  -> name-only E
```

즉 `name-only -> name-only`를 직접 재귀하지 않는다. 추가 named pass는 이전 named-resolved exhibit가 실제 SEC Archives URL / CIK / accession locator를 노출하고, 그 explicit closure가 새로운 source document를 만들어야만 열린다.

## Global depth / node / iteration bounds

Workspace에서는 root source nodes와 foreign contract nodes를 global depth로 재기준화한 뒤 named resolver에 전달한다.

Foreign local graph node는:

```text
global_depth = entry_global_depth + local_node.depth
```

를 사용한다. `reference_depth` 밖의 node는 named-entity scan 대상이 아니다.

Named-entity 경계 자체도 한 depth를 소비한다.

```text
root source                              depth 0
  -> name-confirmed A exhibit            depth 1
  -> explicit B SEC locator              depth 2
  -> name-confirmed C exhibit            depth 3
```

고정점 엔진 `close_named_entity_fixed_point()`는 다음 세 가지를 동시에 제한한다.

- `max_depth`: 모든 named/explicit/foreign hop이 공유하는 global depth
- `max_nodes`: named resolver가 스캔한 unique source `(CIK, URL)`의 총 budget과 closure에 전달되는 node cap
- `max_iterations`: alternating named/explicit round cap, 기본 8

이미 처리한 `(CIK, source URL)`은 다시 named scan하지 않고, 이미 closure seed로 사용한 `(target CIK, target document URL)`도 다시 처리하지 않는다. Named graph의 candidate/resolution/document도 semantic key로 merge해 iteration마다 중복 레코드가 쌓이지 않는다.

## Alternating fixed point

현재 전체 흐름은 다음과 같다.

```text
root / existing foreign sources
    -> named resolver
    -> resolved_named_entity_contract A
    -> A exhibit explicit locator scan
    -> resolved_cross_cik B
    -> B same-CIK + locator-less foreign closure
    -> newly exposed B-source documents
    -> named resolver rerun
    -> resolved_named_entity_contract C
    -> C exhibit explicit locator scan
    -> ...
    -> no fresh source / depth exhausted / node budget exhausted / iteration cap
```

각 named pass에서 새로 확인된 target CIK의 point-in-time legal-name graph도 legal-name set에 추가한다. 이후 explicit/foreign contract matching에서 root alias나 다른 CIK alias가 섞이지 않는다.

`cross_cik_graph.json`에는 최종 fixed-point graph가 저장되고 다음 metadata도 기록된다.

```text
named_entity_fixed_point_iterations
```

`named_entity_contract_graph.json`은 첫 pass만이 아니라 모든 alternating pass의 candidates / resolutions / resolved documents를 merge한 aggregate graph다.

동일 동작은 다음 두 진입점 모두에 적용된다.

- `distressed-equity-research`
- `distressed-equity-sec-instruments`

## Coverage improvement

`company_tickers.json`만 사용할 때 놓치던 다음 후보를 이제 `cik-lookup-data.txt`에서 생성할 수 있다.

- 비상장/private SEC filer
- ticker가 없는 finance subsidiary
- 현재 ticker map에 없는 historical filer
- 과거 이름으로만 누적 CIK registry에 남아 있는 filer

단, 이 파일에는 funds와 individuals도 포함되고 historically cumulative이므로 exact name hit 자체는 신뢰 신호가 아니다. 기존 source-date confirmation과 unique-contract confirmation이 반드시 뒤따른다.

## Remaining false-negative boundary

모든 name-only entity가 해결되는 것은 아니다.

- SEC 누적 CIK/name 목록에도 해당 이름이 없는 경우
- source contract가 약칭/trade name만 쓰고 legal name을 쓰지 않은 경우
- source-date legal-name evidence가 최근 complete-submission header 범위 밖에 있어 확인되지 않는 경우
- target filer가 public EDGAR filing history를 갖지 않아 contract confirmation이 불가능한 경우
- named target 뒤에 explicit SEC locator가 전혀 없고, 오직 또 다른 name-only party만 등장하는 경우

마지막 항목은 의도적인 precision boundary다. 현재 구현은 alternating `named -> explicit -> named` 체인은 반복하지만, SEC locator 없이 이름 추론만 연속되는 체인은 열지 않는다.

## Safety properties

- 일반 회사명 언급만으로 CIK 경계를 넘지 않음
- fuzzy name matching 없음
- current ticker mapping 단독으로 CIK 확정하지 않음
- cumulative CIK/name hit 단독으로 CIK 확정하지 않음
- source-date filing existence 필요
- source-date legal-name confirmation 필요
- contract identity + party hard-filter 필요
- 복수 결과는 ambiguous
- name-origin provenance와 explicit cross-CIK provenance를 별도 보존
- named hop과 explicit hop 모두 동일 global-depth budget을 소비
- direct name-only/name-only recursion 금지
- unique source / named target semantic dedupe
- iteration cap으로 cyclic graph 폭주 방지
- resolved exhibit도 authoritative debt row로 직접 승격하지 않음
- closure 이후 최종 cross graph를 재직렬화해 provenance 누락 방지

현재 구조는 다음과 같다.

```text
SEC cumulative all-CIK candidate
    -> source-date SEC identity confirmation
    -> unique historical contract confirmation
    -> explicit cross-CIK restart when present
    -> newly exposed source에서 named resolution 재실행
    -> bounded alternating fixed point
    -> source-backed debt extraction
```

이 설계의 목표는 recall을 무제한으로 높이는 것이 아니라, SEC provenance를 유지하면서 ticker가 없는 filer와 historical legal name 때문에 생기는 고가치 false negative를 제한적으로 제거하는 것이다.
