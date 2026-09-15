# Point-in-Time Legal-Name Alias Graph

## 목적

동일한 CIK의 법인이 이름을 바꾸면 오래된 계약과 최근 filing에서 party 이름이 달라질 수 있다.

```text
old contract: Facebook Inc., as Borrower
later filing: Meta Platforms, Inc., as Borrower
```

문자열 fuzzy matching으로 둘을 합치면 동명이인/자회사/유사 법인명을 잘못 합칠 위험이 있다.

이 레이어의 원칙은 다음과 같다.

```text
same CIK
+ SEC source-backed name history
+ rename boundary <= analysis cutoff
=> alias 허용
```

반대로 이름이 비슷하다는 이유만으로 alias를 만들지 않는다.

## Sources

기본 metadata source는 SEC submissions endpoint다.

```text
https://data.sec.gov/submissions/CIK##########.json
```

추가 point-in-time source로 cutoff 이전 filing의 **Complete submission text file** `<SEC-HEADER>`를 사용한다.

```text
https://www.sec.gov/Archives/edgar/data/<CIK>/<ACCESSION_NO_DASHES>/<ACCESSION>.txt
```

Header에서 target CIK의 entity block만 선택해 다음을 읽는다.

```text
COMPANY CONFORMED NAME
CENTRAL INDEX KEY
FORMER CONFORMED NAME
DATE OF NAME CHANGE
```

CIK는 filer에 부여되는 identity anchor로 사용한다. submissions의 `formerNames.from/to`와 header의 `DATE OF NAME CHANGE`를 주법상 법적 이름변경 효력일로 단정하지 않고, 이 파이프라인의 **SEC identity metadata 시간 경계**로만 사용한다.

## Datamodel

### `LegalNameRecord`

```text
name
normalized_name
valid_from
valid_to
source_kind
source
evidence_on
```

### `LegalNameTransition`

```text
from_name
to_name
effective_on
source_kind
source
```

### `ReconciledLegalNameAliasGraph`

```text
cik
analysis_date
canonical_name_as_of
canonical_status
records
transitions
comparisons
header_evidence
warnings
header_scan_status
header_scan_filings_considered
header_scan_filings_fetched
header_scan_oldest_filing_on
```

마지막 네 필드는 historical complete-header scan이 실제로 얼마나 깊게 수행됐는지 frozen provenance로 남긴다.

## Historical cutoff guard

현재 SEC metadata를 과거 분석에 그대로 넣으면 미래 이름이 유출될 수 있다.

예를 들어 현재 metadata가 다음과 같다고 하자.

```text
current: Meta Platforms, Inc.
former: Facebook Inc.
former.to: 2021-10-27
```

cutoff가 2020-12-31이면 `Facebook Inc.`만 허용하고 미래 `Meta Platforms, Inc.` alias를 넣지 않는다. cutoff가 2022-12-31이면 rename boundary가 cutoff 이전이므로 두 이름을 같은 CIK의 cutoff-safe alias로 사용할 수 있다.

## Complete-header reconciliation

submissions `formerNames`가 비어 있거나 current name의 역사적 유효시점을 설명하지 못하는 경우 complete-submission header를 사용한다.

예:

```text
analysis cutoff: 2020-12-31
submissions current name today: Future Name Inc.
submissions formerNames: []
2020-11-01 complete header: Historical Name Inc.
```

이 경우 현재 이름을 과거로 backfill하지 않는다.

```text
canonical_name_as_of = Historical Name Inc.
canonical_status = conflict_header_preferred
```

반대로 latest sampled header 이후 cutoff 이전에 날짜가 있는 submissions rename transition이 존재하면 그 더 최근 transition을 인정할 수 있다.

## Adaptive historical header deep scan

기존 구현은 cutoff 이전 최신 filing 3개의 complete-submission header만 확인했다. 최근 header가 오래된 legal-name history를 싣지 않는 issuer에서는 과거 계약 party alias를 놓칠 수 있었다.

