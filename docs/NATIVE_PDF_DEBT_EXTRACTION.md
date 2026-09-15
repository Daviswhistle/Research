# Native-Text PDF Debt Extraction

## 목적

SEC debt exhibit가 PDF라는 이유만으로 binary bytes를 `response.text`로 decode하지 않는다. Native text layer가 충분한 PDF는 deterministic layout evidence로 처리하고, 그렇지 않은 scanned/image source는 **multimodal source reader**로 넘긴다.

```text
PDF bytes
  -> native text layer usable?
       yes -> pypdf text matrix / deterministic extraction
       no  -> multimodal source reader
```

Scanned PDF를 먼저 전면 OCR하고 모든 표를 JSON으로 만드는 단계는 기본 pipeline이 아니다.

## Native-text path

현재 parser는 `pypdf>=6,<7`을 사용한다.

기본 안전 한계:

```text
max PDF bytes       = 25 MB
min native chars    = 80 non-whitespace chars
min text fragments  = 2 positioned fragments
```

이 기준은 usable native text layer가 있는지 확인하는 gate일 뿐 debt field confidence score가 아니다.

각 text fragment는 다음을 보존한다.

```text
page_number
x
y
font_size
text
```

같은 page에서 y가 가까운 fragment를 visual row로, row 내부는 x 오름차순으로 재구성한다.

## Native PDF table extraction

한 row에서 semantic header가 충분히 확인되면 header x 좌표 사이 midpoint를 column boundary로 사용한다.

지원 header semantics:

- instrument / security / debt / facility / loan
- principal / outstanding / commitment / drawn / available
- maturity
- coupon / rate / benchmark / spread
- CUSIP / ISIN
- seniority / secured / currency

Source ref:

```text
<accession>:<sequence>:p<page>:r<row>
```

Candidate provenance:

```text
cluster_status = pdf_layout_row
cluster_basis = [
  same_native_pdf_layout_row,
  coordinate_mapped_columns,
  pypdf_text_matrix
]
```

Table로 판단된 page는 flattened prose로 다시 읽지 않는다.

## Native PDF prose

Table header가 검출되지 않은 native-text page는 y/x coordinate order로 text를 재구성한 뒤 기존 debt block/clustering logic을 적용한다.

```text
<accession>:<sequence>:p<page>:s<span>
cluster_status = pdf_native_prose | pdf_native_prose_linked
```

## Graph / closure integration

PDF semantics는 initial SEC packet에만 적용되지 않는다. Package bootstrap이 downstream graph module import 전에 document-aware helper를 설치하므로 다음 경로가 동일하다.

```text
initial SEC packet
source-document graph
locator-less contract graph
foreign contract closure
named-entity contract resolution
```

Native PDF graph source는 coordinate-ordered string-compatible payload를 제공하면서 원본 bytes도 보존한다. Debt extraction은 그 bytes를 다시 사용해 layout candidate를 만든다.

## Scanned / image-only PDF

Native text layer가 없거나 너무 sparse하면 자동 debt candidate를 만들지 않는다.

```text
VISUAL_EXTRACTION_REQUIRED|...|media_type=pdf|...
PDF_NATIVE_TEXT_UNAVAILABLE|...
```

Parser failure:

```text
PDF_NATIVE_TEXT_EXTRACTION_FAILED|...
```

Native success:

```text
PDF_NATIVE_TEXT_EXTRACTED|...
```

여기서 `VISUAL_EXTRACTION_REQUIRED`의 다음 단계는 **OCR table extractor가 아니라 multimodal source reader**다.

Multimodal model은 원본 page/image를 직접 읽고 다음 순서로 작업한다.

```text
source understanding
-> material finding
-> exact page / visual-region evidence
-> interpretation / investment implication
-> deterministic calculation에 필요한 최소 field만 structure-on-demand
```

즉 OCR은 model이 문자를 읽는 내부 수단일 수 있지만 repository가 요구하는 연구 산출물은 OCR transcript가 아니다.

자세한 계약은 [`MULTIMODAL_SOURCE_READER.md`](MULTIMODAL_SOURCE_READER.md)를 참고한다.

## Verification boundary

Native PDF candidate도 authoritative debt row가 아니다. 최소 다음을 확인해야 한다.

1. text-matrix 좌표가 실제 visual layout과 일치하는지
2. header anchor가 실제 column header인지
3. wrapped cell이 다른 row로 잘못 분리되지 않았는지
4. amount unit이 header/footnote와 일치하는지
5. source ref page/row가 실제 instrument를 지지하는지

Multimodal source-reader finding도 마찬가지로 source evidence를 다시 열 수 있어야 하며 model confidence만으로 deterministic engine patch를 확정하지 않는다.

## Known limitations

- rotated/skewed text 또는 복잡한 CTM 사용 PDF
- multi-row/spanning PDF headers
- transposed debt tables
- footnote가 amount/unit 의미를 바꾸는 표
- glyph 단위로 지나치게 잘게 쪼개진 native text
- multimodal runner/provider adapter는 별도 integration 단계

핵심은 **native structure가 있으면 deterministic하게 활용하고, 없으면 원본을 multimodal model이 직접 이해하게 하며, 불필요한 전면 구조화는 하지 않는 것**이다.
