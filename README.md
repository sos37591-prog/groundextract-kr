# GroundExtract-KR 데모 뷰어

빌드 스텝 없는 단일 파일 정적 뷰어입니다 (바닐라 JS + CSS, 오프라인 동작).
실제 규제문서(세금계산서·재무제표) **이미지 위에 게이트 판정을 bbox로 오버레이**합니다.

## 사용법

```bash
# 그냥 열기 — 픽스처(viewer/fixtures/image_data.js)는 저장소에 포함되어 있음
python -m http.server 8931 --directory viewer
#   → http://localhost:8931/index.html
#   (file:// 직접 열기는 브라우저의 로컬 이미지 로드 제약이 있어 로컬 서버 권장)

# 픽스처 재생성 (실제 run_gate를 돌려 생성):
python viewer/build_combined_fixture.py
#   → viewer/assets/*.png + viewer/fixtures/image_data.js
#     (window.GEK_DOCS = [전자세금계산서, 표준재무상태표])

# 실 문서로 만들려면 환경변수로 경로를 전달한다. 원본 PDF는 커밋되지 않는다.
GEK_BALANCE_PDF="/path/to/재무제표.pdf" python viewer/build_combined_fixture.py
#   ⚠ 재무상태표 경로에는 마스킹 단계가 없다 — 커밋된 샘플이 합성이라 가릴 것이
#     없기 때문이다. 따라서 실 PDF를 넘기면 렌더 결과는 추적 파일이 아니라
#     gitignore된 local/balance_sheet_demo.png 로 나간다. 그 이미지에는 사업자
#     등록번호·법인명·전 계정과목 금액이 그대로 있으니 공개 전에 직접 확인할 것.
#     (마스킹이 실제로 적용되는 것은 세금계산서 경로의 승인번호뿐이다.)
```

## 화면 구성

- **문서 탭**: `전자세금계산서 ↔ 표준재무상태표` 전환.
- **문서 이미지 + bbox 오버레이**: 초록 = 근거·산술 검증 통과 / 빨강 = 자동 폐기.
  `환각 주입` 토글 시 **실제로 값이 바뀐 필드** 위에 `✕ AI: <틀린값>` 태그가 뜨고,
  산술 위반으로 **연쇄 폐기된 형제 필드**는 박스만 빨강으로 바뀝니다. 빨강 박스에
  마우스를 올리면 실패한 검증(check 이름 + `diff`) 툴팁이 표시됩니다.
- **추출 필드 판정 표**: 필드 / 추출값 / 판정 / 신뢰도.
- **NumHall-KR 벤치마크 카드**: 숫자 환각률 19.9%→0.0%, 자동폐기 재현율 100% 등 측정 지표.

> 참고: `python -m groundextract.export`는 이 뷰어와 **별개의** 정적 JSON
> (`clean.json`·`injected.json`·`fixtures.js`, `window.__GROUNDEXTRACT_FIXTURES__`)을
> 내보내는 유틸리티입니다. 현재 `index.html`은 `image_data.js`(`window.GEK_DOCS`)만
> 로드합니다.
