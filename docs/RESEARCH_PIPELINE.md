# 공통 Research Candidate Pipeline 초안

## 목적

`transformation_scanner`를 비롯해 앞으로 추가될 서로 다른 탐색기를 하나의 거대한 점수식으로 합치지 않고, 공통 실행 구조 위에 올리기 위한 최소 골격이다.

X의 공개 `x-algorithm`에서 유용했던 아이디어는 추천 모델 자체가 아니라 다음과 같은 단계 분리다.

```text
candidate sources
    ↓
hydration
    ↓
hard filters
    ↓
multi-axis scoring
    ↓
selection / diversity reranking
    ↓
research queue
```

기존 `transformation_scanner`는 독립적으로 계속 동작한다. 이 초안은 이를 `TransformationScannerSource` adapter로 감싸서 공통 파이프라인에 연결할 수 있게 한다.

## 설계 원칙

### 1. source와 판단을 분리한다

`CandidateSource`는 후보를 찾는 역할만 한다. DART 변신기업, distress, 산업 이벤트, 운영 KPI 등 서로 다른 탐색기를 나중에 독립적으로 추가할 수 있다.

### 2. 기회와 위험을 섣불리 한 숫자로 상쇄하지 않는다

`ResearchCandidate.scores`는 하나의 `score` 필드가 아니라 이름 있는 다중 축이다.

예:

```text
attention_score
financing_risk_score
survival_probability
normalization_probability
dilution_probability
information_quality
```

특정 selector가 어떤 축을 연구 우선순위에 사용할지는 별도 결정이다.

### 3. hard filter와 scorer를 구분한다

명백히 조사 대상이 아닌 조건은 filter가 제거한다. 불확실한 현실 판단은 scorer가 남긴다. 점수가 낮다는 이유로 중요한 위험 신호를 숨기지 않는다.

### 4. diversity는 투자판단이 아니라 research queue에 적용한다

초안의 `DiversitySelector`는 dependency-free MMR 방식이다. 상위 후보가 같은 산업·같은 메커니즘으로 과밀해지는 것을 막을 수 있는 인터페이스를 검증하기 위한 구현이다.

**현재 구현은 DPP가 아니다.** 실제 DPP가 필요하면 같은 `Selector` 인터페이스 뒤에 후속 구현한다.

### 5. 실행 결과를 관측 가능하게 만든다

각 stage는 입력 수, 출력 수, 제거 수, latency를 `StageMetric`으로 남긴다. 나중에 좋은 후보를 놓쳤을 때 source / filter / scorer / selector 중 어디가 병목이었는지 추적할 수 있게 한다.

### 6. 기존 scanner를 재작성하지 않는다

`TransformationScannerSource`는 기존 `TransformationPipeline.scan()`의 결과를 공통 `ResearchCandidate`로 변환한다. 기존 CLI와 출력 형식에는 영향을 주지 않는다.

## 현재 범위

포함:

- 공통 candidate/query/result 모델
- source / hydrator / filter / scorer / selector protocol
- 중복 후보 제거
- source fail-open + warning 기록
- stage metrics
- Top-K selector
- MMR형 diversity selector
- 기존 transformation scanner adapter
- 단위 테스트

의도적으로 제외:

- Carvana형 distressed-equity 탐색 로직
- 실제 DPP
- SQLite 공통 상태 저장
- seen/rejected/thesis-changed 상태 머신
- async/parallel hydration
- deep-research agent orchestration
- Telegram/email 알림
- 실험/feature flag

Carvana형 탐색은 별도 작업과 충돌하지 않도록 이슈에서 추적하고, 완성된 로직을 추후 `CandidateSource`로 연결한다.

## 후속 우선순위

1. 공통 persistent state (`seen`, `investigated`, `rejected`, `watch`, `thesis_changed`)
2. candidate별 rejection / selection reason
3. async hydration과 source 병렬화
4. exact DPP 또는 다른 diversity 정책 비교
5. deep-research agent handoff
6. historical replay에서 look-ahead 방지 규약
