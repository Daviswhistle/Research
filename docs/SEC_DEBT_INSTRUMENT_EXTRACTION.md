# SEC Debt Instrument Source Extraction

## 목적

`distressed-equity-sec-instruments`는 10-K/10-Q/8-K의 primary document에서 채무 숫자를 추측하는 대신, 같은 filing에 첨부된 **indenture / credit agreement / amendment / waiver / security agreement exhibit 원문**을 찾아 instrument-level stable ledger의 입력 후보를 만든다.

이 단계의 핵심 원칙은 다음과 같다.

```text
SEC filing index
  ↓
debt-relevant EX-4 / EX-10 candidate
  ↓
source exhibit
  ↓
prose span / HTML table row / visual deferral
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

SEC filing detail의 Document Format Files 표에서 sequence, description, document filename, type, size를 읽고 document metadata를 보존한다.

대표 source:

- `EX-4.*`: indenture, note/security-holder rights 관련 문서 후보
- `EX-10.*`: credit agreement, material debt contract 후보
- debt keyword가 description/filename에 명시된 amendment, waiver, security/guarantee document

`EX-31`, `EX-32`, `EX-101` 등은 제외한다.

`EX-10`은 고용계약 등 비채무 계약도 포함할 수 있으므로 metadata만으로 채무라고 확정하지 않는다.

## Prose source-span extraction

일반 prose exhibit는 debt-relevant block마다 `span_id`를 만든다.

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

동일 exhibit 안에서 term이 멀리 떨어져 있으면 [`DEBT_EVIDENCE_CLUSTERING.md`](DEBT_EVIDENCE_CLUSTERING.md)의 document-local clustering 규칙을 적용한다.

## HTML table 구조 보존

Debt table은 더 이상 `html_to_text` 결과만으로 처리하지 않는다.

```text
HTML table
    ↓
rowspan / colspan grid expansion
    ↓
column header normalization
    ↓
one data row = one structured source span
    ↓
same-row deterministic field proposals
```

예:

```text
Instrument                            Principal ($mm)  Maturity     Coupon  CUSIP
5.25% Senior Secured Notes due 2028   500              2028-06-15   5.25%   123456789
6.00% Senior Secured Notes due 2030   400              2030-06-15   6.00%   987654321
```

각 row는 다음 형태의 span ID를 가진다.

```text
<accession>:<sequence>:t<table_index>:r<row_index>
```

그리고 candidate provenance는:

```text
cluster_status = table_row
cluster_basis = [same_html_table_row, header_mapped_columns]
```

로 남는다.

중요한 안전 규칙:

- row 1 principal을 row 2 CUSIP과 결합하지 않는다.
- `rowspan` / `colspan`을 column grid로 확장해 header association을 유지한다.
- header의 `millions / billions / thousands` 단위를 numeric cell에 적용한다.
- table body는 prose extractor에서 제거해 flattened duplicate candidate를 만들지 않는다.

자세한 규칙은 [`STRUCTURED_DEBT_EXTRACTION.md`](STRUCTURED_DEBT_EXTRACTION.md)를 참고한다.

## 지원 field

현재 deterministic extractor가 제안할 수 있는 주요 필드는 다음과 같다.

- instrument name/type
- principal / face amount
- outstanding amount (medium-confidence principal candidate)
- commitment / facility size
- drawn / available
- maturity date/year
- coupon / interest rate
- benchmark / spread
- seniority / secured
- currency
- CUSIP / ISIN

## PDF / image safety boundary

현재 기본 pipeline은 PDF/image binary를 response text로 강제 decode해 regex extraction하지 않는다.

다음 source는 자동 text extraction 전에 defer한다.

```text
.pdf
.png
.jpg / .jpeg
.gif
.tif / .tiff
.bmp
.webp
GRAPHIC document type
```

HTML wrapper 안에 image만 있고 visible text가 너무 적은 exhibit도 defer한다.

Frozen packet에는 machine-readable warning을 남긴다.

```text
VISUAL_EXTRACTION_REQUIRED|
accession=...|
document=...|
document_type=...|
media_type=pdf|image|image_heavy_html|
url=...|
reason=...
```

이렇게 함으로써 PDF의 binary bytes나 scanned image를 prose처럼 오인해 잘못된 debt association을 만드는 경로를 막는다.

## High-confidence prefill

`debt_instrument_template.json`은 high-confidence field만 미리 채운다.

예를 들어:

```text
5.25% Senior Secured Notes due 2028
aggregate principal amount of $500 million
CUSIP No. 123456789
```

처럼 instrument와 term이 명시적으로 연결된 경우 coupon, maturity, principal, CUSIP을 template에 제안할 수 있다.

Table row도 같은 원칙을 따른다. Header/cell 구조가 명확한 동일 row의 term만 prefill하며, conflicting high-confidence value가 남으면 임의 선택하지 않고 null로 둔다.

## Verification boundary

검증 단계에서는 최소한 다음을 확인한다.

1. source span이 실제 해당 legal instrument를 말하는지
2. principal / commitment / drawn / available을 서로 바꾸지 않았는지
3. amendment/supplement가 original agreement로 잘못 승격되지 않았는지
4. CUSIP/ISIN 등 explicit identifier가 conflict하지 않는지
5. multi-span cluster가 실제 하나의 legal instrument인지
6. table header / unit / rowspan / colspan 해석이 맞는지
7. row가 instrument row인지 subtotal/summary row인지

검증된 snapshot만 stable debt ledger input으로 사용한다.

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

`--source-packet`이 있으면 모든 snapshot의 `source_refs`, accession, published date, cutoff를 검증한다.

## 일부러 자동화하지 않는 것

다음은 deterministic extraction만으로 확정하지 않는다.

- 한 문단의 여러 dollar amount 중 어떤 것이 principal인지 애매한 경우
- transposed table(행이 metric, 열이 instrument)의 자동 pivot 해석
- footnote가 column 의미를 바꾸는 복잡한 table의 법적 해석
- PDF native text/table extraction
- scanned PDF/image OCR
- debt exchange에서 구채권과 신채권의 법적 동일성
- amendment가 modification인지 extinguishment인지에 대한 회계 결론
- guarantor/borrower 범위의 법률적 해석
- covenant add-back의 법적 허용 여부

현재 목표는 **구조를 아는 HTML에서는 row/column association을 보존하고, 구조를 안전하게 읽을 수 없는 visual source는 unresolved로 남기는 것**이다.
