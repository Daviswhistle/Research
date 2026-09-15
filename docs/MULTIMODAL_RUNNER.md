# Multimodal Source Runner

`VISUAL_EXTRACTION_REQUIRED` source를 실제 multimodal model로 읽는 실행 단계다.

핵심 경계는 다음과 같다.

```text
frozen SEC source
  -> multimodal read
  -> finding/evidence
  -> engine patch proposal
  -> independent evidence re-open verification
  -> promoted agent result
  -> existing deterministic ingestion
```

모델 출력은 자동으로 screening 숫자가 되지 않는다.

## 1. Runner

Workspace를 먼저 만든다.

```bash
export SEC_USER_AGENT="Research your-email@example.com"
export OPENAI_API_KEY="..."
export OPENAI_MULTIMODAL_MODEL="gpt-5.6-sol"   # optional
```

그 다음 frozen packet의 visual task를 실행한다.

```bash
distressed-equity-multimodal run \
  --workspace output/cvna_2022-12-31
```

기본 출력:

```text
output/cvna_2022-12-31/multimodal_results/
├── multimodal-source_....json
└── multimodal-source_....verification.json
```

기존 결과가 있으면 기본적으로 건너뛴다. 다시 실행할 때만 `--overwrite`를 사용한다.

특정 task만 실행할 수 있다.

```bash
distressed-equity-multimodal run \
  --workspace output/cvna_2022-12-31 \
  --task-id 'multimodal-source:...'
```

현재 실제 provider adapter는 OpenAI Responses API다. Provider 경계는 별도 interface로 유지되어 이후 davis-agent-kit이나 다른 multimodal harness를 붙일 수 있다.

## 2. Source handling

Runner는 frozen task의 source URL을 다시 읽는다.

기본 source host는 다음만 허용한다.

```text
https://www.sec.gov
https://sec.gov
```

redirect도 최종 host를 다시 검사한다. 추가 host가 정말 필요한 경우에만 명시적으로 허용한다.

```bash
--allow-source-host example.com
```

PDF는 원본 bytes를 multimodal file input으로 전달하고 높은 visual detail을 요청한다.

Image source는 image input으로 전달한다.

Image-heavy HTML은:

1. HTML 자체를 context로 전달하고
2. `<img src=...>` visual asset을 별도로 내려받아 image input으로 함께 전달한다.

따라서 HTML table을 전면 OCR하는 경로가 아니라, deterministic HTML extraction이 실패한 경우 원본 문맥과 embedded visual을 함께 읽는 fallback이다.

Source byte limit과 embedded-image limit이 있으며 CLI에서 조정할 수 있다.

## 3. Finding-first result

결과는 full debt table transcription이 아니다.

각 finding은 최소 다음을 가져야 한다.

```text
finding_id
visible_fact
evidence[]
  evidence_id
  page
  region
  visible_text
interpretation
investment_implication
confidence
unresolved
```

PDF evidence는 양의 page number와 재탐색 가능한 region이 필수다.

Engine patch는 다음 허용 경계 안에서만 제안할 수 있다.

```text
capital_structure.current_shares
capital_structure.net_debt
capital_structure.other_senior_claims
starting_liquidity
available_credit
asset_monetization
restricted_cash
debt_obligations
covenants
```

Patch는 반드시 `finding_refs`와 `evidence_refs`를 가져야 한다. Unresolved finding에 의존하는 patch는 validation에서 거부한다.

## 4. Independent verification boundary

Runner가 만든 `*.verification.json`은 처음에는 모든 patch가 다음 상태다.

```json
{
  "status": "unverified",
  "evidence_reopened": false
}
```

검증자는 원본 page/image region을 직접 다시 연 뒤에만:

```json
{
  "status": "verified",
  "evidence_reopened": true
}
```

로 바꾼다.

검증 파일에는 다음도 채운다.

```json
{
  "verifier": "reviewer-or-verifier-id",
  "verified_on": "2026-09-15"
}
```

Verification은 multimodal result의 canonical semantic payload에 대한 SHA-256 fingerprint를 포함한다.

따라서 검증 후 result의 숫자, finding, evidence, patch가 바뀌면 promotion이 거부된다.

Model confidence는 verification을 대신하지 않는다.

## 5. Promotion

검증이 끝난 patch만 기존 agent ingestion contract로 변환한다.

```bash
distressed-equity-multimodal promote \
  --result output/cvna_2022-12-31/multimodal_results/<task>.json \
  --verification output/cvna_2022-12-31/multimodal_results/<task>.verification.json \
  -o output/cvna_2022-12-31/multimodal_results/<task>.agent-result.json
```

`verified + evidence_reopened=true`인 patch만 `patches[]`에 들어간다.

Unverified patch는 계산에 들어가지 않고 `unresolved[]`에 남는다. Rejected patch도 적용되지 않는다.

Promoted evidence ID는 source task hash로 namespace되어, 여러 visual source가 모두 `e1` 같은 local ID를 사용해도 combined evidence ledger에서 충돌하지 않는다.

## 6. Existing ingestion으로 연결

Promotion 결과는 기존 `distressed-equity-research --result`에 그대로 넣는다.

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --result output/cvna_2022-12-31/multimodal_results/<task>.agent-result.json
```

이후에도 기존 규칙이 그대로 적용된다.

- point-in-time evidence validation
- task patch permission
- low-confidence patch default skip
- existing deterministic value와 conflict 시 overwrite 금지
- deterministic screening

즉 multimodal model은 deterministic engine을 우회하지 않는다.

## 7. 실패 artifact

Provider 호출, source fetch, result validation이 실패하면 해당 task의 `.error.json`을 남기고 CLI는 non-zero로 종료한다.

잘못된 result를 정상 result처럼 저장하거나 자동 승격하지 않는다.

## 8. Provider/API semantics

OpenAI adapter는 Responses API를 사용한다.

- `store=false`
- PDF: Base64 `input_file`, high detail
- image: Base64 `input_image`, high detail
- structured JSON response contract
- local validation이 최종 authority

Provider 응답 형식이 맞더라도 source identity, analysis cutoff, page locator, evidence reference, patch value가 local validator를 통과하지 못하면 결과는 거부된다.

## 남은 다음 병목

이 단계 이후 남는 큰 문제는 provider 호출 자체가 아니라 연구 데이터/모델링 쪽이다.

- transposed / complex multi-row / footnote-heavy deterministic tables
- debt exchange / modification / extinguishment lineage와 accounting linkage
- historical bond price/yield provider
- CRSP-scale replay와 historical distress base-rate population
