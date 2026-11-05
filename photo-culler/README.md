# 사진 정리 (Photo Culler) — 불완전한 사진 골라내기

사진이 너무 많을 때, **초점이 나간 사진 · 눈 감은 사진 · 표정이 어색한 사진 · 연사로 찍은 비슷한 사진**을
자동으로 골라 분류해 주는 프로그램입니다.
모든 분석은 **내 컴퓨터에서만** 이루어지고 사진이 외부로 업로드되지 않습니다.
직접 **Del** 키로 지우기 전에는 사진을 **삭제하지 않고** 폴더로 옮기기만 하며, 언제든 **되돌리기** 할 수 있습니다.

## 프로그램 받기

저장소의 **Releases** 에서 최신 `사진 정리 (Photo Culler)` 를 열고 Assets 에서 받으세요.
(`photo-culler/` 가 바뀌어 푸시될 때마다 GitHub Actions 가 자동으로 새로 빌드합니다.)

| 컴퓨터 | 파일 | 실행 |
|---|---|---|
| Windows 10/11 | `PhotoCuller-Windows.zip` | 압축 풀고 `PhotoCuller\PhotoCuller.exe` 실행. "PC 보호" 창이 뜨면 **추가 정보 → 실행** |
| Mac (Apple 칩, macOS 14+) | `PhotoCuller-macOS.zip` | 압축 풀고 `PhotoCuller.app` 을 **우클릭 → 열기**. 안 열리면 터미널에서 `xattr -cr PhotoCuller.app` |

## 사용법

1. **폴더 선택** (또는 창에 폴더를 끌어다 놓기) → 바로 분석이 시작됩니다.
2. 위쪽 탭 **불량 / 확인필요 / 연사중복 / 스크린샷 / 정상** 으로 결과를 봅니다.
   - 사진을 누르면 오른쪽에 크게 보이고, 얼굴마다 테두리(빨강=눈 감음, 주황=확인 필요)와 이유가 나옵니다.
   - 판정이 틀렸으면 아래 버튼이나 **숫자키 1~5** 로 바꿉니다 (1 불량 · 2 확인필요 · 3 연사중복 · 4 남기기 · 5 스크린샷).
     바꾸면 다음 사진으로 넘어가서 키보드로 빠르게 훑을 수 있습니다. 여러 장을 선택해 한꺼번에 바꿀 수도 있습니다.
   - **Enter** 또는 더블클릭 = 원본 열기
   - **Del** (맥은 delete) = 사진 삭제. 바로 **휴지통**으로 가므로 휴지통에서 복원할 수 있습니다.
     한 장은 묻지 않고 바로 지우고, 여러 장을 선택했을 때만 한 번 확인합니다.
     USB 메모리·SD 카드·네트워크 드라이브처럼 휴지통이 없는 곳은 영구 삭제되므로 처음에 한 번 경고합니다.
     지운 기록은 `_사진정리/deleted.json` 에 남습니다.
3. **정리하기** → 사진 폴더 안 `_사진정리` 폴더로 분류해서 옮깁니다.

```
내 사진 폴더/
├── (정상 사진은 그대로)
└── _사진정리/
    ├── 1_불량/        ← 확인 후 지워도 되는 사진
    ├── 2_확인필요/     ← 애매해서 사람 눈으로 봐야 하는 사진
    ├── 3_연사중복/     ← 비슷한 사진 중 베스트가 아닌 컷
    ├── 4_스크린샷/     ← 정리하기 창에서 체크했을 때만 옮김
    ├── report.html    ← 브라우저용 리포트
    └── report.csv     ← 엑셀용 표
```

4. 마음에 안 들면 **되돌리기** 로 전부 원래 자리로 돌아갑니다.

같은 폴더를 다시 열면 이전 분석 결과와 직접 바꾼 판정을 그대로 불러옵니다.
판정 기준이 바뀐 새 버전에서 예전 결과를 열면, 다시 분석하지 않고 새 기준으로 자동으로 다시 판정합니다.
너무 많이/적게 걸러지면 **민감도**(느슨하게 / 보통 / 엄격하게)를 바꿔서 다시 분석하세요.

## 무엇을 검사하나

