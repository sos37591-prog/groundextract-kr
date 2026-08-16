window.GEK_DOCS = [
  {
    "id": "tax_invoice",
    "title": "전자세금계산서",
    "image": "assets/tax_invoice_demo.png",
    "width": 1699,
    "height": 1189,
    "clean": {
      "summary": {
        "total": 3,
        "verified": 3,
        "discarded": 0,
        "ungrounded": 0,
        "rule_pack": "tax_invoice",
        "rules_applied": 2
      },
      "fields": [
        {
          "field": "supply",
          "label": "공급가액",
          "value": "150,000원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            452,
            666,
            543,
            706
          ],
          "failed": []
        },
        {
          "field": "vat",
          "label": "세액(부가세)",
          "value": "15,000원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            706,
            668,
            785,
            705
          ],
          "failed": []
        },
        {
          "field": "total",
          "label": "합계금액",
          "value": "165,000원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            290,
            1057,
            381,
            1098
          ],
          "failed": []
        }
      ]
    },
    "injected": {
      "summary": {
        "total": 3,
        "verified": 0,
        "discarded": 3,
        "ungrounded": 1,
        "rule_pack": "tax_invoice",
        "rules_applied": 2
      },
      "fields": [
        {
          "field": "supply",
          "label": "공급가액",
          "value": "150,000원",
          "verdict": "discarded",
          "confidence": 0.0,
          "box": [
            452,
            666,
            543,
            706
          ],
          "failed": [
            {
              "name": "vat_equals_supply_x_10pct",
              "detail": "50,000 vs 15,000 (tol 1) -> diff 35,000"
            },
            {
              "name": "total_equals_supply_plus_vat",
              "detail": "165,000 vs 200,000 (tol 1) -> diff 35,000"
            }
          ]
        },
        {
          "field": "vat",
          "label": "세액(부가세)",
          "value": "50,000원",
          "verdict": "discarded",
          "confidence": 0.0,
          "box": [
            706,
            668,
            785,
            705
          ],
          "failed": [
            {
              "name": "grounding",
              "detail": "number 50000 not found in source (fuzzy matching is not applied to numeric values)"
            },
            {
              "name": "vat_equals_supply_x_10pct",
              "detail": "50,000 vs 15,000 (tol 1) -> diff 35,000"
            },
            {
              "name": "total_equals_supply_plus_vat",
              "detail": "165,000 vs 200,000 (tol 1) -> diff 35,000"
            }
          ]
        },
        {
          "field": "total",
          "label": "합계금액",
          "value": "165,000원",
          "verdict": "discarded",
          "confidence": 0.0,
          "box": [
            290,
            1057,
            381,
            1098
          ],
          "failed": [
            {
              "name": "total_equals_supply_plus_vat",
              "detail": "165,000 vs 200,000 (tol 1) -> diff 35,000"
            }
          ]
        }
      ]
    },
    "story": "환각 세액(50,000원)은 문서에 없음 → 근거 실패 + 산술 위반으로 연쇄 폐기",
    "source": "비식별 캡처 (공급자·공급받는자·승인번호 마스킹 · 품목 일반화)"
  },
  {
    "id": "balance_sheet",
    "title": "표준재무상태표",
    "image": "assets/balance_sheet_demo.png",
    "width": 1191,
    "height": 1684,
    "clean": {
      "summary": {
        "total": 6,
        "verified": 6,
        "discarded": 0,
        "ungrounded": 0,
        "rule_pack": "balance_sheet",
        "rules_applied": 3
      },
      "fields": [
        {
          "field": "current_assets",
          "label": "유동자산",
          "value": "398,180,000원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            484.2,
            422.4,
            581.9,
            438.1
          ],
          "failed": []
        },
        {
          "field": "noncurrent_assets",
          "label": "비유동자산",
          "value": "39,431,995원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            493.2,
            822.1,
            581.9,
            837.8
          ],
          "failed": []
        },
        {
          "field": "total_assets",
          "label": "자산총계",
          "value": "437,611,995원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            484.0,
            1061.9,
            581.9,
            1077.6
          ],
          "failed": []
        },
        {
          "field": "total_liabilities",
          "label": "부채총계",
          "value": "109,500,000원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            989.9,
            422.4,
            1086.4,
            438.1
          ],
          "failed": []
        },
        {
          "field": "total_equity",
          "label": "자본총계",
          "value": "328,111,995원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            988.7,
            635.5,
            1086.5,
            651.3
          ],
          "failed": []
        },
        {
          "field": "total_liab_equity",
          "label": "부채와자본총계",
          "value": "437,611,995원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            988.6,
            662.2,
            1086.5,
            678.0
          ],
          "failed": []
        }
      ]
    },
    "injected": {
      "summary": {
        "total": 6,
        "verified": 3,
        "discarded": 3,
        "ungrounded": 0,
        "rule_pack": "balance_sheet",
        "rules_applied": 3
      },
      "fields": [
        {
          "field": "current_assets",
          "label": "유동자산",
          "value": "398,180,000원",
          "verdict": "discarded",
          "confidence": 0.0,
          "box": [
            484.2,
            422.4,
            581.9,
            438.1
          ],
          "failed": [
            {
              "name": "assets_eq_current_plus_noncurrent",
              "detail": "total_assets=437,611,995 vs sum(current_assets+noncurrent_assets)=437,611,895 -> diff 100"
            }
          ]
        },
        {
          "field": "noncurrent_assets",
          "label": "비유동자산",
          "value": "39,431,895원",
          "verdict": "discarded",
          "confidence": 0.0,
          "box": [
            493.2,
            822.1,
            581.9,
            837.8
          ],
          "failed": [
            {
              "name": "assets_eq_current_plus_noncurrent",
              "detail": "total_assets=437,611,995 vs sum(current_assets+noncurrent_assets)=437,611,895 -> diff 100"
            }
          ]
        },
        {
          "field": "total_assets",
          "label": "자산총계",
          "value": "437,611,995원",
          "verdict": "discarded",
          "confidence": 0.0,
          "box": [
            484.0,
            1061.9,
            581.9,
            1077.6
          ],
          "failed": [
            {
              "name": "assets_eq_current_plus_noncurrent",
              "detail": "total_assets=437,611,995 vs sum(current_assets+noncurrent_assets)=437,611,895 -> diff 100"
            }
          ]
        },
        {
          "field": "total_liabilities",
          "label": "부채총계",
          "value": "109,500,000원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            989.9,
            422.4,
            1086.4,
            438.1
          ],
          "failed": []
        },
        {
          "field": "total_equity",
          "label": "자본총계",
          "value": "328,111,995원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            988.7,
            635.5,
            1086.5,
            651.3
          ],
          "failed": []
        },
        {
          "field": "total_liab_equity",
          "label": "부채와자본총계",
          "value": "437,611,995원",
          "verdict": "verified",
          "confidence": 1.0,
          "box": [
            988.6,
            662.2,
            1086.5,
            678.0
          ],
          "failed": []
        }
      ]
    },
    "story": "비유동자산 한 자리 오독은 OCR 텍스트에도 존재 → 근거 통과, 산술만 적발",
    "source": "합성 샘플 (표준 서식 · 모든 금액 가상 · 회계 항등식 성립)"
  }
];
