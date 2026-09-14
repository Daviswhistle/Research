# SEC Complete-Submission Legal-Name Fallback

## 목적

SEC `submissions/CIK##########.json`의 `name` / `formerNames`는 편리하지만 현재 시점 metadata다.

과거 replay에서는 다음 문제가 생길 수 있다.

```text
analysis cutoff: 2020-12-31
current SEC name today: New Name Inc.
submissions formerNames: incomplete
historical filing header: Old Name Inc.
```

현재 이름을 그대로 과거에 주입하면 look-ahead가 된다.

이 레이어는 cutoff 이전 filing의 **Complete submission text file** 안 `<SEC-HEADER>`를 별도 point-in-time source로 사용한다.

## SEC source

EDGAR filing detail은 accession별 complete submission text file을 제공한다.

```text
https://www.sec.gov/Archives/edgar/data/<CIK>/<ACCESSION_NO_DASHES>/<ACCESSION>.txt
```

예:

```text
.../000110465926054116/0001104659-26-054116.txt
```

Header에는 다음과 같은 filer identity fields가 존재할 수 있다.

```text
COMPANY CONFORMED NAME:
CENTRAL INDEX KEY:
FORMER CONFORMED NAME:
DATE OF NAME CHANGE:
```

`DATE OF NAME CHANGE`는 이 연구 엔진에서 **SEC identity metadata boundary**로 사용한다. 주법상 legal-effective date라고 자동 해석하지 않는다.

## Multi-entity submission guard

한 submission에는 여러 entity가 함께 존재할 수 있다.

예:

```text
FILER
ISSUER
REPORTING-OWNER
SUBJECT COMPANY
```

따라서 header 전체의 `FORMER CONFORMED NAME`을 모으면 안 된다.

Resolver는 먼저 각 entity section의:

```text
CENTRAL INDEX KEY
```

를 읽고 **target CIK와 정확히 일치하는 section만** 사용한다.

다른 filer/issuer/reporting owner의 former name은 절대 target entity alias에 들어가지 않는다.

## Datamodel

### `CompleteSubmissionFormerName`

```text
name
normalized_name
change_on
```

### `CompleteSubmissionNameEvidence`

```text
cik
accession_number
filing_date
source_url
current_name
normalized_current_name
former_names
warnings
```

`source_url`은 실제 complete-submission `.txt` URL이다.

## Historical guards

Header evidence는 다음 조건을 모두 만족해야 한다.

1. filing date <= analysis cutoff
2. SEC header 내부 CIK == target CIK
3. `DATE OF NAME CHANGE`가 존재하면 filing date보다 미래일 수 없음
4. malformed/future boundary는 자동 승격하지 않음

날짜 없는 former name은 identity evidence로 보존할 수 있지만 정확한 rename boundary를 발명하지 않는다.

## Reconciliation

기존 submissions graph와 complete-header evidence를 별도로 만든 뒤 reconcile한다.

출력에는 `canonical_status`, `comparisons`, `header_evidence`가 추가된다.

### canonical status

- `confirmed_by_header_and_submissions`
  - 두 source의 cutoff name이 일치
- `header_only_fallback`
  - submissions에서 cutoff name을 확정하지 못했지만 historical header가 확인
- `submissions_only`
  - usable header가 없음
- `submissions_newer_than_latest_header`
  - latest sampled header 뒤, cutoff 이전에 submissions metadata상 rename이 발생
- `conflict_header_preferred`
  - historical header와 undated/current submissions name이 충돌해 header를 보수적으로 사용

## Name-history comparison status

각 normalized name별 source 비교 상태:

- `confirmed_same_boundary`
  - 두 source가 name과 transition boundary 모두 일치
- `confirmed_name_boundary_unavailable`
  - name은 일치하지만 한 source에 정확한 boundary가 없음
- `boundary_conflict`
  - 같은 name의 transition boundary가 서로 다름
- `header_only`
  - complete header에만 존재
- `submissions_only`
  - submissions metadata에만 존재

`boundary_conflict`는 조용히 하나를 선택하지 않고 warning으로 남긴다.

## Canonical-name precedence

Historical header는 실제 cutoff 이전 filing에서 관찰된 이름이므로 current metadata보다 강한 point-in-time observation이다.

다만 latest sampled header 이후, cutoff 이전에 submissions metadata가 날짜가 있는 rename transition을 보여주면 그 더 최근 transition을 인정한다.

```text
header filing: 2021-06-30  -> Old Name
SEC rename boundary: 2021-10-27
analysis cutoff: 2021-12-31

=> New Name 사용 가능
```

반면 submissions current name에 transition date가 없고 historical header가 다른 이름을 증명하면:

```text
=> header name 사용
=> undated current name은 historical alias set에서 제외
```

이 규칙이 미래 current-name leakage를 막는다.

## Fetch behavior

`build_legal_name_alias_graph()`는 기존 API 이름을 유지한다.

실제 `SecClient`에서는:

```text
submissions metadata
    ↓
recent cutoff-safe SEC filings
    ↓
latest complete-submission headers (default 3)
    ↓
reconciliation
```

을 수행한다.

Complete-header fetch는 기존 SEC client의:

- User-Agent
- fair-access throttle
- HTTP session

을 그대로 사용한다.

Header fetch가 실패해도 submissions graph 자체를 실패시키지 않는다. 실패는 warning/fallback으로 처리한다.

## Cross-CIK

Cross-CIK 이름 이력도 같은 builder를 사용한다.

단, 순서는 계속 다음과 같다.

```text
explicit SEC locator
→ target CIK 확정
→ target CIK filings
→ submissions + complete-header legal-name reconciliation
```

회사명으로 CIK를 추정하는 기능은 없다.

## 의도적으로 하지 않는 것

- fuzzy legal-name matching
- name similarity로 CIK 탐색
- header의 다른 entity section 이름을 target CIK에 합치기
- 미래 filing/header를 historical cutoff에 사용
- conflicting boundary를 임의 평균/선택
- `DATE OF NAME CHANGE`를 corporate-law effective date로 단정

## Known limitations

1. 최근 3개 cutoff-safe filing header를 기본 sample로 사용한다. 아주 오래된 name-history가 최근 header에서 생략되는 issuer는 추가 historical scan이 필요할 수 있다.
2. SEC header 자체가 잘못됐거나 filings 간 history가 달라질 수 있으며, 이 경우 provenance와 conflict를 남기고 자동 법률 판단은 하지 않는다.
3. merger, reincorporation, successor/novation처럼 CIK continuity가 끊기는 사건은 legal-name alias와 별도의 lineage 문제다.
4. filing header와 charter/state filing의 법률적 effective date reconciliation은 아직 범위 밖이다.