| 검사 | 방법 | 결과 |
|---|---|---|
| 사진 전체가 흐림 / 손떨림 | 화면에서 가장 선명한 부분의 선명도(Laplacian 분산) | 불량 |
| 인물 초점 나감 / 초점이 배경에 맞음 | 눈·눈썹 영역의 선명도를 얼굴 크기 기준으로 측정 | 불량 |
| 인물 초점이 약간 부드러움, 주인공(가장 큰 얼굴)만 흐림 | 〃 | 확인필요 |
| 눈 감음 | MediaPipe 얼굴 표정값(eyeBlink) + 눈 세로/가로 비율(EAR) | 불량 |
| 눈을 감았거나 아래를 봄 / 웃느라 눈 감김 / 반쯤 감김 / 한쪽 눈 감김 | 〃 | 확인필요 |
| 어색한 표정 (말하는 중, 찡그림, 입 오므림) | MediaPipe 표정값(jawOpen, browDown, mouthPucker 등) | 확인필요 |
| 얼굴이 프레임에 잘림 | 얼굴 박스가 사진 가장자리 밖으로 나감 | 확인필요 |
| 얼굴이 어두움(역광) / 하얗게 날아감 | 이마~눈 영역 밝기 | 확인필요 |
| 사진이 너무 어두움 / 전체가 허옇게 뜸 | 밝기 히스토그램 | 불량 / 확인필요 |
| 연사·비슷한 사진 | 크기가 같고 10초 안에 찍힌 비슷한 사진을 묶어 점수가 가장 높은 컷만 남김. 촬영 시각 정보가 없는 사진은 훨씬 엄격하게, 흰 화면·검은 화면처럼 밋밋한 이미지는 묶지 않음 | 연사중복 |
| 스크린샷 | 파일·폴더 이름(Screenshot, 스크린샷, 캡처), 아이폰 스크린샷 표시, 세로로 긴 PNG | 스크린샷 (사진 검사는 하지 않음) |

단체 사진에서는 배경의 작은 얼굴(지나가는 사람 등)은 무시하고, 주요 인물만 봅니다.
여러 명 중 일부만 눈을 감았으면 `눈 감음 (1/6명)` 처럼 인원수를 함께 알려 줍니다.

속도: 1200만 화소 사진 기준 1장당 약 0.1초 (4코어). 1,000장이면 2분 남짓.

## 휴대폰 사진은?

- **안드로이드**: USB로 연결해 `DCIM/Camera` 폴더를 PC로 복사한 뒤 실행
- **아이폰**: 사진 앱에서 내보내거나 iCloud 사진 다운로드 → HEIC도 그대로 지원
- **구글 포토**: [Google Takeout](https://takeout.google.com)으로 내려받은 폴더에 실행

## 한계 (알아 두면 좋은 점)

- 판정 기준은 휴리스틱입니다. 처음에는 정리하기 전에 결과를 훑어보세요.
- **아래를 보는 눈**과 **감은 눈**은 사진만으로 구분이 어려워 '확인필요'로 분류합니다.
  환하게 웃어서 눈이 감긴 사진도 일부러 남기는 경우가 많아 '확인필요'로 둡니다.
- 얼굴이 아주 작으면(사진 긴 변 2000px 기준 폭 48px 미만) 표정·눈은 판정하지 않습니다.
- 선글라스, 마스크, 옆모습은 눈·표정 판정이 부정확할 수 있습니다.
- 하늘·벽처럼 무늬가 거의 없는 사진은 '흐림'으로 잘못 잡힐 수 있습니다.
- RAW 파일(CR2/NEF/ARW/DNG)과 동영상은 지원하지 않습니다.

---

## 개발자용

### 소스에서 실행

Python 3.10 이상.

```bash
cd photo-culler
python -m venv .venv
```

Windows는 `.venv\Scripts\activate`, macOS/Linux는 `source .venv/bin/activate` 후:

```bash
pip install -r requirements.txt
```

```bash
python photo_cull_app.py
```

처음 실행할 때 얼굴 인식 모델 2개(약 4MB)를 `models/` 폴더에 자동으로 받습니다.

### 터미널 버전

화면 없이 같은 엔진을 쓰는 명령줄 버전도 있습니다.

```bash
python photo_cull.py scan "D:\사진\2026 제주여행" -r
```

```bash
python photo_cull.py apply "D:\사진\2026 제주여행"
```

```bash
python photo_cull.py undo "D:\사진\2026 제주여행"
```

옵션: `-r` 하위 폴더 포함, `-s low|normal|high` 민감도, `-w 4` 동시 작업 수,
`apply --skip-review` '확인필요'는 옮기지 않음, `apply --screenshots` 스크린샷도 옮김. `report.csv` 의 `판정` 열을 엑셀에서 고친 뒤
`apply` 하면 고친 대로 옮깁니다.

### 직접 빌드

`.github/workflows/photo-culler-build.yml` 과 같은 순서입니다.

```bash
pip install --no-deps mediapipe==1.2.2
```

```bash
pip install -r requirements-build.txt
```

```bash
python -c "import photo_cull; photo_cull.ensure_models()"
```

```bash
pyinstaller --noconfirm photo_culler.spec
```

결과물은 `dist/PhotoCuller/` (macOS는 `dist/PhotoCuller.app`).
빌드한 프로그램이 제대로 도는지는 `PhotoCuller --selftest <사진폴더> <결과.json>` 으로 확인할 수 있습니다.

### 파일 구성

| 파일 | 내용 |
|---|---|
| `photo_cull.py` | 분석 엔진 + 터미널 버전 |
| `photo_cull_app.py` | 프로그램 화면 (PySide6) |
| `photo_culler.spec` | PyInstaller 빌드 설정 |
| `assets/` | 앱 아이콘 |

### 사용한 모델

- 얼굴 검출: [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) (MIT)
- 얼굴 랜드마크·표정: [MediaPipe Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) (Apache 2.0)
