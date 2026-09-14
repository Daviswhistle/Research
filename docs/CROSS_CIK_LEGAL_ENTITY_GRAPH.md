# Cross-CIK SEC Source / Legal-Entity Graph

## 목적

모회사 filing이 부채 계약의 실제 borrower/issuer인 별도 등록 자회사 CIK의 filing 또는 exhibit를 직접 참조하는 경우, 단일 CIK 내부만 탐색하면 원계약을 놓칠 수 있다.

이 레이어는 **SEC 원문이 target CIK를 직접 증명하는 경우에만** CIK 경계를 넘는다.

```text
root issuer filing
    ↓ explicit SEC Archives href / explicit CIK
foreign CIK
    ↓ point-in-time filings_as_of(cutoff)
exact accession / form-date
    ↓ filing index
exact exhibit / filename
    ↓
source span extraction
    ↓
DebtInstrumentSnapshot verification
```

회사 이름이 비슷하다는 이유만으로 CIK를 검색하거나 추정하지 않는다.

## Cross-CIK traversal을 허용하는 증거

현재 target CIK는 다음 중 하나가 원문에 있을 때만 사용한다.

1. SEC Archives URL

```text
/Archives/edgar/data/2222222222/000222222220000010/ex10-1.htm
```

절대 URL과 상대 `href=/Archives/...`를 모두 지원한다.

2. 명시적 CIK label

```text
CIK No. 2222222222
```

3. exact accession

```text
0002222222-20-000010
```

accession prefix가 filer CIK를 직접 식별할 수 있는 경우다.

반대로 다음만 있는 경우 cross-CIK 탐색을 시작하지 않는다.

```text
ABC Borrower LLC
ABC Finance Subsidiary
our financing subsidiary
```

이름→CIK fuzzy resolution은 false-positive contract linkage 위험이 너무 크기 때문이다.

## Historical guard

외부 CIK에서도 동일한 point-in-time 규칙을 적용한다.

```text
target filing date <= analysis cutoff
target filing date <= referencing source filing date
```

따라서 2021년 source가 2022년 foreign filing을 가리키도록 잘못 매칭되는 경우는 `blocked_temporal`로 거부한다.

foreign CIK의 filing universe는 `SecClient.filings_as_of(target_cik, analysis_date)`로 별도 구성한다.

## Resolution status

### `resolved_cross_cik`

foreign CIK + filing + 특정 exhibit까지 SEC source로 재확인했다.

### `unresolved`

foreign CIK는 명시됐지만 accession/form/date로 filing을 하나로 확정하지 못했다.

### `filing_resolved`

foreign filing은 확정했지만 특정 exhibit를 하나로 좁히지 못했다.

### `filing_resolved_document_unavailable`

filing은 확정했지만 filing index를 읽지 못했다.

### `target_cik_unavailable`

target CIK의 point-in-time filing universe를 가져오지 못했다.

### `blocked_cutoff`

reference가 analysis cutoff 이후 filing을 요구한다.

### `blocked_temporal`

target filing이 referencing source보다 나중이다.

## foreign document 내부 재귀

foreign exhibit를 찾은 뒤 그 문서 내부의 ordinary same-CIK incorporation reference도 놓치지 않는다.

```text
Parent CIK
  ↓ explicit foreign CIK link
Borrower CIK EX-10.1
  ↓ same-CIK incorporation
Borrower CIK older amendment/original agreement
```

foreign document 안의 same-CIK chain은 기존 `source_graph` resolver를 재사용한다.

그리고 그 결과 문서에 또 다른 explicit foreign CIK reference가 있으면 cross-CIK scanner가 다시 처리한다.

재귀 상한은 기존 source graph와 동일하게 depth/node budget으로 제한한다.

## Legal-entity evidence graph

문서 이동 graph와 법인관계 graph는 분리한다.

Cross-CIK source graph가 어떤 문서를 어디에서 찾았는지를 기록한다면, legal-entity evidence는 원문에 **명시적으로 쓰인 관계만** 기록한다.

### Entity node

```text
cik:2222222222
```

CIK가 identity key다. 이름은 원문에서 확인되는 경우에만 metadata로 붙는다.

### Role evidence

현재 다음 명시 role을 보존한다.

- borrower / co-borrower
- issuer
- guarantor / parent guarantor

예:

```text
ABC Borrower LLC (CIK No. 2222222222), as Borrower
```

이는 `cik:2222222222`에 borrower role evidence를 추가한다.

Role은 법인간 지배관계를 자동 의미하지 않는다.

### Parent/subsidiary relation

다음처럼 target CIK와 관계가 같은 source span에 명시된 경우만 relation edge를 만든다.

```text
ABC Borrower LLC (CIK No. 2222222222),
a wholly owned subsidiary of the Company
```

결과:

```text
cik:2222222222
    --wholly_owned_subsidiary_of-->
cik:<source filer CIK>
```

`subsidiary of the Company / Registrant / Issuer`처럼 source filer를 명시적으로 가리키는 표현만 현재 자동화한다.

이름이 같거나 경제적으로 관계가 있어 보인다는 이유로 parent/subsidiary edge를 만들지 않는다.

## Artifact

`distressed-equity-research` workspace에는 기본적으로 다음이 추가된다.

```text
cross_cik_graph.json
```

그리고 동일 내용이 frozen `research_packet.json`의:

```text
cross_cik_graph
```

필드에도 저장된다.

따라서 후속 agent 검증 시점에 cross-CIK source path가 달라지지 않는다.

## CLI

### Research workspace

```bash
distressed-equity-research \
  --ticker PARENT \
  --analysis-date 2022-12-31 \
  --workspace output/parent_2022-12-31 \
  --cross-cik-max-nodes 80
```

끄려면:

```bash
--no-cross-cik
```

### SEC instrument extractor

```bash
distressed-equity-sec-instruments \
  --ticker PARENT \
  --analysis-date 2022-12-31 \
  -o output/debt_instrument_sources.json \
  --graph-output output/source_document_graph.json \
  --cross-cik-output output/cross_cik_graph.json
```

## 일부러 자동화하지 않는 것

- 회사명만으로 CIK 검색/선택
- subsidiary 이름과 public company 이름의 fuzzy match
- 현재 SEC company name을 과거 시점 entity name으로 소급 사용
- source evidence 없는 parent/subsidiary 추론
- borrower라는 이유만으로 parent/guarantor 관계 추론
- target filing이 source보다 나중인데 현재는 존재한다는 이유로 연결
- cross-CIK 계약의 legal successor / novation / extinguishment 판정

## 현재 남은 병목

1. source-backed legal-name alias / historical name-change graph
2. explicit parent CIK가 이름으로만 쓰이고 Archives locator가 없는 경우의 안전한 resolver
3. distant source-span clustering
4. PDF/image/table-heavy exhibit extraction
5. debt exchange / modification / extinguishment linkage

현재 목표는 **CIK 경계를 넘어야 할 때만 넘어가되, 그 경계를 넘는 근거 자체도 historical SEC source로 감사 가능하게 만드는 것**이다.
