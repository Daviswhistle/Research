# SEC Debt Instrument Source Extraction

## 목적

`distressed-equity-sec-instruments`는 10-K/10-Q/8-K의 primary document에서 채무 숫자를 추측하는 대신, 같은 filing에 첨부된 **indenture / credit agreement / amendment / waiver / security agreement exhibit 원문**을 찾아 survival/common-equity 연구에 필요한 evidence를 좁힌다.

핵심 순서는 다음과 같다.

```text
SEC filing index
  -> debt-relevant EX-4 / EX-10
  -> source type 확인
       HTML/native PDF structure usable -> deterministic candidate
       visual/scanned source            -> multimodal source reader
  -> source verification
  -> 필요한 최소 deterministic fields
  -> stable debt ledger / covenant engine
```

Deterministic extractor의 결과도, multimodal model의 finding도 최종 debt schedule 자체가 아니다.

## Document selection

우선순위가 높은 문서:

- `EX-4.*`: indenture, note/security-holder rights
- `EX-10.*`: credit agreement, material debt contract
- debt keyword가 description/filename에 명시된 amendment, waiver, security/guarantee document

`EX-31`, `EX-32`, `EX-101` 등은 제외한다.

## Deterministic source paths

HTML table은 row/column association을 보존한다. Native-text PDF는 binary로 fetch하고 `pypdf` text matrix의 page/x/y를 보존한다.

주요 deterministic field candidate:

- instrument name/type
- coupon
- maturity date/year
- principal / commitment / availability
- CUSIP / ISIN
- benchmark / spread
- seniority/security

모든 candidate는 exact `source_refs`를 유지하며 verification 전에는 authoritative debt row가 아니다.

자세한 내용:

- [`STRUCTURED_DEBT_EXTRACTION.md`](STRUCTURED_DEBT_EXTRACTION.md)
- [`NATIVE_PDF_DEBT_EXTRACTION.md`](NATIVE_PDF_DEBT_EXTRACTION.md)
- [`DEBT_EVIDENCE_CLUSTERING.md`](DEBT_EVIDENCE_CLUSTERING.md)

## Visual / scanned source path

Native text가 없는 PDF, image file, image-heavy HTML은 `VISUAL_EXTRACTION_REQUIRED|...`로 남긴다.

이 상태에서 모든 페이지를 먼저 OCR하고 모든 table cell을 JSON으로 바꾸지 않는다. Serialized source packet은 `multimodal_source_reading` manifest를 자동으로 만든다.

```text
strategy = read_source_first_structure_on_demand
```

Multimodal model의 역할:

```text
original visual source 직접 읽기
-> material finding
-> page / visual-region evidence
-> surrounding footnote/definition/exception/cross-reference 확인
-> investment/survival implication
-> 계산에 필요한 최소 engine patch만 구조화
```

Result contract:

```text
findings[]
disconfirming_or_ambiguous_evidence[]
engine_patches[]   # optional, minimal only
unresolved[]
```

전체 `instruments[]`나 full debt-table transcription은 기본 계약이 아니다.

자세한 내용은 [`MULTIMODAL_SOURCE_READER.md`](MULTIMODAL_SOURCE_READER.md)를 참고한다.

## Provenance / ledger boundary

Verified snapshot을 ledger에 넘길 때는 여전히 source provenance가 필요하다.

- snapshot은 source refs를 가져야 한다.
- accession/cutoff가 일치해야 한다.
- ambiguous visual reading은 unresolved로 남긴다.
- model confidence만으로 숫자를 확정하지 않는다.
- engine patch는 material finding에 필요한 최소 필드여야 한다.

## Research workspace

`distressed-equity-research`의 `debt_instrument_source_packet`에는 deterministic source evidence와 함께 visual source가 남아 있을 경우 `multimodal_source_reading` manifest가 포함된다.

따라서 workspace가 하는 일은 source를 전부 한 형태로 정규화하는 것이 아니라:

```text
source discovery
-> best available reading path 선택
-> evidence-backed research finding
-> deterministic arithmetic boundary에서만 structure
```

이다.

## 일부러 자동화하지 않는 것

- 애매한 dollar amount를 임의로 principal로 선택
- debt exchange에서 구채권/신채권 legal identity 자동 확정
- modification vs extinguishment 회계 결론 자동 확정
- guarantor/borrower 범위 법률 해석 자동 확정
- scanned document 전체 OCR/JSON normalization
- model confidence만으로 engine field patch

이 도구의 역할은 **원문 탐색 비용을 줄이면서 provenance와 문맥을 잃지 않는 것**이다.
