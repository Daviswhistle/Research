# Verified Final Shareholder Payoff Provenance

## 목적

`equity_multiple_3y`는 원칙적으로 분석일로부터 +3년이 지나야 확정할 수 있다.

하지만 일부 사건은 +3년 전에 분석 당시 기존 보통주의 **최종 경제적 payoff를 완전히 고정**한다.

예:

```text
fixed-cash acquisition closes
final liquidation distribution is paid
old common is extinguished with no distribution
```

이 경우 +3년까지 기다리는 것은 정보 사용 시점을 불필요하게 늦춘다.

반대로 사건명만 보고 배수를 자동 계산하면 오류가 생긴다.

따라서 이 계층은 사건과 최종 payoff multiple을 모두 source-backed fact로 명시적으로 요구한다.

## 비추론 원칙

다음은 금지한다.

```text
cash acquisition announced
    -> final payoff known

bankruptcy filed
    -> equity multiple = 0

common delisted
    -> equity multiple = 0

liquidation mentioned
    -> final distribution known
```

최종 payoff가 실제로 고정됐다는 source-backed fact가 있어야 한다.

## CSV fields

Optional fields:

```text
final_shareholder_payoff_event_type
final_shareholder_payoff_event_date
final_shareholder_payoff_known_date
final_shareholder_payoff_multiple
final_shareholder_payoff_evidence_refs
```

하나라도 쓰면 모두 필요하다.

## 허용 event type

```text
fixed_cash_acquisition_closed
final_liquidation_distribution
common_extinguished_no_distribution
```

### fixed_cash_acquisition_closed

거래가 단순 발표된 상태가 아니라 종결되어 분석 당시 common의 최종 고정 현금대가가 확정된 경우다.

### final_liquidation_distribution

추가 분배 가능성이 남지 않은 최종 청산분배가 확정된 경우다.

### common_extinguished_no_distribution

기존 common이 소각되고 최종적으로 아무 분배도 받지 않는 것이 확정된 경우다.

이 event type은 반드시:

```text
final_shareholder_payoff_multiple = 0
```

이어야 한다.

## payoff multiple 자체를 직접 검증

Event type에서 payoff multiple을 추론하지 않는다.

다음 값이 직접 필요하다.

```text
final_shareholder_payoff_multiple
```

그리고:

```text
final_shareholder_payoff_multiple == equity_multiple_3y
```

이어야 한다.

예를 들어 T0 기준 주당 경제적 basis가 5이고 최종 fixed cash consideration이 12.5로 검증됐다면 upstream review가 최종 shareholder multiple을 2.5x로 확정한 뒤:

```text
equity_multiple_3y = 2.5
final_shareholder_payoff_multiple = 2.5
```

를 함께 넣는다.

Repository는 event type만 보고 12.5 / 5를 자동 계산하지 않는다.

## 시간 경계

일반 규칙:

```text
event_date >= analysis_date
known_date >= event_date
event_date <= analysis_date + 3y
```

즉 +3년 이후에 발생한 사건을 +3년 metric의 조기 확정 근거로 소급할 수 없다.

`equity_multiple_3y_known_date`는 payoff fact가 알려진 날짜보다 빠를 수 없다.

```text
final_shareholder_payoff_known_date <= equity_multiple_3y_known_date
```

+3년 전에 equity multiple을 확정하려면 final payoff fact가 필수다.

## Evidence binding

Final payoff fact는 자기 evidence refs를 가진다.

```text
final_shareholder_payoff_evidence_refs
```

이 refs는 반드시:

```text
final_shareholder_payoff_evidence_refs
    ⊆ equity_multiple_3y_evidence_refs
```

이어야 한다.

즉 3년 multiple의 provenance가 최종 payoff를 확정한 문서를 실제로 포함해야 한다.

## Cutoff-safe view

`known_labels(cutoff)`에서는:

- equity multiple이 cutoff까지 알려졌고
- final payoff fact 자체도 cutoff까지 알려졌을 때만

`final_shareholder_payoff_fact`가 남는다.

Cutoff 이전에 payoff가 아직 확정되지 않았다면 해당 3년 multiple도 보이지 않는다.

## 기존 common survival과 분리

Final payoff fact는 `equity_multiple_3y`를 위한 경제적 payoff provenance다.

다음과 자동으로 동일시하지 않는다.

```text
existing_common_survived_12m
company survived_12m
normalized_within_3y
```

예를 들어 fixed cash acquisition은 기존 common이 종결될 수 있지만, 그 법적 생존 metric을 조기 `false`로 쓰려면 별도 `common_terminal_fact` provenance가 필요하다.

## 왜 별도 계층인가

투자 관점에서 중요한 것은 단순히 주식이 사라졌는지가 아니라:

```text
기존 common 소유자가 최종적으로 얼마를 받았는가?
```

다.

이 계층은 조기 payoff가 확정된 사례를 walk-forward calibration에서 너무 늦게 인식하는 오류를 줄이면서도, 사건명 기반 자동 추론을 금지한다.

## 현재 한계

`final_shareholder_payoff_multiple`은 upstream에서 이미 모든 최종 consideration을 반영해 검증된 값이어야 한다.

예를 들어 다음이 복합적으로 존재하면 별도 계산/검증이 필요하다.

```text
cash + stock consideration
contingent value rights
escrow / holdback
multiple liquidation distributions
special dividends
fractional-share cash
```

현재 parser는 이런 구성요소를 자동 합산하지 않는다.

이 계산이 필요해지면 차기 단계에서 shareholder-payoff ledger를 별도 모델로 추가하는 것이 안전하다.
