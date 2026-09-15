# Native-Text PDF Debt Extraction

## 목적

SEC debt exhibit가 PDF라는 이유만으로 binary bytes를 `response.text`로 decode하거나 OCR을 강제하지 않는다.

현재 PDF 경로는 먼저 **native text layer가 있는지** 확인하고, usable text objects가 있으면 PDF text matrix의 좌표를 보존해 debt evidence를 추출한다.

```text
PDF bytes
  -> pypdf text objects
  -> page + x/y text-matrix coordinates
  -> y-axis row reconstruction
  -> x-axis header anchors / column boundaries
  -> same-layout-row debt candidates
  -> page-local prose candidates
```

Native text가 없거나 너무 희박하면 OCR로 자동 승격하지 않고 기존 visual-extraction 경계에 남긴다.

## Dependency / limits

현재 parser는 pure-Python `pypdf>=6,<7`을 사용한다.

기본 안전 한계:

```text
max PDF bytes       = 25 MB
min native chars    = 80 non-whitespace chars
min text fragments  = 2 positioned fragments
```

이 기준은 문서가 native text layer를 갖는지 확인하는 gate일 뿐, debt field confidence를 올리는 점수가 아니다. Debt candidate는 이후 기존 deterministic instrument/term 규칙을 다시 통과해야 한다.

## Coordinate provenance

각 native text fragment는 다음을 보존한다.

```text
page_number
x
y
font_size
text
```

`pypdf.PageObject.extract_text(visitor_text=...)`가 전달하는 current/transformation matrix와 text matrix를 조합해 좌표를 얻는다.

정확히 같은 위치에 동일 text-show operation이 중복되는 PDF layer는 `(x, y, text)` 기준으로 dedupe한다.

## Row reconstruction

같은 page에서 y 좌표가 충분히 가까운 text fragment를 하나의 visual row로 묶고, row 내부는 x 좌표 오름차순으로 정렬한다.

```text
page 1
  y=750  Instrument | Principal | Maturity | Coupon | CUSIP
  y=725  5.25% ...  | 500       | 2028...  | 5.25%  | 123...
```

Row tolerance는 font size를 참고하되 bounded하게 적용한다. 이는 layout reconstruction aid이며, OCR처럼 문자 자체를 추정하지 않는다.

## Native PDF table extraction

한 row에서 semantic header가 두 개 이상 확인되고 instrument/identifier axis가 존재하면 table header 후보로 취급한다.

지원 header semantics는 HTML structured extractor와 동일하다.

- instrument / security / debt / facility / loan
- principal / outstanding / commitment / drawn / available
- maturity
- coupon / rate / benchmark / spread
- CUSIP / ISIN
- seniority / secured / currency

Header의 x 좌표 사이 midpoint를 column boundary로 사용한다. Data row의 각 text fragment는 자신의 x 좌표에 따라 하나의 column에만 들어간다.

따라서:

```text
row 1 principal -> row 2 CUSIP
```

같은 cross-row association은 만들지 않는다.

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

## Native PDF prose extraction

Table header가 검출되지 않은 page는 y/x row order로 text를 다시 구성한 뒤 기존 debt block/proposal logic을 적용한다.

Source ref:

```text
<accession>:<sequence>:p<page>:s<span>
```

Candidate provenance:

```text
cluster_status = pdf_native_prose | pdf_native_prose_linked
cluster_basis += [same_pdf_page, native_text_matrix_order]
```

Table로 판단된 page는 prose extraction에서 제외한다. 같은 PDF table을 coordinate row와 flattened prose로 이중 해석하지 않기 위해서다.

## Graph / closure integration

PDF 처리는 initial `build_sec_instrument_packet()`에만 국한되지 않는다.

Package bootstrap은 downstream graph modules가 import되기 전에 document-aware `_get_text` / `extract_source_candidates` bridge를 설치한다.

따라서 다음 경로가 같은 PDF semantics를 사용한다.

```text
initial SEC packet
source-document graph
locator-less contract graph
foreign contract closure
named-entity contract resolution
```

Native PDF를 graph source로 읽을 때 `_get_text`는 PDF binary를 text로 decode하지 않는다. Binary에서 native text objects를 읽어 coordinate-ordered `NativePdfTextPayload(str)`를 만들고 원본 PDF bytes도 payload에 보존한다.

그 결과 source-reference parser는 string interface를 그대로 쓸 수 있고, debt extractor는 같은 payload의 원본 bytes를 사용해 coordinate candidate를 재구축한다.

## Scanned / image-only PDF safety

Native text layer가 없거나 gate를 통과하지 못하면 자동 candidate를 만들지 않는다.

상태 예:

```text
VISUAL_EXTRACTION_REQUIRED|...|media_type=pdf|...
PDF_NATIVE_TEXT_UNAVAILABLE|...
```

Parser 실패/invalid PDF:

```text
PDF_NATIVE_TEXT_EXTRACTION_FAILED|...
```

Native extraction 성공:

```text
PDF_NATIVE_TEXT_EXTRACTED|
page_count=...|
fragment_count=...|
character_count=...|
candidate_count=...
```

Scanned PDF는 다음 OCR stage의 입력 후보이지, native parser가 억지로 처리할 대상이 아니다.

## 검증 경계

Native PDF candidate도 authoritative debt row가 아니다.

Verification 단계에서 최소 다음을 확인한다.

1. PDF text layer의 좌표가 실제 visual layout과 일치하는지
2. header anchor가 실제 column header인지
3. multi-line / wrapped cell이 다른 row로 잘못 분리되지 않았는지
4. amount unit이 header/footnote와 일치하는지
5. source ref page/row가 실제 instrument를 지지하는지

## Known limitations

- rotated/skewed text 또는 복잡한 CTM 사용 PDF는 좌표가 예상과 다를 수 있다.
- PDF multi-row/spanning header reconstruction은 HTML rowspan/colspan만큼 강하지 않다.
- transposed debt table은 자동 해석하지 않는다.
- footnote가 amount/unit 의미를 바꾸는 표는 자동 확정하지 않는다.
- native text가 glyph 단위로 지나치게 잘게 쪼개진 PDF는 header recognition recall이 낮을 수 있다.
- scanned/image-only PDF OCR은 별도 단계다.

현재 목표는 PDF를 무조건 읽는 것이 아니라, **PDF 자체가 제공하는 native layout evidence가 충분할 때만 자동 extraction을 허용하는 것**이다.