현재 public builder는 다음 순서로 동작한다.

```text
SEC submissions history
    ↓
latest 3 complete-submission headers
    ↓
충분한가?
    ├─ yes → stop
    └─ no  → oldest filing probe
                ↓
             필요하면 temporal midpoint scan
                ↓
             max_header_filings bound
```

기본값:

```text
header_recent_limit = 3
header_max_filings = 12
deep_scan_headers = True
```

### Recent-only 즉시 종료

recent headers가 submissions에 이미 기록된 rename history를 모두 corroborate하면 older filing을 추가 요청하지 않는다.

예를 들어 submissions가 `{Old Name, New Name}`을 이미 가지고 있고 recent header도 두 이름을 모두 확인하면:

```text
header_scan_status = recent_only_sufficient
```

으로 종료한다.

### Oldest filing probe

submissions가 current name 하나만 갖고 있는데 훨씬 오래된 filing history가 존재하면 “옛 이름이 없었다”고 단정할 수 없다. 따라서 최소 한 번 oldest available filing의 header를 본다.

latest와 oldest의 current name이 같고 새로운 alias도 발견되지 않은 일반 scan은:

```text
header_scan_status = deep_probe_stable_endpoints
```

로 종료할 수 있다. 이는 무제한 exhaustive scan이 아니라 bounded confidence probe다.

### Temporal midpoint scan

oldest probe에서 다른 이름이 발견되거나, 특정 historical alias를 반드시 확인해야 하는데 아직 찾지 못했다면 quarter-by-quarter로 내려가지 않는다.

대신 전체 filing timeline에서:

```text
oldest
midpoint
quarter points
...
```

처럼 recursive temporal midpoint를 선택한다. 긴 filing history에서 제한된 요청으로 시간축 전체를 넓게 덮기 위한 전략이다.

최대 요청 수에 도달했는데 전체 history를 다 보지 못하면:

```text
header_scan_status = deep_scan_bounded
```

과 함께 다음 warning을 남긴다.

```text
older/intermediate alias history may remain incomplete
```

filing 수가 bound 이하라 전부 확인하면:

```text
header_scan_status = deep_scan_complete
```

이다.

## Targeted historical-name confirmation

일반 graph 구축보다 더 강한 경우가 있다. Named external-entity resolver는 source contract에 실제로 등장한 특정 법인명이 후보 CIK의 역사적 alias였는지를 확인해야 한다.

이때 builder에:

```python
required_aliases=(party.normalized_name,)
```

를 전달한다.

따라서 recent headers와 oldest header의 이름이 우연히 같더라도 필요한 과거 이름이 아직 확인되지 않았다면 stable-endpoint stop을 사용하지 않고 temporal midpoint scan을 계속한다.

필요한 이름을 deep scan에서 찾으면:

```text
header_scan_status = target_alias_found_deep
```

이 된다.

반대로 bounded scan이 끝날 때까지 못 찾으면 자동으로 이름을 추정하지 않고 warning을 남긴다. Candidate registry name 자체는 historical identity proof가 아니다.

이 targeted scan은 다음 체인의 source-date confirmation에 직접 사용된다.

```text
name-only contract party
    ↓
SEC candidate CIK generation
    ↓
required historical alias deep scan
    ↓
source-date legal-name confirmation
    ↓
unique historical contract confirmation
```

## Reconciliation states

### `canonical_status`

- `confirmed_by_header_and_submissions`
- `header_only_fallback`
- `submissions_only`
- `submissions_newer_than_latest_header`
- `conflict_header_preferred`

### per-name `comparisons[].status`

- `confirmed_same_boundary`
- `confirmed_name_boundary_unavailable`
- `boundary_conflict`
- `header_only`
- `submissions_only`

`boundary_conflict`는 한 source를 조용히 덮어쓰지 않고 warning과 provenance로 남긴다.

## Multi-entity submission guard

