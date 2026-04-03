# airport-incheon-project

실시간 인천공항 주차 정보와 차량 이동 분석 UI를 함께 보는 프로토타입입니다.

## Run

1. 환경변수 설정

```bash
cp .env.example .env
export AIRPORT_API_KEY="발급받은_일반인증키"
```

2. 서버 실행

```bash
python3 server.py
```

3. 브라우저 접속

```text
http://127.0.0.1:8000
```

## API

- `GET /api/health`
- `GET /api/parking/live`

`/api/parking/live`는 공공데이터포털 인천공항 주차 API를 읽어 다음처럼 가공된 JSON을 반환합니다.

```json
{
  "status": "ok",
  "fetchedAt": "2026-04-03T16:25:00+09:00",
  "sourceUpdatedAt": "2026-04-03T16:22:14+09:00",
  "summary": {
    "totalOccupied": 0,
    "totalCapacity": 0,
    "totalAvailable": 0,
    "occupancyRate": 0,
    "hotspot": "T1 장기 P1 주차타워",
    "hotspotRate": 99.6,
    "congestedZones": 3
  }
}
```

## Notes

- 인증키는 프론트에 직접 넣지 않고 서버 환경변수로만 사용합니다.
- GitHub Pages 정적 배포만으로는 실시간 백엔드 API가 동작하지 않으므로, 실제 실시간 연동은 이 서버나 별도 백엔드 배포가 필요합니다.

## Render Deploy

이 저장소에는 [render.yaml](/home/tilon/jiseolin/render.yaml)이 포함되어 있어 Render에 바로 백엔드를 올릴 수 있습니다.

1. Render에서 `New +` -> `Blueprint`
2. 이 GitHub 저장소 선택
3. 서비스명 `airport-incheon-api` 확인
4. 환경변수 `AIRPORT_API_KEY`에 공공데이터포털 인증키 입력
5. 배포 완료 후 발급된 URL 확인

예시:

```text
https://airport-incheon-api.onrender.com
```

## GitHub Pages Frontend 연결

GitHub Pages에서 실시간 API를 쓰려면 [app-config.js](/home/tilon/jiseolin/app-config.js)의 `API_BASE_URL`을 배포된 백엔드 주소로 바꿔야 합니다.

```js
window.AIRWATCH_CONFIG = {
  API_BASE_URL: "https://airport-incheon-api.onrender.com"
};
```

그다음 배포에 사용하는 브랜치로 푸시하면 Pages 프론트가 외부 백엔드의 `/api/parking/live`를 30초마다 읽습니다.
