# Multimodal Source Reader

## 목적

Scanned PDF나 image exhibit의 목표는 **OCR 데이터셋을 만드는 것**이 아니다.

이 연구 파이프라인의 목표는 투자 판단에 필요한 사실과 의미를 알아내는 것이다. 따라서 native text가 없는 visual source는 먼저 multimodal model이 원본 페이지를 직접 읽고, material finding과 evidence를 만든다.

```text
visual source
  -> multimodal model reads original page/image
  -> material finding
  -> exact page / visual-region evidence
  -> interpretation + investment implication
  -> only if deterministic arithmetic needs it:
       minimal engine-facing fields
```

반대로 다음 경로는 기본 전략이 아니다.

```text
visual source
  -> OCR everything
  -> normalize every table/cell into JSON
  -> hope context survived
```

## 왜 read-first인가

Debt agreement와 capital-stack source에서 의미는 표 cell만으로 결정되지 않는다.

다음 요소가 숫자의 의미를 바꿀 수 있다.

- footnote
- definition
- exception / proviso
- amendment wording
- cross-reference
- borrower / guarantor scope
- drawn amount와 commitment의 차이
- maturity와 springing maturity의 차이
- principal과 exchange consideration의 차이

전면 OCR/구조화는 이런 문맥을 먼저 버린 뒤 숫자를 복구하려는 순서가 될 수 있다.

Multimodal source reader는 반대로 원본을 먼저 읽고 다음 질문을 한다.

```text
이 source가 survival/common-equity thesis를 무엇 때문에 바꾸는가?
그 결론을 실제로 지지하는 page/region은 어디인가?
표의 가장 단순한 읽기가 틀릴 수 있는 이유는 무엇인가?
코드 계산에 정말 필요한 field는 무엇인가?
```

## Structure on demand

구조화는 연구 목표가 아니라 deterministic engine과 연결하기 위한 인터페이스다.

예:

```text
Finding:
2027 Term Loan은 $600m이며 만기 전 refinancing이 필요하다.

Evidence:
page 43 debt table
page 117 maturity clause

Implication:
현재 liquidity runway가 recovery보다 짧아질 수 있다.

Engine patch needed:
debt_obligations += {amount: 600m, maturity/due month: ...}
```

이때 필요한 debt obligation만 구조화한다. 같은 페이지의 다른 30개 cell을 모두 JSON으로 변환할 필요는 없다.

## Workspace task manifest

`VISUAL_EXTRACTION_REQUIRED|...` warning이 남은 document는 `debt_instrument_source_packet.multimodal_source_reading`에 자동 task로 들어간다.

Manifest strategy:

```text
read_source_first_structure_on_demand
```

각 task는 다음을 포함한다.

- source accession / document / URL
- media type / deterministic deferral reason
- 투자·survival 관점 objective
- material research questions
- multimodal guardrails
- finding-first output contract

Result template은 full debt schedule schema를 요구하지 않는다.

```text
findings[]
disconfirming_or_ambiguous_evidence[]
engine_patches[]   # optional / minimal only
unresolved[]
warnings[]
```

`instruments[]`나 전체 table transcription은 기본 contract에 없다.

## Evidence contract

각 material finding은 최소 다음을 가져야 한다.

1. source가 visibly states 하는 사실
2. page number
3. verifier가 다시 찾을 수 있는 visual region / locator
4. interpretation
5. investment implication
6. confidence 또는 unresolved state
7. 반증/애매한 evidence가 있으면 별도 기록

LLM confidence 자체는 source evidence를 대신하지 않는다.

## Native-text PDF와의 관계

Native-text PDF는 deterministic extraction이 가능한 경우 기존 `pypdf` coordinate 경로를 먼저 사용한다.

```text
native text sufficient
  -> deterministic text/layout evidence

native text unavailable / image-heavy
  -> multimodal source reader task
```

즉 multimodal reader는 native parser를 없애는 것이 아니라, deterministic extraction이 불가능하거나 문맥 이해가 필요한 source의 연구 fallback이다.

## 하지 않는 것

다음은 기본 workflow가 아니다.

- Tesseract 등 OCR로 모든 document를 먼저 transcription
- 모든 table row/cell의 일괄 JSON 변환
- visual model output을 검증 없이 authoritative debt schedule로 승격
- ambiguous digit/column을 임의 보정
- model confidence만으로 engine patch 적용

## 향후 runner adapter

현재 repository는 source-reader task/result contract를 frozen packet에 제공한다. 실제 multimodal model invocation은 runner/agent adapter가 담당하게 둔다.

Runner는 page render 또는 원본 visual document를 모델에 제공하고, result contract에 맞춰 finding/evidence를 반환하면 된다. 추후 davis-agent-kit adapter를 붙이더라도 이 source-reading semantics는 provider-independent하게 유지한다.

핵심 원칙은 하나다.

> **원본을 먼저 이해하고, 구조화는 계산이 필요할 때만 한다.**
