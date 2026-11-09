# 매시간 실행 지침 (예약 작업용)

예약 작업이 새 세션을 열 때마다 이 순서대로 한 편을 만든다. 저장소는
`rstar2541-blip/ETC`, 브랜치는 `claude/upbeat-franklin-0nprlp`, 작업 폴더는
`naver-blog/`.

## 1. 준비

```bash
git fetch origin claude/upbeat-franklin-0nprlp
git checkout claude/upbeat-franklin-0nprlp
git pull --rebase origin claude/upbeat-franklin-0nprlp
cd naver-blog
```

저장소가 세션에 없으면 `add_repo`(owner `rstar2541-blip`, repo `ETC`, access `push`)로
붙이고 클론한 뒤 진행한다.

## 2. 글감 고르기

```bash
python3 scripts/pick_topic.py
```

`data/current_topic.json`에 모델, 주제(angle), 트렌드 점수, 경쟁 글 제목, 저장 경로가
나온다. `trend_source`가 `none`이면 데이터랩 없이 순번으로 고른 것이다. 트렌드
수치를 지어내지 말 것.

`notes_file`이 있으면 그 파일을 먼저 읽는다. `data/history.jsonl`에서 같은 모델의
이전 글 제목도 확인해 겹치지 않게 한다.

## 3. 글 쓰기

`post_path`에 UTF-8 txt로 저장한다. 형식:

```
제목: <메인 키워드를 앞쪽에 넣은 제목, 25~40자>
메인 키워드: <model.keywords[0]>
모델: <model.label> / 주제: <angle>

[서론]
...

[본론]
■ <소제목>
...
[사진: <어떤 사진을 넣으면 좋을지>]
...

[결론]
...

태그: #BMW #... (10개 안팎)
```

규칙:

- 한국어 해요체, 네이버 블로그 톤. 서론·본론·결론 구성, 본론은 소제목 3~5개.
- 본문(서론~결론) 공백 제외 **2,000자 이상**, 목표 2,300~3,000자.
- 메인 키워드는 제목 앞쪽과 본문에 자연스럽게 4~6번.
- `[사진: ...]` 자리를 4~6곳 넣는다. 오너가 직접 찍은 사진을 넣을 자리다.
- **owned(소유했던 차)**: 1인칭 오너 시점. 메모 파일의 사실을 중심으로 쓴다.
  메모에 없는 구체적 사건(고장·수리비 금액·사고·날짜·주행거리·장소)은 지어내지 않는다.
  글에 꼭 필요하면 그 자리에 `[확인: 실제 경험으로 채워 주세요]`를 넣는다.
- **interested(관심 모델)**: 소유했다고 쓰지 않는다. 메모에 없으면 시승했다고도 쓰지 않는다.
  관심 가는 이유, 제원으로 본 성격, 소유했던 차(config의 owned)와 비교하는 관점으로 쓴다.
- 출력·토크·가속 같은 제원은 확실한 것만 쓰고, 애매하면 웹 검색으로 확인하거나 뺀다.
  가격·시세·보험료 금액은 메모에 없는 한 쓰지 않는다.
- `competition.top_titles`와 겹치는 제목·구성은 피한다. 남의 글을 베끼지 않는다.
- 상투적인 AI 문투(과한 감탄, "결론적으로", 의미 없는 나열)를 피한다.

## 4. 검사·기록·업로드

```bash
python3 scripts/check_post.py <post_path>     # FAIL이면 고쳐서 다시
python3 scripts/record.py "<제목>"
cd ..
git add naver-blog
git commit -m "Blog draft: <model.label> - <angle>"
git push -u origin claude/upbeat-franklin-0nprlp
```

push가 거절되면 `git pull --rebase origin claude/upbeat-franklin-0nprlp` 후 다시 push.
네트워크 오류면 2·4·8·16초 간격으로 최대 4번 재시도.

## 5. 카카오톡 나에게 보내기

PlayMCP 커넥터의 카카오톡 '나에게 보내기' 도구를 찾아(ToolSearch로 `kakao`, `카카오톡`,
`나에게` 검색) 짧은 메시지를 보낸다.

```
[블로그 초안] <제목>
<model.label> · <angle> · <chars>자
https://github.com/rstar2541-blip/ETC/blob/claude/upbeat-franklin-0nprlp/naver-blog/<post_path>
```

도구가 없거나 실패하면 보내지 않고, 마지막 응답에 "카카오 전송 건너뜀: <이유>"를 남긴다.
메시지를 지어내 보냈다고 쓰지 않는다.

## 6. 마무리

마지막 응답에 제목, 모델, 주제, 글자 수, 트렌드 출처, 커밋 여부, 카카오 전송 여부를 한 줄씩 적는다.
