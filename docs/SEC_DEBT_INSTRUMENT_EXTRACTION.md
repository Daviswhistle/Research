# SEC Debt Instrument Source Extraction

## 목적

`distressed-equity-sec-instruments`는 10-K/10-Q/8-K의 primary document에서 채무 숫자를 추측하는 대신, 같은 filing에 첨부된 **indenture / credit agreement / amendment / waiver / security agreement exhibit 원문**을 찾아 instrument-level stable ledger의 입력 후보를 만든다.

이 단계의 핵심 원칙은 다음과 같다.

```text
SEC filing index
  ↓
debt-relevant EX-4 / EX-10 candidate
  ↓
source exhibit text
  ↓
field-level proposals + exact source span
  ↓
human/agent verification
  ↓
source_refs / accession validation
  ↓
DebtInstrumentSnapshot
  ↓
stable-ID debt ledger
```

Deterministic extractor의 결과는 최종 debt schedule이 아니다.

## SEC archive source

SEC는 EDGAR archive의 각 디렉터리에 `index.html`, `index.xml`, `index.json` 구조를 제공한다.

공식 설명:

- https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data

Filing detail의 Document Format Files 표에는 sequence, description, document filename, type, size가 포함된다. 이 도구는 filing index page를 SEC fair-access throttle을 통해 읽고 document metadata를 보존한다.

## Document selection

우선순위가 높은 문서는 다음과 같다.

- `EX-4.*`: indenture, note/security-holder rights 관련 문서 후보
- `EX-10.*`: credit agreement, material debt contract 후보
- debt keyword가 description/filename에 명시된 amendment, waiver, security/guarantee document

`EX-31`, `EX-32`, `EX-101` 등은 제외한다.

`EX-10`은 고용계약 등 비채무 계약도 포함할 수 있으므로 metadata만으로 채무라고 확정하지 않는다. 실제 문서를 fetch한 뒤 debt term이 없으면 instrument candidate를 만들지 않는다.

## Field-level provenance

원문 block마다 `span_id`를 만든다.

예:

```text
0000123456-22-000100:2:s1
```

각 field proposal은 다음을 보존한다.

```json
{
  "field": "maturity_year",
  "value": 2028,
  "confidence": "high",
  "source_span_id": "0000123456-22-000100:2:s1",
  "basis": "due year embedded in notes title"
}
```

현재 deterministic extractor가 제안할 수 있는 주요 필드는 다음과 같다.

- instrument name/type
- explicit coupon
- explicit maturity date/year
- aggregate/principal amount
- commitment/facility size
- CUSIP / ISIN
- SOFR benchmark and explicit spread
- senior/secured indicators

## High-confidence prefill

`debt_instrument_template.json`은 high-confidence field만 미리 채운다.

예를 들어:

```text
5.25% Senior Secured Notes due 2028
aggregate principal amount of $500 million
CUSIP No. 123456789
```

처럼 instrument와 term이 명시적으로 연결된 경우 coupon, maturity, principal, CUSIP을 template에 제안할 수 있다.

하지만 template에는 항상 review warning과 `source_refs`가 남는다. 최종 ledger에 넣기 전 source exhibit에서 다시 확인해야 한다.

Medium-confidence seniority/security 분류나 문서명 기반 instrument type은 자동 snapshot field로 승격하지 않는다.

## 실행

```bash
export SEC_USER_AGENT="Research your-email@example.com"

distressed-equity-sec-instruments \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --filing-limit 12 \
  -o output/cvna_debt_sources.json \
  --task-output output/cvna_debt_task.json \
  --template-output output/cvna_debt_instruments.json
```

검증을 끝낸 template은 source packet과 함께 ledger로 넘긴다.

```bash
distressed-equity-debt-ledger output/cvna_debt_instruments.json \
  --source-packet output/cvna_debt_sources.json \
  -o output/cvna_debt_ledger.json
```

`--source-packet`이 있으면 다음을 강제한다.

- 모든 snapshot은 하나 이상의 `source_refs`를 가져야 한다.
- 모든 ref는 frozen source packet의 실제 span ID여야 한다.
- `source_accession`은 cited spans의 accession과 일치해야 한다.
- snapshot `as_of_date`는 cited exhibit 공개일보다 빠를 수 없다.
- snapshot `as_of_date`는 workspace cutoff보다 늦을 수 없다.

따라서 verified instrument JSON에 출처 없는 숫자를 임의로 추가해 stable ledger로 보내는 경로를 차단한다.

## Research workspace

`distressed-equity-research`에서는 다음 artifact가 자동 생성된다.

```text
research_packet.json
capital_stack.json
capital_stack_diff.json
debt_instrument_sources.json
debt_instrument_task.json
debt_instrument_template.json
tasks.json
summary.md
```

`research_packet.json`은 frozen point-in-time input이다. 검증 후 stable debt ledger는 별도 artifact로 유지한다.

Workspace의 `--debt-instruments` 경로는 source packet이 존재하면 위 provenance validation을 자동 적용한다.

## 일부러 자동화하지 않는 것

다음은 deterministic regex 결과만으로 확정하지 않는다.

- 한 문단의 여러 dollar amount 중 어떤 것이 principal인지 애매한 경우
- debt exchange에서 구채권과 신채권의 법적 동일성
- amendment가 modification인지 extinguishment인지에 대한 회계 결론
- guarantor/borrower 범위의 법률적 해석
- permitted debt basket / restricted payment capacity
- covenant add-back의 법적 허용 여부

이 영역은 source-aware agent/analyst 검증을 거친 뒤 구조화한다.

## 알려진 한계

- 최근 filing에 원계약 exhibit가 첨부되어 있지 않고 과거 filing을 incorporation by reference하는 경우 `--filing-limit` 범위 밖의 원계약을 놓칠 수 있다.
- PDF/image exhibit는 현재 text/HTML extraction 대상이 아니다.
- 문서 내 표가 복잡하게 flatten된 경우 field proposal이 누락될 수 있다.
- 동일 instrument의 term들이 멀리 떨어진 section에 있는 경우 candidate template이 여러 행으로 나뉠 수 있다. Verification 단계에서 source refs를 합쳐 하나의 snapshot으로 정리해야 한다.

따라서 이 도구의 역할은 **원문 탐색 비용을 크게 줄이면서 provenance를 잃지 않는 것**이지, 계약서를 regex만으로 완전 해석하는 것이 아니다.
