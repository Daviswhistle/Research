# SEC Capital-Stack Extraction

## 목적

표준 XBRL의 `debt_current / debt_noncurrent`만으로는 distressed-equity survival 분석에 필요한 자본구조를 재구성할 수 없다.

실제로 필요한 것은 다음과 같은 note-level 정보다.

- 개별 debt tranche와 만기
- revolver commitment / drawn / availability
- 담보와 선순위
- covenant와 minimum liquidity
- springing maturity
- cure mechanics
- preferred / lease / earn-out / TRA / contingent claim
- refinancing이 필요한 시점

따라서 SEC primary filing HTML에서 관련 문맥을 먼저 좁힌 뒤, 그 문맥을 structured agent audit의 입력으로 사용한다.

## 두 단계 구조

```text
SEC primary filing
  ↓
deterministic HTML → text
  ↓
keyword/context snippet extraction
  ↓
amount / year / percentage candidates
  ↓
agent verifies instrument context in source filing
  ↓
structured agent result
  ↓
validated candidate patch
```

첫 번째 extractor는 **navigation layer**다. 자동 accounting parser가 아니다.

## 왜 자동 debt schedule로 바로 만들지 않는가

예를 들어 다음 문장이 있다고 하자.

```text
The $500 million revolver matures in 2027, subject to a springing maturity
if the 2026 notes remain outstanding.
```

여기서 `$500m`, `2027`, `2026`을 정규식으로 찾는 것은 쉽지만:

- 어느 숫자가 어느 instrument의 principal인지
- springing condition이 실제로 언제 trigger되는지
- revolver가 fully available한지
- maturity 전에 refinancing이 사실상 필요한지

는 표·각주·앞뒤 문맥을 봐야 한다.

따라서 extractor는 `amount_candidates`, `year_candidates`, `percentage_candidates`만 기록하고 debt obligation으로 자동 승격하지 않는다.

## 현재 category

- `springing_maturity`
- `covenant`
- `revolver`
- `maturity`
- `collateral_seniority`
- `interest`

HTML의 `script/style/noscript`는 제거한다. SEC 표가 한 줄씩 분리되는 경우를 고려해 인접 3개 line을 sliding window로 묶어 검색한다.

## 실행

```bash
export SEC_USER_AGENT="Research your-email@example.com"

distressed-equity-sec-debt \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --filing-limit 3 \
  --max-snippets-per-filing 50 \
  -o output/cvna_capital_stack_packet.json \
  --task-output output/cvna_capital_stack_task.json
```

`task-output`에는:

- `capital_stack_extractor` task
- 동일 cutoff의 structured agent-result template

이 같이 들어간다.

## Point-in-time guard

분석일 이후 filing은 packet에 포함하지 않는다.

```text
filing.filing_date <= analysis_date
```

agent task에도 동일한 cutoff가 guardrail로 전달된다. 회사가 나중에 살아남았다는 결과를 이용해 당시 refinancing 성공을 소급 추정하면 안 된다.

## Patch로 들어갈 수 있는 값

capital-stack agent가 검증 후 제안할 수 있는 주요 필드는 다음이다.

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

각 patch는 source-backed evidence ID를 가져야 하며, 기존 non-null 값과 충돌하면 자동 overwrite하지 않는다.

## 현재 한계

1. 표 셀의 완전한 행/열 의미를 deterministic하게 재구성하지 않는다.
2. 회사별 custom HTML/XBRL extension은 여전히 agent/source review가 필요하다.
3. debt amendment가 여러 filing에 걸쳐 있을 때 최신 유효조건을 자동 diff하지 않는다.
4. covenant headroom 계산에는 EBITDA 정의와 excluded add-back을 별도로 재구성해야 한다.
5. revolver availability는 commitment 금액과 다르므로 `commitment = available_credit`로 간주하지 않는다.

## 다음 확장

- amendment 전후 debt term diff
- maturity table parser with source cell coordinates
- covenant EBITDA definition parser
- collateral / guarantor graph
- revolver borrowing-base and availability bridge
- agent result에서 debt schedule consistency checker
