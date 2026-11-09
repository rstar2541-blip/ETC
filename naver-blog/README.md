# 네이버 블로그 BMW 글 자동 생성

3시간마다 BMW 글 초안 한 편을 만들어 `posts/날짜/시간_모델.txt`로 저장하고,
rstar2541@gmail.com으로 본문 전체와 txt 첨부를 보냅니다. 메일에는 Gmail 라벨
`BMW 블로그 초안`이 붙습니다. 게시는 직접 합니다.

데이터랩을 쓰려면 Blog-Autopost 환경 설정의 Network secrets에 `openapi.naver.com`용으로
`X-Naver-Client-Id`, `X-Naver-Client-Secret` 두 헤더를 추가하세요. 없으면 순번으로 고릅니다.

1. **글감**: 네이버 데이터랩 검색어 트렌드로 요즘 검색이 많거나 늘고 있는 모델을 고르고,
   그 모델이 아직 안 다룬 주제를 붙입니다. 블로그 검색 API로 같은 키워드의 상위 글
   제목을 보고 겹치지 않게 씁니다. 키가 없으면 순번대로 고릅니다.
2. **글**: 서론·본론·결론, 공백 제외 2,000자 이상. 사진을 넣을 자리를 `[사진: …]`으로 표시합니다.
3. **검사**: `scripts/check_post.py`가 구성과 글자 수를 확인합니다. 사진 자리와 확인 표시는 글자 수에서 뺍니다.

## 직접 손볼 곳

- **`notes/`**: 소유했던 차의 실제 경험 메모. 여기 적은 내용이 경험담의 바탕이 됩니다.
  메모가 없으면 구체적 사건은 지어내지 않고 `[확인: …]`으로 비워 둡니다.
- **`config/models.json`**: 차종, 검색 키워드, 주제 목록, 소유 모델 비율(`owned_share`).

## 파일

| 경로 | 내용 |
|---|---|
| `RUNBOOK.md` | 예약 작업이 매시간 따르는 지침 |
| `scripts/pick_topic.py` | 글감 선정 (모델·주제·트렌드·경쟁 글) |
| `scripts/trends.py` | 데이터랩 트렌드 점수, 하루 한 번 `data/trends/`에 저장 |
| `scripts/naver_api.py` | 데이터랩·블로그 검색 API 호출 |
| `scripts/check_post.py` | 구성·글자 수 검사 |
| `scripts/record.py` | `data/history.jsonl`에 기록 |
| `scripts/email_payload.py` | Gmail로 보낼 제목·본문·첨부 만들기 |