하나의 complete submission에는 filer, issuer, reporting owner, subject company가 함께 존재할 수 있다. Header 전체의 former name을 합치지 않고 `CENTRAL INDEX KEY == target CIK`인 entity section만 사용한다.

## Party / contract integration

Contract party matching은 role + normalized legal name을 hard filter로 사용한다. Source-backed alias graph가 있을 때만 old/new legal name을 bridge한다.

```text
borrower: Facebook Inc.
borrower: Meta Platforms, Inc.
```

은 동일 CIK alias evidence가 있으면 연결할 수 있지만:

```text
borrower: Facebook Inc.
guarantor: Meta Platforms, Inc.
```

처럼 role이 다르면 match하지 않는다.

Locator-less contract resolver도 계속 다음 조건을 모두 요구한다.

```text
contract kind
+ exact execution date
+ original/restated state
+ role-aware party identity
```

## Cross-CIK integration

Cross-CIK resolver가 explicit SEC locator로 foreign CIK를 발견하면 그 CIK에 대해서도 같은 adaptive reconciled name-history builder를 사용한다.

순서:

```text
explicit SEC CIK / Archives locator
    ↓
CIK 확정
    ↓
그 CIK의 submissions + adaptive historical complete-header scan
```

절대 회사명 fuzzy search로 CIK를 추정한 뒤 alias graph를 authority로 사용하지 않는다.

## Fixed-limit compatibility helper

`build_reconciled_legal_name_alias_graph(..., header_filing_limit=N)`은 실험/테스트용 exact fixed-limit helper로 유지한다. 이 helper는 이제 submissions base graph에서 직접 출발하므로 public adaptive builder를 다시 호출해 **double reconciliation / duplicate header fetch**를 하지 않는다.

## Workspace artifacts

`distressed-equity-research`는 다음을 저장한다.

```text
legal_name_alias_graph.json
source_document_graph.json
cross_cik_graph.json
```

동일 내용은 frozen `research_packet.json`에도 들어가며 scan status/count도 함께 보존된다.

자세한 complete-header parser 규칙은 [`SEC_COMPLETE_SUBMISSION_NAME_HEADERS.md`](SEC_COMPLETE_SUBMISSION_NAME_HEADERS.md)를 참고한다.

## 의도적으로 하지 않는 것

- edit distance / embedding similarity로 법인 alias 생성
- 비슷한 이름을 근거로 CIK 추정
- parent/subsidiary 이름이 비슷하다는 이유로 동일 entity 처리
- cutoff 이후 rename을 과거 계약에 적용
- SEC metadata boundary를 법률적 name-change effective date로 단정
- CIK가 다른 두 법인을 이름 alias만으로 합치기
- complete header의 다른 entity section 이름을 target CIK에 합치기
- long-history filer의 모든 complete-submission text를 무조건 exhaustive fetch

## Known limitations

1. 기본 deep scan은 최신 3개 + adaptive temporal anchors, 최대 12개 header다. 12개보다 긴 history에서 아주 짧게 사용된 중간 이름은 targeted alias가 없는 일반 scan에서 여전히 놓칠 수 있다. 이 경우 `deep_scan_bounded` warning을 신뢰해야 한다.
2. latest/oldest endpoint가 같은 이름인 일반 scan은 rename-and-revert 같은 드문 중간 변화를 놓칠 수 있다. 특정 이름을 확인해야 하는 named-entity path에서는 `required_aliases`가 이 early stop을 해제한다.
3. SEC submissions와 filing header 자체가 불일치할 수 있으며, 이 경우 `boundary_conflict`와 provenance를 남기고 자동 법률 판단은 하지 않는다.
4. merger, conversion, reincorporation, successor/novation처럼 **CIK continuity 자체로 설명되지 않는 법적 succession**은 name alias와 별개의 문제다.

목표는 모든 역사적 법인명을 추측하는 것이 아니라, SEC provenance와 request budget을 보존하면서 오래된 계약의 legal-name false negative를 실질적으로 줄이는 것이다.
