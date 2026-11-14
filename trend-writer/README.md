# 트렌드 라이터

트렌드 키워드를 골라 네이버 블로그 홈피드용 제목과 글, 소주제별 이미지까지 만드는 도구예요. Claude로 동작합니다.

1. **주제**: 크리에이터 어드바이저나 데이터랩에서 본 주제를 붙여넣거나 AI 추천을 받고, 네이버 데이터랩 검색 추이로 점수를 매겨 고릅니다.
2. **제목**: 웹 검색으로 사실을 먼저 확인한 뒤(팩트 노트) 이득형·손해형·위협형 제목을 5개씩 만들고 유형별 BEST를 표시합니다.
3. **글**: 고른 제목과 팩트 노트로 1~2줄 단락, 굵은 소제목의 모바일용 글을 씁니다.
4. **이미지·내보내기**: 소제목마다 네이버 이미지 검색으로 고르거나 Gemini로 이미지를 만들고, 제목·본문을 복사하고 이미지를 저장합니다.

## 필요한 키

모두 선택 사항이고, 설정 화면에서 넣습니다. 키는 그 기기에만 저장되고 각 서비스로만 전송돼요.

| 키 | 쓰는 곳 | 없으면 |
| --- | --- | --- |
| Anthropic API 키 | AI 추천, 자료 조사, 제목, 글, 이미지 기획 | 프롬프트를 복사해 claude.ai에 붙여넣는 수동 모드 |
| 네이버 API 키 | 데이터랩 점수, 이미지 검색 | 후보에서 직접 고르기 |
| Gemini API 키 | AI 이미지 생성 | 이미지 검색만 사용 |

- **Anthropic**: [console.anthropic.com](https://console.anthropic.com/)에서 발급. 모델은 기본 Claude Opus 5.5이고 설정에서 Sonnet 5.5나 Haiku 5.5로 바꿀 수 있어요. 글 한 편에 조사·제목·글·이미지 기획 요청이 나가고, 사용량만큼 과금됩니다.
- **네이버**: 네이버 클라우드 콘솔 > NAVER API HUB 앱의 Client ID / Client Secret (blog-autopost의 `.env`에 넣은 `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`과 같은 키). 그 앱에 **검색어 트렌드**와 **검색 > 이미지** API가 모두 추가돼 있어야 합니다. 예전 네이버 개발자센터 키라면 설정에서 키 종류를 바꿉니다.
- **Gemini**: Google AI Studio에서 발급. 이미지 모델 ID는 설정에서 바꿀 수 있어요(기본 `gemini-2.5-flash-image`).

## 실행

네이버 API는 웹페이지에서 직접 부를 수 없어서, 네이버 기능은 **안드로이드 앱**이나 **PC의 로컬 서버**로 실행했을 때만 동작합니다.

```bash
# PC: Node.js 18 이상
node trend-writer/server.mjs
# http://localhost:8000
```

`index.html`을 그냥 열어도 되지만, 그때는 데이터랩 점수와 네이버 이미지 검색이 꺼집니다.

로컬 서버는 이 컴퓨터에서만 접속할 수 있고(127.0.0.1), 네이버 API 두 곳으로 가는 요청과 방금 검색 결과에 나온 이미지 내려받기만 대신 전달해요.

## 데이터랩 점수 읽는 법

데이터랩 API는 인기 검색어 목록을 주지 않고, 넣은 키워드의 검색 추이를 상댓값으로 돌려줍니다. 그래서 후보마다 **검색 키워드**(2~3단어)를 정해 최근 30일 추이를 조회해요. 긴 기사 제목을 붙여넣었다면 Claude가 검색 키워드를 뽑아 주고, 직접 고쳐도 됩니다.

- **상승 배수**: 최근 3일 평균 ÷ 그 이전 평균. 순서는 이 값이 큰 순입니다.
- **검색량**: 후보 중 최근 7일 검색이 가장 많은 키워드를 100으로 본 값.
- 데이터는 어제까지 반영돼요. 오늘 막 터진 주제는 아직 낮게 나올 수 있습니다.

## 쓰기 전에

- **사실 확인**: 글은 팩트 노트에 있는 내용만 쓰도록 되어 있지만, 발행 전에 숫자·날짜·이름을 출처와 한 번 더 대조하세요. 팩트 노트는 직접 고칠 수 있어요.
- **이미지 저작권**: 네이버 검색으로 찾은 이미지는 다른 사람의 저작물입니다. 원본 페이지에서 사용 조건과 출처 표기를 확인하세요. AI 이미지 설명은 실존 인물의 얼굴과 로고를 넣지 않도록 만들어집니다.
- 본문을 복사하면 이미지 자리가 `(이미지 1)`처럼 표시되고, 저장한 파일 이름은 `image-01.png`처럼 같은 번호를 씁니다.

## 프롬프트 바꾸기

설정에서 제목 프롬프트와 글쓰기 프롬프트를 고칠 수 있어요. `{{topic}}`, `{{notes}}`, `{{title}}`, `{{length}}`, `{{sections}}`, `{{tone}}` 자리에 값이 들어갑니다.

## 안드로이드 앱 (APK)

`android/` 폴더는 이 페이지를 WebView로 띄우는 앱이에요. 빌드할 때 `index.html`과 `vendor/`가 앱 안에 복사되니, 기능 수정은 `index.html`만 고치면 됩니다. 앱에서는 네이버 API 호출, 사진첩 저장(Pictures/TrendWriter), 서식 있는 복사, 외부 링크 열기를 앱이 대신 처리해요.

### APK 받기

`trend-writer/` 아래가 바뀌어 푸시되면 GitHub Actions(`.github/workflows/trend-writer-apk.yml`)가 APK를 빌드해서 **Releases**에 올려요.

1. 폰에서 이 저장소의 **Releases** 페이지를 열어요.
2. 최신 `트렌드 라이터 v1.0.N`의 `trend-writer-v1.0.N.apk`를 내려받아요.
3. 설치할 때 "출처를 알 수 없는 앱 설치"를 허용해 주세요. (Android 10 이상)

새 버전은 기존 앱 위에 덮어 설치되고 저장한 키와 기록은 유지돼요.

### 서명 키

저장소에 들어 있는 `android/keystore/trend-writer.jks`(비밀번호 `trendwriter`)로 서명해요. 개인용으로는 충분하지만, 다른 사람에게 배포하려면 직접 만든 키를 GitHub Secrets에 넣어 주세요.

- `TW_KEYSTORE_BASE64`: `base64 -w0 my.jks` 결과
- `TW_KEYSTORE_PASSWORD`, `TW_KEY_ALIAS`, `TW_KEY_PASSWORD`

키를 바꾸면 기존 앱을 지우고 새로 설치해야 하고, 이때 저장한 키와 기록도 지워져요.

### 직접 빌드

Android SDK와 JDK 17이 있으면:

```bash
cd trend-writer/android
gradle assembleRelease
# app/build/outputs/apk/release/app-release.apk
```

## Claude SDK 번들

`vendor/anthropic-sdk.js`는 공식 `@anthropic-ai/sdk` 1.8.0을 한 파일로 묶은 것입니다. 버전을 올리려면:

```bash
mkdir /tmp/sdk && cd /tmp/sdk && npm init -y && npm install @anthropic-ai/sdk esbuild
printf 'import Anthropic from "@anthropic-ai/sdk";\nexport default Anthropic;\nexport { Anthropic };\n' > entry.js
npx esbuild entry.js --bundle --format=iife --global-name=AnthropicSDK --platform=browser --target=es2020 --minify --legal-comments=none --outfile=anthropic-sdk.js
```
