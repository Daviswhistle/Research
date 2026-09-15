# Covenant Add-back Sensitivity

`distressed-equity-covenant`은 covenant EBITDA 정의에 disputed add-back이 있으면 단일 ratio만 출력하지 않고 inclusion/exclusion sensitivity를 같이 계산한다.

## 왜 필요한가

Distress 상황에서는 EBITDA 정의가 실제 생존 경로를 크게 바꿀 수 있다.

```text
Reported EBITDA
+ restructuring add-back
+ run-rate synergies
+ cost-savings add-back
- required deductions
= Covenant EBITDA
```

계약상 add-back 허용 여부가 불확실한데 이를 전부 확정값처럼 넣으면 covenant headroom이 과대평가될 수 있다.

이 모듈은 법률 해석을 자동화하지 않는다. Agent/analyst가 `disputed_add_backs`를 명시하면 산술만 다시 계산한다.

## 입력

```json
{
  "ebitda_bridge": {
    "base_ebitda": 100,
    "add_backs": {
      "restructuring": 30,
      "synergies": 20
    },
    "deductions": {},
    "disputed_add_backs": [
      "restructuring",
      "synergies"
    ]
  }
}
```

## 계산 케이스

기본적으로 다음을 계산한다.

1. `claimed_all_disputed_included`
2. `conservative_all_disputed_excluded`
3. disputed item별 개별 제외

예를 들어 net debt가 450, max net leverage가 4.0x라면:

```text
claimed EBITDA      = 150 -> leverage 3.0x -> compliant
conservative EBITDA = 100 -> leverage 4.5x -> breach
```

이 경우 classification은:

```text
disputed_addbacks_flip_outcome
```

이다.

## 출력

각 covenant model에는 `addback_sensitivity`가 추가된다.

주요 필드:

- `covenant_ebitda_range`
- `actual_range`
- `headroom_range`
- `breach_in_any_determinate_case`
- `breach_in_all_cases`
- `classification`

classification 예:

- `robust_compliance`
- `robust_breach`
- `disputed_addbacks_flip_outcome`
- `sensitivity_unresolved_or_partial`
- `no_modeled_disputed_addbacks`

## 해석 원칙

이 범위를 probability로 바꾸지 않는다.

`disputed_addbacks_flip_outcome`은 “breach 확률이 50%”라는 뜻이 아니다. 의미는 다음뿐이다.

> covenant compliance 결론이 disputed accounting/legal interpretation에 의존한다.

이 경우 survival gate의 중요한 research blocker로 취급해야 한다.

## 법률적 경계

코드는 다음을 결정하지 않는다.

- restructuring charge가 계약상 add-back 가능한지
- run-rate synergy 기간 제한을 만족하는지
- cap이 있는 add-back인지
- pro forma acquisition adjustment가 허용되는지
- lender consent나 amendment가 이미 효력을 발생했는지

이 판단은 credit agreement, amendment, compliance certificate 등 point-in-time source를 확인해야 한다.
