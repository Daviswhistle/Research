# Structured Debt Exhibit Extraction

## 목적

Debt exhibit의 구조가 명확할 때는 그 구조를 보존해 deterministic extraction을 한다. 구조가 불확실한 visual source는 모든 내용을 먼저 OCR/JSON화하지 않고 multimodal model이 원본을 직접 읽는다.

```text
HTML table      -> DOM row / column 구조 보존
native-text PDF -> text-matrix x/y 구조 보존
scanned/image   -> multimodal source reader
```

구조화 자체가 목적이 아니라 **투자 판단에 필요한 사실을 정확하게 알아내고, 계산에 필요한 최소 필드만 마지막에 코드와 연결하는 것**이 목적이다.

## HTML table path

`structured_debt_extraction.py`는 table body를 prose path와 분리한다.

```text
SEC exhibit HTML
-> top-level table parser
-> rowspan / colspan expansion
-> semantic header mapping
-> one data row = one source span
-> same-row deterministic proposals
-> remaining prose만 distant-span extractor로 전달
```

Source ref:

```text
<accession>:<sequence>:t<table_index>:r<row_index>
```

Candidate provenance:

```text
cluster_status = table_row
cluster_basis = [same_html_table_row, header_mapped_columns]
```

다른 row의 principal/CUSIP/maturity를 자동 결합하지 않는다.

## Native-text PDF path

PDF는 binary bytes로 읽고 `pypdf` text matrix의 page/x/y를 보존한다.

```text
PDF bytes
-> positioned text fragments
-> visual rows
-> x-axis header anchors
-> same-layout-row candidates
```

Source ref:

```text
<accession>:<sequence>:p<page>:r<row>
```

Candidate provenance:

```text
cluster_status = pdf_layout_row
cluster_basis = [same_native_pdf_layout_row, coordinate_mapped_columns, pypdf_text_matrix]
```

Table이 아닌 native PDF prose는 page-local coordinate order로 처리한다.

자세한 규칙은 [`NATIVE_PDF_DEBT_EXTRACTION.md`](NATIVE_PDF_DEBT_EXTRACTION.md)를 참고한다.

## Supported deterministic semantics

HTML/PDF structured paths는 다음 field 계열을 지원한다.

- instrument / debt / security / facility / loan
- principal / face amount / outstanding
- commitment / drawn / available
- maturity / due date
- coupon / interest rate
- benchmark / spread
- CUSIP / ISIN
- seniority / secured / currency

Header unit이 명확하면 millions/billions/thousands를 amount에 적용한다.

## Visual-source boundary

Native text가 없는 PDF, image file, image-heavy HTML은 deterministic extractor가 억지로 처리하지 않는다.

```text
VISUAL_EXTRACTION_REQUIRED|...
```

이 warning은 이제 “다음 OCR pipeline에 넣으라”는 뜻이 아니다. Frozen source packet serializer가 해당 document에 대해 `multimodal_source_reading` task를 자동 생성한다.

Task strategy:

```text
read_source_first_structure_on_demand
```

Multimodal model은 원본 page/image를 직접 읽고:

1. material finding을 만든다.
2. page/visual-region evidence를 남긴다.
3. surrounding text/footnote/definition/cross-reference를 같이 고려한다.
4. investment/survival implication을 설명한다.
5. deterministic engine 계산에 정말 필요한 field만 선택적으로 구조화한다.

전체 table의 모든 row/cell을 JSON으로 전환하는 것은 기본 계약이 아니다.

자세한 내용은 [`MULTIMODAL_SOURCE_READER.md`](MULTIMODAL_SOURCE_READER.md)를 참고한다.

## 왜 두 경로를 나누는가

Deterministic structure가 실제 source에 존재하면 코드가 잘한다.

- HTML DOM table
- native PDF text matrix
- explicit labels / identifiers

반대로 visual source의 의미가 다음 요소에 의존하면 LLM이 원본 문맥을 먼저 보는 편이 낫다.

- footnote
- proviso / exception
- amendment wording
- cross-reference
- borrower/guarantor scope
- 표 밖 정의
- 복잡한 visual grouping

따라서 원칙은:

```text
source-provided structure -> deterministic extraction
source-understanding needed -> multimodal reading
engine arithmetic needed   -> minimal structure on demand
```

## Verification boundary

어느 경로든 authoritative debt row로 바로 승격하지 않는다.

- deterministic candidate는 source ref를 다시 확인한다.
- multimodal finding은 page/region evidence를 다시 확인한다.
- ambiguous number/column은 unresolved로 남긴다.
- model confidence만으로 patch를 확정하지 않는다.
- engine patch는 source-backed material finding에 필요한 최소 범위여야 한다.

## 현재 제한

- transposed / 복잡한 spanning table
- footnote-heavy visual table
- rotated/skewed PDF layout
- multimodal model invocation runner는 아직 provider adapter가 필요함
- separate exhibits 사이의 visual evidence 자동 merge는 하지 않음

핵심 원칙은 **원본을 먼저 이해하고, 구조화는 계산이 필요할 때만 한다**는 것이다.
