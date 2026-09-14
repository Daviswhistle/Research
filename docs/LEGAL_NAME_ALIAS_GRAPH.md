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

SEC는 이 구조에 current name과 former name 같은 filer metadata가 포함된다고 설명한다.

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

CIK는 filer에 부여되는 고유 식별자이므로 이름 변경 전후 identity의 deterministic anchor로 사용한다.

중요: submissions의 `formerNames.from/to`와 header의 `DATE OF NAME CHANGE`를 주법상 법적 이름변경 효력일로 단정하지 않는다. 이 날짜들은 이 연구 파이프라인에서 **SEC identity metadata의 시간 경계**로만 사용한다.

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
```

## Historical cutoff guard

현재 SEC metadata를 과거 분석에 그대로 넣으면 미래 이름이 유출될 수 있다.

예를 들어 현재 metadata가 다음과 같다고 하자.

```text
current: Meta Platforms, Inc.
former: Facebook Inc.
former.to: 2021-10-27
```

### cutoff = 2020-12-31

허용:

```text
canonical_name_as_of = Facebook Inc.
alias set = {facebook inc}
```

금지:

```text
facebook inc == meta platforms inc
```

2020년 분석에 2021년 이후 rename을 넣으면 hindsight다.

### cutoff = 2022-12-31

rename boundary가 cutoff 이전이므로:

```text
alias set = {
  facebook inc,
  meta platforms inc
}
```

가 허용된다.

## Complete-header fallback

submissions `formerNames`가 비어 있거나 current name의 역사적 유효시점을 설명하지 못하는 경우 complete-submission header를 fallback으로 사용한다.

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

근거 없는 `Future Name Inc.`는 historical alias set에서도 제외한다.

반대로 latest sampled header 이후 cutoff 이전에 날짜가 있는 submissions rename transition이 존재하면 더 최근 transition을 인정할 수 있다.

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

하나의 complete submission에는 filer, issuer, reporting owner, subject company가 함께 존재할 수 있다.

따라서 header 전체의 former name을 합치지 않는다.

```text
entity section
    ↓
CENTRAL INDEX KEY == target CIK
    ↓
그 section의 current/former name만 사용
```

다른 CIK의 이름은 alias graph에 들어올 수 없다.

## Party matching integration

기존 contract party matching은 role + normalized legal name을 hard filter로 사용한다.

이제 source-backed alias group을 명시적으로 전달할 수 있다.

```text
borrower: Facebook Inc.
borrower: Meta Platforms, Inc.
```

동일 CIK의 cutoff-safe alias graph가 둘을 연결하면 같은 borrower identity로 비교할 수 있다.

하지만:

```text
borrower: Facebook Inc.
guarantor: Meta Platforms, Inc.
```

처럼 role이 다르면 alias여도 match하지 않는다.

또한 alias graph가 없으면 이름이 달라진 두 party는 match하지 않는다.

## Contract identity integration

Locator-less contract resolver의 identity는 계속 다음 구조다.

```text
contract kind
+ exact execution date
+ original/restated state
+ role-aware party identity
```

법인명 rename은 party identity 비교에서만 bridge한다.

다음 조건은 바뀌지 않는다.

- contract kind가 달라지면 match하지 않는다.
- execution date가 달라지면 match하지 않는다.
- original agreement와 amended-and-restated agreement를 조용히 합치지 않는다.
- amendment/waiver/supplement를 original agreement로 승격하지 않는다.
- candidate가 source-specified party를 독립적으로 확인하지 못하면 match하지 않는다.

## Cross-CIK integration

Cross-CIK resolver가 explicit SEC locator로 foreign CIK를 발견하면 그 CIK에 대해서도 같은 reconciled name-history builder를 사용한다.

중요한 순서는 다음과 같다.

```text
explicit SEC CIK / Archives locator
    ↓
CIK 확정
    ↓
그 CIK의 submissions + historical complete-header 조회
```

절대 다음처럼 하지 않는다.

```text
company name
    ↓ fuzzy search
CIK 추정
```

따라서 legal-name alias graph는 cross-CIK traversal을 허가하는 근거가 아니다. 이미 SEC evidence로 확정된 CIK의 identity history를 보강하는 artifact다.

`cross_cik_graph.json`에는 발견된 CIK들의 cutoff-safe `legal_name_alias_graphs`도 함께 frozen 된다.

## Workspace artifacts

`distressed-equity-research`는 다음을 저장한다.

```text
legal_name_alias_graph.json
source_document_graph.json
cross_cik_graph.json
```

그리고 동일 내용이 frozen `research_packet.json`에도 들어간다.

자세한 complete-header parser/reconciliation 규칙은 [`SEC_COMPLETE_SUBMISSION_NAME_HEADERS.md`](SEC_COMPLETE_SUBMISSION_NAME_HEADERS.md)를 참고한다.

## 의도적으로 하지 않는 것

- edit distance / embedding similarity로 법인 alias 생성
- 비슷한 이름을 근거로 CIK 추정
- parent/subsidiary 이름이 비슷하다는 이유로 동일 entity 처리
- cutoff 이후 rename을 과거 계약에 적용
- SEC metadata boundary를 법률적 name-change effective date로 단정
- CIK가 다른 두 법인을 이름 alias만으로 합치기
- complete header의 다른 entity section 이름을 target CIK에 합치기

## Known limitations

1. complete-header fallback은 최근 cutoff-safe filing 3개를 기본 sample로 사용한다. 아주 오래된 history가 최근 header에서 빠지는 issuer는 추가 historical scan이 필요할 수 있다.
2. SEC submissions와 filing header 자체가 불일치할 수 있으며, 이 경우 `boundary_conflict`와 provenance를 남기고 자동 법률 판단은 하지 않는다.
3. merger, conversion, reincorporation, successor/novation처럼 **CIK continuity 자체로 설명되지 않는 법적 succession**은 name alias와 별개의 문제다.
4. foreign CIK 내부의 locator-less contract fallback을 그 foreign CIK의 alias graph로 다시 실행하는 단계는 별도 확장 대상이다.

현재 목표는 법인명 변경 때문에 과거 계약 연결이 끊기는 false negative를 줄이면서, fuzzy legal-entity matching이 만드는 false positive와 historical look-ahead를 피하는 것이다.