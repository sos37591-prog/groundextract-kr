# GroundExtract-KR

**LLM이 지어낸 숫자는 당신의 DB에 도달하지 못한다.**

[![CI](https://github.com/sos37591-prog/groundextract-kr/actions/workflows/ci.yml/badge.svg)](https://github.com/sos37591-prog/groundextract-kr/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Runtime deps: 1](https://img.shields.io/badge/runtime%20deps-1%20%28PyYAML%29-brightgreen.svg)](pyproject.toml)

> **[▶ 라이브 데모](https://sos37591-prog.github.io/groundextract-kr/?doc=balance_sheet)** —
> *환각 주입* 토글을 누르면, 근거검증은 통과하는 한 자리 오독을 산술 정합성만으로 잡아내는
> 장면을 볼 수 있습니다. 설치 없이 브라우저에서 바로 동작합니다.
>
> 🇬🇧 English documentation: **[README.md](README.md)**

한국 규제문서(세금계산서·재무제표·시산표)에서 AI가 추출한 **숫자마다**

1. **원문 verbatim 근거** — 모델이 인용한 그 자리에 그 숫자가 실제로 있는가
2. **도메인 산술 정합성** — 부가세=공급가×10%, 합계=공급가+세액, 자산=부채+자본 …
3. **자동 폐기** — 근거가 없거나 규칙을 위반하면 `verdict=discarded, confidence=0.0`

을 강제하는 **값-수준 신뢰성 게이트**입니다. OCR도 LLM 추출도 아닌 그 **다음 단계**로서,
기존 OCR/AI 출력을 재사용해 검증만 합니다.

판정은 또 다른 모델 호출이 아니라 **결정적 코드**가 내립니다. API 키·네트워크·난수가
없으며, 같은 입력은 언제나 같은 판정을 냅니다.

> 2026 공개SW 개발자대회 자유과제(인공지능) 출품작 · Apache-2.0

---

## 30초 시작

```bash
git clone https://github.com/sos37591-prog/groundextract-kr.git
cd groundextract-kr
python -m pip install -e .     # Python 3.11+, 런타임 의존성은 PyYAML 하나뿐
python -m groundextract        # 무키(no-API-key) 데모, 네트워크 불필요
```

컨테이너를 선호한다면 `docker build -t groundextract-kr . && docker run --rm groundextract-kr`
로 동일한 데모가 돕니다 — [Docker로 실행](#docker로-실행) 참조.

데모는 세액을 환각(`250,000원` — 문서 어디에도 없는 숫자)한 추출 결과를 게이트에 넣습니다:

```text
=== GroundExtract-KR demo ===
XX supply value=1,000,000원   verdict=discarded conf=0.0
       ! vat_equals_supply_x_10pct: 250,000 vs 100,000 (tol 1) -> diff 150,000
       ! total_equals_supply_plus_vat: 1,100,000 vs 1,250,000 (tol 1) -> diff 150,000
XX vat    value=250,000원     verdict=discarded conf=0.0
       ! grounding: cited quote not found in document: '세액  250,000원'
       ! vat_equals_supply_x_10pct: 250,000 vs 100,000 (tol 1) -> diff 150,000
       ! total_equals_supply_plus_vat: 1,100,000 vs 1,250,000 (tol 1) -> diff 150,000
XX total  value=1,100,000원   verdict=discarded conf=0.0
       ! total_equals_supply_plus_vat: 1,100,000 vs 1,250,000 (tol 1) -> diff 150,000
summary: {"total": 3, "verified": 0, "discarded": 3, "ungrounded": 1}
```

환각된 `vat`뿐 아니라 세 필드가 모두 폐기됩니다. 위반된 산술 규칙이 `supply`·`total`을
참조하므로 게이트가 **형제 필드까지 보수적으로 함께 폐기**하기 때문입니다. 이 트레이드오프는
[지표](#numhall-kr-벤치마크)와 [한계](#한계)에서 그대로 공개합니다.

---

## 왜 두 개의 검증인가

근거검증과 산술검증은 **서로소인 오류군**을 잡습니다. 어느 한쪽만으로는 부족합니다.

| 추출기가 저지르는 오류 | 근거검증이 잡나? | 산술검증이 잡나? |
| --- | :---: | :---: |
| 문서에 없는 숫자를 지어냄 | ✅ | 때때로 |
| 문서의 **다른 줄** 숫자를 가져옴 | ❌ | ✅ |
| 한 자리를 오독했는데 그 값이 문서 다른 곳에 존재 | ❌ | ✅ |
| 숫자는 맞게 읽었으나 문서 자체가 내부 모순 | ❌ | ✅ |

### 시그니처 사례 — 오직 산술만 잡는다

벤치마크 스위트에 실제로 들어 있는 문서(`bench/golden/gen_tax_001.json` — 합성·고정 시드)
입니다. 추출기가 `vat` 칸에 `5,230,000원`을 넣었는데, 이 숫자는 문서에 **실제로 인쇄되어
있고**(품목 행), 모델은 그 구간을 정확히 인용했습니다:

```text
품목: 전산장비 납품    공급가액 5,230,000원   세액 523,000원    ← 인용된 구간, 원문 그대로
품목: 시스템 유지보수  공급가액 31,048,000원  세액 3,104,800원

공급가액  36,278,000원
세액  3,627,800원          ← 원래 가져왔어야 할 값
합계금액  39,905,800원
```

근거검증은 **통과**합니다(인용문이 한 글자도 다르지 않게 문서에 존재). 오직
`vat = supply × 10%`만이 이를 잡아냅니다:

```text
! vat_equals_supply_x_10pct: 5,230,000 vs 3,627,800 (tol 1) -> diff 1,602,200
```

위치 근거(bbox/span 인용)에서 멈추는 도구라면 이 숫자를 초록 체크와 하이라이트 박스까지
붙여 그대로 통과시킵니다.

반대 방향도 성립합니다. 어떤 불변식도 걸려 있지 않은 필드(또는 다른 피연산자가 추출되지
않아 규칙이 평가 자체를 건너뛴 경우)에 지어낸 숫자가 들어오면 산술은 아무것도 보지
못하고, 오직 근거검증만이 그것을 죽입니다. 게이트가 **둘 다** 요구하는 이유입니다.

```text
추출값(LLM) ──▶ 근거검증(verbatim) ─┐
                                    ├─▶ 게이트 ─▶ VerifiedField[]
룰팩(YAML) ───▶ 산술 룰엔진 ────────┘        (둘 중 하나라도 실패 → confidence 0)
```

- `groundextract/grounding.py` — 3단 결정적 판정: `exact`(원문 그대로) →
  `partial_numeric`(같은 숫자, 다른 표기: `1,000,000원`/`₩1000000`/`△500`/`(500)`) →
  `fuzzy`(OCR 노이즈 허용). 어느 것도 아니면 `none` = 환각.
  **fuzzy 티어는 숫자 값을 절대 인정하지 않습니다**: 편집거리 비율은 "번진 글자"와
  "다른 금액"을 구분하지 못하기 때문입니다(`1,900,000원`은 `1,000,000원`에 대해 0.90).
  숫자는 exact 또는 partial_numeric으로만 통과하며, fuzzy는 상호·품목 같은 순수
  텍스트 필드에만 적용됩니다.
- `groundextract/rules.py` — YAML 룰팩을 로드해 `equals`/`sum` 불변식을 **AST 기반 안전
  평가**(`+ - * /`만, 임의 `eval` 금지). 평가 불가한 룰(필드 누락·오작성)은 안전 폐기.
- `groundextract/gate.py` — `run_gate()`: 근거 실패 **또는** 해당 필드를 참조하는 규칙
  위반 시 `verdict=DISCARDED`, `confidence=0.0`.

---

## 코드로 쓰기

```python
from groundextract import ExtractedValue, load_rule_pack, run_gate

doc = "공급가액 1,000,000원\n세액 100,000원\n합계금액 1,100,000원"
pack = load_rule_pack("rules/tax_invoice.yaml")

values = [
    ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액 1,000,000원"),
    ExtractedValue("vat",    "100,000원",   100_000,   "세액 100,000원"),
    ExtractedValue("total",  "1,100,000원", 1_100_000, "합계금액 1,100,000원"),
]

for f in run_gate(values, doc, pack):
    print(f.field, f.verdict.value, f.confidence)
    # supply verified 1.0 / vat verified 1.0 / total verified 1.0
```

core·MCP·벤치·뷰어가 단일 출력계약을 공유합니다:

```python
VerifiedField(field, value, checks[], verdict, confidence)
#   value  -> raw / number / grounding_quote / page / bbox
#   checks -> 수행된 검증 하나당 Check 하나 (name, passed, detail, kind)
```

## MCP 서버 — 에이전트는 verified 값만 소비한다

`groundextract.mcp_server`는 **무의존 MCP 서버**입니다(프로토콜 `2024-11-05`, stdio 위
개행 구분 JSON-RPC 2.0, 표준 라이브러리만 사용). 에이전트를 여기에 연결하면 게이트를
통과하지 못한 숫자로는 아무 행동도 할 수 없게 됩니다.

```bash
python -m groundextract.mcp_server
```

클라이언트 설정(Claude Desktop / Claude Code 등):

```json
{
  "mcpServers": {
    "groundextract": {
      "command": "python",
      "args": ["-m", "groundextract.mcp_server"],
      "cwd": "/path/to/groundextract-kr"
    }
  }
}
```

| 툴 | 모델 필요? | 하는 일 |
| --- | --- | --- |
| `verify_extraction` | ❌ 결정적·무키·오프라인 | `full_text`·`doc_type`·이미 추출된 `values`를 받아 필드별 판정과 요약 반환 |
| `extract_verified` | ✅ 로컬 Ollama | 로컬 오픈웨이트 모델로 추출한 뒤 동일 게이트 실행. Ollama 미가동 시 안내 담은 `isError` 결과 반환 |

`doc_type`은 `tax_invoice` · `statement` · `balance_sheet` 중 하나입니다. 에이전트 측
규칙은 단순해집니다: **`verdict == "verified"`만 실행하고 나머지는 사람에게 에스컬레이션.**

---

## 룰팩은 YAML 한 장 (`rules/tax_invoice.yaml`)

```yaml
doc_type: tax_invoice
rules:
  - name: vat_equals_supply_x_10pct       # 부가세 = 공급가 × 10%
    type: equals
    lhs: {field: vat}
    rhs: {expr: "supply * 0.10"}
    tol: 1.0
  - name: total_equals_supply_plus_vat    # 합계 = 공급가 + 세액
    type: equals
    lhs: {field: total}
    rhs: {expr: "supply + vat"}
    tol: 1.0
```

기본 제공 룰팩: `tax_invoice`(부가세·합계·품목합), `statement`(차변=대변),
`balance_sheet`(대차평형·자산합·부채자본합). `tol`은 값의 단위(원) 기준이라 1원 반올림
여유는 정상입니다.

새 문서 유형 추가는 YAML 한 장 + **3곳 등록**입니다 —
[CONTRIBUTING.md](CONTRIBUTING.md#adding-a-rule-pack-new-document-type) 참조.

---

## NumHall-KR 벤치마크

```bash
python -m groundextract.bench
```

```text
=== NumHall-KR benchmark ===
fields: 146  (ground-truth bad: 29)
confusion: TP=29 FP=52 FN=0 TN=65
Numeric Hallucination Rate: 19.9% (before gate)  ->  0.0% (after gate)
Grounded-Accuracy:          64.4%
Auto-Discard Precision:     35.8%
Auto-Discard Recall:        100.0%
```

| 지표 | 값 | 해석 |
| --- | --- | --- |
| Numeric Hallucination Rate | **19.9% → 0.0%** | 게이트를 통과한 환각 숫자 0건 |
| Auto-Discard Recall | **100.0%** | 라벨된 환각 29건 전부 폐기(FN=0) |
| Auto-Discard Precision | **35.8%** | 정상 필드 52건이 함께 폐기됨 |
| Grounded-Accuracy | **64.4%** | 전체 146필드 중 최종적으로 올바르게 verified 된 비율 |

**불리한 수치를 의도적으로 함께 공개합니다.** 이 시스템의 목적함수는 정확도가 아니라
**누출 0**입니다. 지어낸 숫자가 downstream 장부에 단 하나도 도달하지 않는 것이 목표이고,
그 대가로 정밀도를 내주는 것을 알고서 선택했습니다. 규칙이 깨졌을 때 참조된 필드 중
**누가 거짓말쟁이인지** 아직 특정하지 못하므로 전부 폐기하기 때문입니다.
정밀도 35.8%는 재현율 100%의 비용이며, 이를 좁히는 fault localization이 v0.2의 첫 항목입니다
(재현율을 깎지 않는 **순수 정밀도 개선**).

### NumHall-KR이 무엇이고, 무엇이 아닌가

- **맞는 것**: 결정적·재현 가능한 **회귀 스위트**. 46문서/146라벨필드 =
  **합성 40**(세금계산서 30 + 시산표 10, 고정 시드 생성기) + **수동 작성 6**.
  주입 오류는 종류별로 라벨됩니다(`ungrounded` — 문서에 없는 숫자 / `field-swap` — 문서에는
  있으나 다른 필드의 숫자). 어느 검증이 잡았는지 추적할 수 있습니다.
- **아닌 것**: 실문서 골든셋이 아닙니다. 합성 문서는 이 저장소에 동봉된
  `bench/generate_golden.py`가 생성합니다. 누구나 재생성·확장·반박할 수 있습니다.

  ```bash
  python bench/generate_golden.py --count-tax 30 --count-stmt 10 --seed 42
  ```

  모든 후보 문서는 기록 전 실제 게이트로 검증되며, 같은 `(seed, counts)`는 언제나 동일한
  파일을 재현합니다.
- **실문서 골든셋은 v0.2 로드맵**입니다. 그전까지 이 수치는 게이트 동작에 대한 회귀 신호로
  보아야 하며, 여러분의 실제 운영 문서군에 대한 주장으로 읽어서는 안 됩니다.

실 PDF 종단 실행(Docling → 로컬 오픈웨이트 LLM → 게이트)은 `bench/verify_real.py`에
있습니다. 저장소에 포함된 샘플 PDF를 변환·추출한 뒤 양방향으로 확인합니다(정상 문서는
전부 `verified`, 세액을 변조하면 `discarded`). Ollama 서버가 필요합니다.

---

## 데모 뷰어

문서 이미지 위에 게이트 판정을 bbox로 오버레이하는 정적 뷰어(초록=검증 통과, 빨강=자동
폐기, 빨강 박스 hover 시 실패한 check 표시):

```bash
python -m http.server 8931 --directory viewer   # → http://localhost:8931/index.html
```

픽스처가 저장소에 포함되어 오프라인에서 동작합니다. [`viewer/README.md`](viewer/README.md) 참조.

## 실문서 파이프라인 (옵션)

| 단계 | 모듈 | 설치 |
| --- | --- | --- |
| PDF → 텍스트/표/bbox | `groundextract.adapters.DoclingAdapter` | `pip install -e ".[docling]"` |
| 로컬 오픈웨이트 모델 추출 | `groundextract.llm.OllamaExtractor` | 표준 라이브러리, [Ollama](https://ollama.com) 서버 필요 (기본 모델 `qwen2.5:7b`, Apache-2.0) |
| 게이트 | `groundextract.gate` | 항상 사용 가능 |

둘 다 **옵션 어댑터**입니다. 검증 경로 전체(데모·벤치·MCP `verify_extraction`·뷰어·테스트)는
모델 없이, 키 없이, 네트워크 없이 동작합니다. 상용 API는 의존성이 아니며 기본 엔진은
오픈웨이트입니다.

---

## 한계

프로덕션에 신뢰를 걸기 전에 반드시 읽어야 할 항목입니다.

- **단일 전표 가정** — 문서 하나 = 필드 집합 하나로 취급합니다. 한 페이지 다중 전표,
  연결재무제표, 문서 간 집계는 범위 밖입니다.
- **형제 필드 과폐기** — 위반된 규칙은 참조하는 모든 필드를 오염시킵니다(정밀도 35.8%).
  verified만 자동 처리하는 파이프라인은 필요 이상으로 많은 건을 사람에게 넘깁니다.
- **표 셀 bbox가 거칠다** — bbox는 PDF 어댑터의 줄/영역 단위로 나오며, 표 내부 개별 셀은
  정밀 매핑되지 않습니다. 조밀한 표에서 뷰어 오버레이가 근사치일 수 있습니다.
- **규칙은 산술이지 의미가 아니다** — 숫자의 존재와 정합성만 검증하며, 거래처·일자·계정과목
  분류가 옳은지는 검증하지 않습니다.
- **EXACT 근거 티어에 토큰 경계 검사가 없다** — 부분문자열 매칭이라
  `match_value("1,234", "합계 21,234원")`이 근거 통과하고, 부호를 뺀 값이 `△1,234`처럼 적힌
  음수에 근거될 수 있습니다. 아래 수치 티어들은 경계를 인식하지만 이 티어는 아직 아닙니다.
  [SECURITY.md](SECURITY.md)상 게이트 우회는 최고 심각도이므로, 발견되기를 기다리지 않고
  여기에 먼저 공개합니다.
- **단위 스케일링 미지원** — 천원/백만원 단위로 인쇄된 재무제표를 규칙 평가 전에
  환산하지 않습니다.
- **`balance_sheet`는 아직 벤치 미포함** — 룰팩·뷰어 픽스처·단위테스트는 있으나 146라벨필드는
  `tax_invoice`·`statement`만 다룹니다.
- **한국 규제문서 전용** — 코어 자체는 언어 종속이 아니지만 기본 룰팩과 필드 스펙은
  한국어 기준입니다.

## 로드맵

**v0.2**

- **Fault localization** — 위반된 규칙의 피연산자 중 **누가 틀렸는지** 특정해 전부 폐기하지
  않기. 재현율을 깎지 않는 순수 정밀도 개선.
- **실문서 골든셋** — 비식별화된 실제 문서 라벨셋을 합성 스위트와 **분리 보고**.
- **단위 스케일링** — 천원/백만원 헤더 감지 후 규칙 평가 전 정규화.
- **표 셀 bbox** — 표 필드의 셀 단위 매핑.

**이후**

- `balance_sheet` 벤치 편입, 문서 유형 확장(등기부 등), 공개 리더보드, 프로덕션 서빙.

## 기여하기

```bash
python -m pip install -e ".[dev]"
python -m pytest -q        # 전체 스위트: 결정적·오프라인·무키
python -m ruff check .
```

[CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) ·
[SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)

**게이트 우회는 보안 이슈입니다.** 환각 숫자가 `verified`로 나오는 입력을 발견하면
[SECURITY.md](SECURITY.md) 절차로 제보해 주세요. false negative는 이 프로젝트가 존재하는
이유 그 자체입니다.

## Docker로 실행

```bash
docker build -t groundextract-kr .
docker run --rm groundextract-kr          # 무키 데모
docker run --rm groundextract-kr python -m groundextract.bench
```

## 인용

학술 인용은 [CITATION.cff](CITATION.cff)를 참조하세요(GitHub이 "Cite this repository"
버튼을 렌더링합니다).

## 라이선스

Apache-2.0 — [LICENSE](LICENSE) 참조.
