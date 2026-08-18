# GroundExtract-KR

**LLM이 지어낸 숫자는 당신의 DB에 도달하지 못한다.**

[![CI](https://github.com/sos37591-prog/groundextract-kr/actions/workflows/ci.yml/badge.svg)](https://github.com/sos37591-prog/groundextract-kr/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Runtime deps: 1](https://img.shields.io/badge/runtime%20deps-1%20%28PyYAML%29-brightgreen.svg)](pyproject.toml)

> **[▶ 라이브 데모](https://sos37591-prog.github.io/groundextract-kr/?doc=balance_sheet&injected=1)** —
> *환각 주입* 토글이 켜진 상태로 열립니다. 비유동자산 한 자리 오독이 문서 텍스트에도 존재해
> 근거검증은 통과하고, 오직 재무상태표 항등식만이 이를 잡아냅니다. 토글을 끄면 같은 문서가
> 전부 초록으로 돌아옵니다. 설치 없이 브라우저에서 바로 동작합니다.
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

> [!WARNING]
> **PyPI는 아직 0.1.3을 서빙합니다. 현재 릴리스는 0.1.8이고, 0.1.3 이하 전 버전에 게이트 우회가 있습니다.**
> `pip install groundextract` 는 0.1.3 을 설치하는데, 문서가 금액을 공백·점 구분으로
> 인쇄했거나 한글/한자 수사로 적었을 때 — 한국 서식 OCR에서 흔한 형태입니다 —
> 오배정된 금액이 confidence 1.0 으로 `verified` 됩니다. 업로드 전까지는 수정본을
> 직접 설치하십시오:
>
> ```bash
> pip install "groundextract @ git+https://github.com/sos37591-prog/groundextract-kr@v0.1.8"
> ```
>
> 전체 목록과 0.1.3 사용자용 완화 방법은
> [릴리스 노트](https://github.com/sos37591-prog/groundextract-kr/releases/latest)에 있습니다.

```bash
git clone https://github.com/sos37591-prog/groundextract-kr.git
cd groundextract-kr
python -m pip install -e .     # Python 3.11+, 런타임 의존성은 PyYAML 하나뿐
python -m groundextract        # 무키(no-API-key) 데모, 네트워크 불필요
python -m groundextract --help # 이 데모가 무엇이고 다음에 무엇을 할지 안내
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
       ! grounding: cited quote not found verbatim in document: '세액  250,000원'
       ! vat_equals_supply_x_10pct: 250,000 vs 100,000 (tol 1) -> diff 150,000
       ! total_equals_supply_plus_vat: 1,100,000 vs 1,250,000 (tol 1) -> diff 150,000
OK total  value=1,100,000원   verdict=verified conf=1.0
summary: {"total": 3, "verified": 1, "discarded": 2, "ungrounded": 1, "rule_pack": "tax_invoice", "rules_applied": 2}
```

요약의 `rule_pack`·`rules_applied`는 **어떤 불변식이 실제로 돌았는지**를 기록합니다.
`rules_applied: 0`이면 산술 검증이 하나도 수행되지 않았다는 뜻이며, "전부 통과"와는 전혀
다른 상황입니다.

`supply`도 함께 폐기되지만 `total`은 살아남는 점을 보십시오. 두 불변식이 모두 깨졌고
`vat`와 `supply`는 **둘 다에 등장**하므로 산술만으로는 누가 거짓말쟁이인지 가릴 수 없어
둘 다 폐기됩니다. `total`은 둘 중 하나에만 등장하므로 이 실패 전체를 설명할 수 없고,
따라서 지목되지 않습니다. 이 좁히기가 [fault localization](#numhall-kr-벤치마크)이며,
좁히지 못하는 경우는 [한계](#한계)에서 공개합니다.

`--json`은 동일한 결과를 기계가 읽을 수 있는 JSON으로 출력하며, 패키지를 설치하면
동일한 동작의 `groundextract` 명령도 PATH에 등록됩니다.

### 내 문서 검증하기

```bash
python -m groundextract verify --doc invoice.txt --values values.json --doc-type tax_invoice
```

`--doc`는 OCR/LLM이 숫자를 읽어낸 **평문 텍스트**, `--values`는 그 도구가 찾았다고 주장하는
값입니다. MCP 툴과 같은 형식이며 `number`·`grounding_quote`는 선택입니다:

```json
[
  {"field": "supply", "raw": "1,000,000원", "number": 1000000,
   "grounding_quote": "공급가액  1,000,000원"},
  {"field": "vat",    "raw": "100,000원"},
  {"field": "total",  "raw": "1,100,000원"}
]
```

직접 만든 룰팩은 `--doc-type` 대신 `--rules my_pack.yaml`을 쓰세요
([rules/README.md](rules/README.md) 참조). **폐기된 필드가 하나라도 있으면 exit 1**이라
파이프라인에서 그대로 게이트로 쓸 수 있습니다:

```bash
python -m groundextract verify --doc doc.txt --values values.json --doc-type tax_invoice   && ./ingest.sh          # 모든 값이 통과했을 때만 실행됨
```

이 명령은 추출기가 아닙니다 — 다른 도구가 만든 결과를 검증할 뿐입니다. 문서에서부터의
전 과정은 [실문서 파이프라인](#실문서-파이프라인-옵션), 에이전트 연동은
[MCP 서버](#mcp-서버--에이전트는-verified-값만-소비한다)를 보세요.

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

벤치마크 스위트에 실제로 들어 있는 문서(`bench/golden/gen_tax_019.json` — 합성·고정 시드)
입니다. 추출기가 `vat` 칸에 한 줄 위의 **공급가액** 값 `17,432,000원`을 넣었습니다. 이 숫자는
문서에 **실제로 인쇄되어 있고**, 모델은 그 줄을 정확히 인용했습니다:

```text
품목: 홈페이지 제작      공급가액 8,651,000원   세액 865,100원
품목: 보안 솔루션 공급   공급가액 8,781,000원   세액 878,100원

공급가액  17,432,000원    ← 인용된 구간, 원문 그대로 — 그리고 잘못된 칸
세액  1,743,200원         ← 원래 가져왔어야 할 값
합계금액  19,175,200원
```

근거검증은 **통과**합니다(인용문이 한 글자도 다르지 않게 문서에 존재). 오직 산술만이
이를 잡아냅니다:

```text
! vat_equals_supply_x_10pct:   17,432,000 vs  1,743,200 (tol 1) -> diff 15,688,800
! total_equals_supply_plus_vat: 19,175,200 vs 34,864,000 (tol 1) -> diff 15,688,800
```

위치 근거(bbox/span 인용)에서 멈추는 도구라면 이 숫자를 초록 체크와 하이라이트 박스까지
붙여 그대로 통과시킵니다.

두 불변식이 모두 깨졌는데, 둘이 함께 지목하는 필드 중 통과한 규칙이 보증해 주지 않는 것은
`vat` 하나뿐입니다. 그래서 `supply`·`total`과 품목 두 건은 **verified로 살아남고** `vat`만
폐기됩니다. [fault localization](#numhall-kr-벤치마크)이 실제 벤치 문서에서 동작하는 모습입니다.

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
from groundextract import ExtractedValue, load_pack, run_gate

doc = "공급가액 1,000,000원\n세액 100,000원\n합계금액 1,100,000원"
pack = load_pack("tax_invoice")   # 패키지에 동봉된 룰팩을 자동으로 찾음

values = [
    ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액 1,000,000원"),
    ExtractedValue("vat",    "100,000원",   100_000,   "세액 100,000원"),
    ExtractedValue("total",  "1,100,000원", 1_100_000, "합계금액 1,100,000원"),
]

for f in run_gate(values, doc, pack):
    print(f.field, f.verdict.value, f.confidence)
    # supply verified 1.0 / vat verified 1.0 / total verified 1.0
```

`load_pack(doc_type)`은 **패키지 내부**에 동봉된 룰팩을 찾으므로 어느 작업 디렉터리에서도,
`pip install`한 wheel에서도 그대로 동작합니다(저장소 체크아웃 불필요).
동봉된 유형은 `available_doc_types()`로 확인하고(`tax_invoice`·`statement`·`balance_sheet`·
`income_statement`·`corporate_tax_return`),
경로는 `default_rules_dir()`가 알려줍니다. 직접 만든 룰팩은 기존대로
`load_rule_pack("경로/my_doc_type.yaml")`를 쓰면 됩니다.

core·MCP·벤치·뷰어가 단일 출력계약을 공유합니다:

```python
VerifiedField(field, value, checks[], verdict, confidence)
#   value  -> raw / number / grounding_quote / page / bbox
#   checks -> 수행된 검증 하나당 Check 하나 (name, passed, detail, kind)
```

## MCP 서버 — 에이전트는 verified 값만 소비한다

**MCP(Model Context Protocol)는 AI 에이전트가 외부 도구를 호출하는 표준 규약입니다.**
이 서버를 붙이면 에이전트는 게이트를 통과하지 못한 숫자에 접근할 수 없습니다 — 모든 값이
판정과 함께 전달되고, 폐기된 값은 폐기된 상태로 전달됩니다.

`groundextract.mcp_server`는 이를 **무의존**으로 구현합니다(프로토콜 `2024-11-05`, stdio 위
개행 구분 JSON-RPC 2.0, 표준 라이브러리만 사용).

```bash
python -m groundextract.mcp_server

# 에이전트 없이 확인하기 — 서버가 stdin/stdout으로 바로 응답합니다:
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m groundextract.mcp_server
```

클라이언트 설정(Claude Desktop / Claude Code 등). `groundextract`가 설치된 **인터프리터의
절대경로**를 쓰세요 — 클라이언트는 자체 환경으로 서버를 띄우기 때문에 맨 `python`은 보통
다른 곳을 가리킵니다:

```json
{
  "mcpServers": {
    "groundextract": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["-m", "groundextract.mcp_server"]
    }
  }
}
```

Windows에서는 `C:\\path\\to\\venv\\Scripts\\python.exe`처럼 적습니다(JSON이라 역슬래시는
이스케이프). 패키지를 설치했다면 룰팩이 패키지 안에 동봉되므로 `cwd`는 필요 없습니다.
저장소를 체크아웃해 그 자리에서 실행한다면 `"cwd": "/absolute/path/to/groundextract-kr"`를
추가하세요.

| 툴 | 모델 필요? | 하는 일 |
| --- | --- | --- |
| `verify_extraction` | ❌ 결정적·무키·오프라인 | `full_text`·`doc_type`·이미 추출된 `values`를 받아 필드별 판정과 요약 반환 |
| `extract_verified` | ✅ Ollama (`OLLAMA_HOST`) | 오픈웨이트 모델로 추출한 뒤 동일 게이트 실행. Ollama 미가동 시 안내 담은 `isError` 결과 반환. **문서 원문이 `OLLAMA_HOST`로 전송됩니다** — [문서가 어디로 가는지](#실문서-파이프라인-옵션) 참조 |

`doc_type`은 `tax_invoice` · `statement` · `balance_sheet` · `income_statement` ·
`corporate_tax_return` 중 하나입니다. 에이전트 측
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
`balance_sheet`(대차평형·자산합·부채자본합), `income_statement`(매출 → 당기순손익 뺄셈 사슬),
`corporate_tax_return`(법인세 과세표준 및 세액조정계산서 — 이 서식은 `(101＋102－103)`처럼
**계산식을 스스로 인쇄**하므로 룰팩은 그것을 옮겨 적은 것입니다).
`tol`은 값의 단위(원) 기준이라 1원 반올림 여유는 정상입니다.

뒤의 세 팩을 합치면 법인세 신고서 한 부가 통째로 덮입니다. 진짜 흥미로운 불변식은 **서식을
가로지르는 것들**입니다 — 손익계산서의 당기순손익은 재무상태표의 이익잉여금이자 법인세
신고서의 101번 줄입니다. 룰팩은 문서 한 건을 범위로 하므로 교차 규칙은 아직 동봉되지
않았지만, 필드명을 맞춰 두었으므로 룰팩으로 표현할 수 있습니다.

새 문서 유형 추가는 YAML 한 장 + **3곳 등록**입니다 —
[CONTRIBUTING.md](CONTRIBUTING.md#adding-a-rule-pack-new-document-type) 참조.

---

## NumHall-KR 벤치마크

```bash
python -m groundextract.bench
```

```text
=== NumHall-KR benchmark ===
fields: 388  (ground-truth bad: 52)
confusion: TP=52 FP=38 FN=0 TN=298
Numeric Hallucination Rate: 13.4% (before gate)  ->  0.0% (after gate)
Grounded-Accuracy:          90.2%
Auto-Discard Precision:     57.8%
Auto-Discard Recall:        100.0%
```

| 지표 | 값 | 해석 |
| --- | --- | --- |
| Numeric Hallucination Rate | **13.4% → 0.0%** | 게이트를 통과한 환각 숫자 0건 |
| Auto-Discard Recall | **100.0%** | 라벨된 환각 52건 전부 폐기(FN=0) |
| Auto-Discard Precision | **57.8%** | 정상 필드 38건이 함께 폐기됨 |
| Grounded-Accuracy | **90.2%** | 전체 388필드 중 최종적으로 올바르게 verified 된 비율 |

게이트 **전** 환각률은 세상이 아니라 이 스위트의 성질입니다. 라벨된 오류가 모호해지지
않도록 문서 한 건에 오류를 하나만 주입하므로, 필드 수가 많은 서식이 들어올수록 달성
가능한 필드 단위 비율에 상한이 생깁니다. 10필드짜리 표준손익계산서와 15필드짜리
세액조정계산서가 합류하면서 19.3%에서 13.4%로 내려갔습니다. 게이트에 대한 주장인
**후** 수치는 0.0%로 그대로입니다.

**불리한 수치를 의도적으로 함께 공개합니다.** 이 시스템의 목적함수는 정확도가 아니라
**누출 0**입니다. 지어낸 숫자가 downstream 장부에 단 하나도 도달하지 않는 것이 목표이고,
그 대가로 정밀도를 내주는 것을 알고서 선택했습니다.

**fault localization**이 그 대가의 대부분을 되찾습니다. 깨진 규칙은 여러 필드를 지목하지만
실제로 유죄인 것은 일부입니다. 그래서 게이트는 먼저 *통과한* 규칙이 입증해 주는 필드를
용의선상에서 제외하고, 남은 후보 중 **모든 위반을 설명하는 가장 작은 조합들**을 지목합니다.
값 하나가 틀렸다면 그 값은 깨진 모든 규칙에 반드시 등장하므로 `{그 값}` 자체가 언제나
최소 설명에 포함됩니다 — 최소 설명들의 합집합을 지목하는 한 진범을 놓칠 수 없습니다.
재현율 100%를 유지한 채 정밀도 35.8% → **57.8%**, 근거정확도 64.4% → **90.2%**.
남은 것은 진짜 모호성입니다. 두 필드가 실패를 똑같이 잘 설명하면 둘 다 폐기됩니다.

**얼마나 좁힐 수 있는지는 규칙이 얼마나 겹치는지가 정합니다.** 각 소계가 다음 규칙의
항이 되는 뺄셈 사슬인 표준손익계산서에서는, 판매비와관리비 한 자리 오독이 **그 필드
하나만** 지목됩니다 — 이웃은 전부 통과한 규칙이 입증해 주기 때문입니다. 반대로 규칙이
하나뿐인 `statement` 룰팩에서는 아무것도 좁힐 수 없습니다.

### NumHall-KR이 무엇이고, 무엇이 아닌가

- **맞는 것**: 결정적·재현 가능한 **회귀 스위트**. 72문서/388라벨필드 =
  **합성 64**(세금계산서 30 + 시산표 10 + 재무상태표 10 + 표준손익계산서 8 +
  법인세 세액조정계산서 6, 고정 시드 생성기) + **수동 작성 8**. 재무제표·법인세 서식은
  실제 신고 서식 레이아웃(계정과목 / 코드 / 금액 3단, 로마숫자 절 표기, 금액란에 `원` 없음)을
  그대로 재현합니다 — 실제 신고서를 OCR한 텍스트와 같은 형태로 시험하기 위해서입니다.
  동봉된 룰팩은 전부 벤치에서 돌아갑니다 — 벤치가 한 번도 돌리지 않는
  룰팩은 회귀가 나도 아무도 모르는 룰팩이며, 두 집합이 어긋나지 않도록 테스트가 고정합니다.
  주입 오류는 종류별로 라벨됩니다(`ungrounded` — 문서에 없는 숫자 / `field-swap` — 문서에는
  있으나 다른 필드의 숫자). 어느 검증이 잡았는지 추적할 수 있습니다.
- **아닌 것**: 실문서 골든셋이 아닙니다. 합성 문서는 이 저장소에 동봉된
  `bench/generate_golden.py`가 생성합니다. 누구나 재생성·확장·반박할 수 있습니다.

  ```bash
  python bench/generate_golden.py --count-tax 30 --count-stmt 10 --count-balance 10 \
      --count-income 8 --count-taxreturn 6 --seed 42
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

화면 상태는 **딥링크**로 공유·캡처할 수 있습니다(호스팅 데모와 localhost 모두 동일):

| 파라미터 | 의미 |
| --- | --- |
| `?doc=<id>` | 문서 선택: `tax_invoice` 또는 `balance_sheet` |
| `&injected=1` | *환각 주입* 토글이 켜진 상태로 시작 |
| `&tip=<field>` | 해당 필드의 폐기 사유 툴팁을 고정 표시 (예: `noncurrent_assets`) |

예시:
[`?doc=balance_sheet&injected=1&tip=noncurrent_assets`](https://sos37591-prog.github.io/groundextract-kr/?doc=balance_sheet&injected=1&tip=noncurrent_assets)

## 실문서 파이프라인 (옵션)

| 단계 | 모듈 | 설치 |
| --- | --- | --- |
| PDF → 텍스트/표/bbox | `groundextract.adapters.DoclingAdapter` | `pip install -e ".[docling]"` |
| 로컬 오픈웨이트 모델 추출 | `groundextract.llm.OllamaExtractor` | 표준 라이브러리, [Ollama](https://ollama.com) 서버 필요 (기본 모델 `qwen2.5:7b`, Apache-2.0) |
| 게이트 | `groundextract.gate` | 항상 사용 가능 |

둘 다 **옵션 어댑터**입니다. 검증 경로 전체(데모·벤치·MCP `verify_extraction`·뷰어·테스트)는
모델 없이, 키 없이, 네트워크 없이 동작합니다. 상용 API는 의존성이 아니며 기본 엔진은
오픈웨이트입니다.

> **문서가 어디로 가는지.** 추출 단계는 `OLLAMA_HOST` 환경변수가 가리키는 Ollama 서버로
> **문서 원문 전체**를 전송합니다(기본값 `http://localhost:11434`). "오픈웨이트"는 라이선스
> 속성이지 네트워크 속성이 아닙니다 — 셸·컨테이너·MCP 호스트에 이 변수가 이미 설정돼
> 있다면(공용 GPU 서버, 원격 엔드포인트, 동료 PC 등) 세금계산서·재무제표가 그곳으로
> 넘어갑니다. 민감한 문서에 이 단계를 돌리기 전에 값을 확인하십시오.
>
> ```bash
> echo "$OLLAMA_HOST"      # 비어 있으면 localhost
> ```
>
> 게이트 자체(`verify_extraction`·벤치·뷰어·테스트)는 소켓을 열지 않습니다.

---

## 한계

프로덕션에 신뢰를 걸기 전에 반드시 읽어야 할 항목입니다.

- **단일 전표 가정** — 문서 하나 = 필드 집합 하나로 취급합니다. 한 페이지 다중 전표,
  연결재무제표, 문서 간 집계는 범위 밖입니다.
- **모호할 때는 여전히 과폐기** — fault localization이 위반을 설명하는 최소 조합까지
  좁히지만, 두 필드가 똑같이 잘 설명하면(품목 행이 없어 세액과 공급가액을 가릴 수 없는
  경우) 둘 다 폐기됩니다(정밀도 57.8%). 룰팩에 서로 겹치는 불변식이 많을수록 좁히기가
  날카로워지며, `statement`처럼 규칙이 하나뿐인 팩은 아예 좁힐 수 없습니다. verified만
  자동 처리하는 파이프라인은 일부 정상 값을 사람에게 넘기게 됩니다.
- **한 값을 건드리는 모든 규칙과 일관되게 틀린 오류는 못 잡습니다 — 게다가 `verified`로
  보고됩니다.** 이 파일에서 가장 날카로운 한계이니, 다열(多列) 서식의 판정을 신뢰하기 전에
  반드시 읽으십시오. **통과한** 규칙은 자기가 이름을 댄 필드를 보증하는데, **함께** 틀린 두
  값이 그 규칙을 만족시킬 수 있습니다. 공급가액과 세액을 둘 다 10배로 오독하면 오류가
  비례적이라 `세액 = 공급가액 × 10%`가 그대로 성립합니다. 그러면 오독된 두 값이 confidence
  1.0으로 `verified`가 되고, 정확히 읽힌 합계금액이 "총액이 안 맞는 유일한 설명"으로 몰려
  폐기됩니다.

  ```text
  supply  10,000,000원   ← 오독    verified   1.0
  vat      1,000,000원   ← 오독    verified   1.0
  total    1,100,000원             discarded  0.0
  ```

  **하나만** 틀린 값은 항상 잡힙니다(재현율 100%). 게이트를 무너뜨리는 것은 **동시에 일관되게**
  틀린 오류이고, 그 전형이 당기/전기 2열 서식을 한 열 밀려 읽는 경우입니다. 겹치는 규칙의
  보증을 인정하지 않으면 잡히지만 정밀도가 17포인트 떨어지므로(57.8% → 40.9%), 이 절충은
  저희가 대신 결정하지 않습니다. **다열 서식을 다루신다면 `verified`를 "사람이 안 봐도 된다"가
  아니라 "어떤 불변식도 이의를 제기하지 않았다"로 읽으십시오.** 다중 오류 벤치 커버리지는
  v0.2 과제입니다 — 현재 스위트는 문서당 오류를 1개만 주입하므로 이 부류는 미측정입니다.
- **표 셀 bbox가 거칠다** — bbox는 PDF 어댑터의 줄/영역 단위로 나오며, 표 내부 개별 셀은
  정밀 매핑되지 않습니다. 조밀한 표에서 뷰어 오버레이가 근사치일 수 있습니다.
- **규칙은 산술이지 의미가 아니다** — 숫자의 존재와 정합성만 검증하며, 거래처·일자·계정과목
  분류가 옳은지는 검증하지 않습니다.
- **근거는 값이 *텍스트 레이어*에 있음을 증명할 뿐, 사람 눈에 보인다는 뜻은 아니다** —
  근거 검증의 기준은 PDF 어댑터가 추출한 텍스트입니다. 흰 글씨·0pt·페이지 밖·주석 레이어처럼
  화면에 보이지 않는 텍스트도 그 안에 포함되어 정상적으로 근거가 됩니다. 따라서 발신자가
  산술까지 맞아떨어지는 비가시 금액 세트를 심어 보내면, 읽는 사람이 본 적 없는 금액이
  `verified`를 받을 수 있습니다. 가시성 검사가 들어오기 전까지(로드맵) **신뢰할 수 없는
  발신자의 문서는 적용 범위 밖**으로 두거나, 숨은 텍스트를 탐지하는 도구로 사전 점검하십시오.
- **인용문은 실재 여부를 검증할 뿐, 적절한 위치인지는 검증하지 않는다** — 게이트는 인용문이
  문서에 그대로 존재하고 그 안에 값이 있을 것을 요구하지만, 그 인용문이 *해당 항목의* 줄인지는
  확인하지 않습니다. 문서 다른 곳에 정당하게 존재하는 금액(품목 단가, 소계, 전기 열 등)이
  엉뚱한 필드의 근거가 될 수 있습니다. 대부분은 산술 룰이 걸러내며, 어떤 룰도 닿지 않은 필드는
  신뢰되지 않고 폐기됩니다. [SECURITY.md](SECURITY.md)상 게이트 우회는 최고 심각도이므로,
  발견되기를 기다리지 않고 여기에 먼저 공개합니다.
- **단위 스케일링 미지원** — 천원/백만원 단위로 인쇄된 재무제표를 규칙 평가 전에
  환산하지 않습니다.
- **한국 규제문서 전용** — 코어 자체는 언어 종속이 아니지만 기본 룰팩과 필드 스펙은
  한국어 기준입니다.

## 로드맵

**v0.2**

- ~~**Fault localization**~~ — 반영 완료. 정밀도 35.8% → 57.8%, 근거정확도 64.4% → 90.2%,
  재현율은 100% 그대로. 남은 것은 진짜 모호성이며, 더 똑똑한 탐색이 아니라 문서당
  **불변식을 더 갖추는 것**이 답입니다.
- **실문서 골든셋** — 비식별화된 실제 문서 라벨셋을 합성 스위트와 **분리 보고**.
- **단위 스케일링** — 천원/백만원 헤더 감지 후 규칙 평가 전 정규화.
- **표 셀 bbox** — 표 필드의 셀 단위 매핑.

**이후**

- 문서 유형 확장(등기부 등), 공개 리더보드, 프로덕션 서빙.

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
