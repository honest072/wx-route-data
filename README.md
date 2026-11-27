# 7C Turbulence 원자료 저장소 (GH Rev.1)

ECMWF(유럽중기예보센터) IFS 0.25° 공개 예보를 AWS에서 직접 받아, 7C Turbulence 앱의 노선별 계산 지점 값으로 바꿔 두는 저장소입니다. 3시간마다 GitHub가 새 발표분이 있는지 확인하고, 있으면 다시 계산해 `data` 가지(branch)에 올립니다. 앱은 거기서 노선 파일 하나만 받아 갑니다.

- 자료 출처: ECMWF IFS open data (AWS `ecmwf-forecasts`), CC-BY-4.0 · ECMWF 이용약관 — 앱 화면에 출처를 표시해야 함
- 들어가는 값: 고도 9개 층(850~150hPa)의 풍향·풍속·기온·고도, 측면 보조 지점의 바람, 지상 CAPE·10m 돌풍·대류성 강수·강수·상층운
- 예보 범위: 발표 시각부터 72시간, 3시간 간격
- 개인정보·비밀번호 없음(공개 기상 자료와 공개 항적 좌표만 사용)

## 처음 설정 (약 30분, 한 번만)

1. **계정 만들기:** github.com 가입(무료).
2. **저장소 만들기:** 오른쪽 위 `+` → `New repository`
   - 이름: 예) `wx-route-data` (회사명이 드러나지 않는 이름 권장)
   - `Public`(공개) 선택 — 자동 작업 시간 제한이 없고, 앱이 별도 설정 없이 결과를 받을 수 있음
   - `Add a README file` 체크 → `Create repository`
3. **파일 올리기:** 저장소 화면 `Add file` → `Upload files` → 이 ZIP을 푼 폴더 안의 **내용물 전체**(`.github`, `pipeline`, `README.md`)를 끌어다 놓기 → 아래 `Commit changes`
   - `.github` 폴더가 안 보이거나 안 올라가면(숨김 폴더): `Add file` → `Create new file` → 파일 이름 칸에 `.github/workflows/ecmwf.yml` 입력 → ZIP 안의 같은 파일 내용을 붙여넣기 → `Commit changes`
4. **자동 작업 권한 확인:** `Settings` → `Actions` → `General` → 맨 아래 `Workflow permissions`에서 `Read and write permissions` 선택 → `Save`
5. **시험 실행:** `Actions` 탭 → (처음이면 `I understand my workflows, go ahead and enable them`) → 왼쪽 `ecmwf-route-data` → 오른쪽 `Run workflow` → `Run workflow`
6. **결과 확인(5~15분):**
   - 초록 체크 = 성공 → 저장소 위쪽 가지 선택에서 `data`를 고르면 `manifest.json`과 `r` 폴더가 보임
   - 빨간 X = 실패 → 실패한 실행을 눌러 빨간 단계를 펼친 화면을 캡처해 보내 주세요
7. **앱 연결:** 아래 주소를 알려 주세요(계정·저장소 이름만 바꾸면 됨)
   `https://raw.githubusercontent.com/<계정>/<저장소>/data/manifest.json`

## 평소에는

- 손댈 일 없음. 컴퓨터를 켜 둘 필요도 없음(GitHub 컴퓨터에서 실행)
- 가끔 `Actions` 탭에서 초록 체크가 이어지는지 확인. 앱 화면의 'ECMWF ○시 발표' 시각으로도 확인 가능
- 새 발표분이 없으면 1분 안에 '건너뜀'으로 끝남(정상)
- GitHub는 오랫동안 저장소 활동이 없으면 예약 실행을 멈추고 메일로 알려 줄 수 있음 → `Actions` 탭에서 다시 켜기

## 노선이 바뀌면

앱의 항적 경로(ADS-B)를 갱신하면 `pipeline/routes.json`도 다시 만들어야 합니다. 새 리비전과 함께 드리는 `routes.json`으로 파일만 바꿔 올리면 됩니다.

## 구성

| 파일 | 역할 |
|---|---|
| `.github/workflows/ecmwf.yml` | 3시간마다 실행 설정 |
| `pipeline/make.py` | 최신 발표분 확인 → 필요한 변수·층만 받기 → 노선 범위로 자르기 → 지점별 보간 → JSON 저장 |
| `pipeline/routes.json` | 앱의 노선별 계산 지점(67개 노선 × 왕복 = 134개 방향) |
| `pipeline/requirements.txt` | 필요한 파이썬 구성 요소 |

결과(`data` 가지): `manifest.json`(발표 시각·단계·노선 목록), `r/<출발>-<도착>.json`(노선별 값, 정수로 줄여 저장 — 풍속 kt×10, 기온 ℃×10, 고도 m, 돌풍 kt×10, 강수 mm/h×100, 상층운 %)

## Rev.9 추가 (2026.11.27)
- adsb/route.py: 주간 ADS-B 작업(adsb-weekly)에서 노선·방향별 대표 경로를 '여러 편 중앙값'으로 계산해 route.json·app.json(RTE)에 담음
  (기존 대표 한 편 방식은 그날 그 편의 기상 회피·관제 지시 흔적이 남아 김포→제주 등에서 경로가 지그재그로 보였음)
- 앱 경로와 계산 지점(pipeline/routes.json)은 서로 같아야 하므로, 첫 실행 결과(route.json)를 받아 다음 앱 개정 때 함께 교체함
