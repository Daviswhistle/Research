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

## Source

기본 source는 SEC submissions endpoint다.

```text
https://data.sec.gov/submissions/CIK##########.json
```

SEC는 이 구조에 current name과 former name 같은 filer metadata가 포함된다고 설명한다.

CIK는 filer에 부여되는 고유 식별자이므로 이름 변경 전후 identity의 deterministic anchor로 사용한다.

현재 구현은 submissions의 `name`과 `formerNames` metadata를 사용한다.

중요: `formerNames.from/to`를 주법상 법적 이름변경 효력일로 단정하지 않는다. 이 날짜는 이 연구 파이프라인에서 **SEC identity metadata의 시간 경계**로만 사용한다.

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

`effective_on`은 현재 source가 제공하는 SEC metadata transition boundary다. 법률적 charter amendment effective date라는 뜻은 아니다.

### `LegalNameAliasGraph`

```text
cik
analysis_date
canonical_name_as_of
records
transitions
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

## Future-current-name withholding

더 까다로운 경우도 있다.

현재 SEC `name`은 미래 이름인데 `formerNames` history가 모두 분석 cutoff 뒤에만 존재할 수 있다.

이 경우 현재 이름을 과거 이름으로 추정하지 않는다.

```text
canonical_name_as_of = null
```

로 남긴다.

즉 모르는 것을 현재 이름으로 backfill하지 않는다.

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

Cross-CIK resolver가 explicit SEC locator로 foreign CIK를 발견하면 그 CIK에 대해서도 name-history graph를 수집한다.

중요한 순서는 다음과 같다.

```text
explicit SEC CIK / Archives locator
    ↓
CIK 확정
    ↓
그 CIK의 submissions name history 조회
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

`distressed-equity-sec-instruments`에서는 독립적으로 다음을 출력할 수 있다.

```bash
distressed-equity-sec-instruments \
  --ticker META \
  --analysis-date 2020-12-31 \
  --legal-name-output output/legal_name_alias_graph.json \
  --graph-output output/source_document_graph.json \
  --cross-cik-output output/cross_cik_graph.json \
  -o output/debt_instrument_sources.json
```

## 의도적으로 하지 않는 것

- edit distance / embedding similarity로 법인 alias 생성
- 비슷한 이름을 근거로 CIK 추정
- parent/subsidiary 이름이 비슷하다는 이유로 동일 entity 처리
- cutoff 이후 rename을 과거 계약에 적용
- SEC metadata boundary를 법률적 name-change effective date로 단정
- CIK가 다른 두 법인을 이름 alias만으로 합치기

## Known limitations

1. submissions `formerNames`가 모든 역사적 법적 이름 변화를 완벽히 표현한다고 가정하지 않는다.
2. filing complete-submission header의 `FORMER CONFORMED NAME` / `DATE OF NAME CHANGE`를 별도 교차검증하는 레이어는 아직 없다.
3. merger, conversion, reincorporation, successor/novation처럼 **CIK continuity 자체로 설명되지 않는 법적 succession**은 name alias와 별개의 문제다.
4. foreign CIK 내부의 locator-less contract fallback을 그 foreign CIK의 alias graph로 다시 실행하는 단계는 별도 확장 대상이다.

현재 목표는 법인명 변경 때문에 과거 계약 연결이 끊기는 false negative를 줄이면서, fuzzy legal-entity matching이 만드는 false positive와 historical look-ahead를 피하는 것이다.
