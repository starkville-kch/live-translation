# 단일 실행 파일 빌드 기록 / Single Executable Build Log
### 실시간 예배 번역 시스템 / Live Translation System

단일 `.exe` 빌드 과정에서 시도하고 실패하고 성공한 내용을 기록합니다. 재빌드 시 참고하세요.

로컬 작업 폴더 정리 후 검증 증거는 `.agent/archive/2026-11-08-worship-review/`에 있습니다. 재사용 UI 검증 도구는 `.agent/scripts/ui/`, 모델 캐시는 `.agent/cache/huggingface/`, 네이티브 빌드 지원 파일은 `.agent/build_support/`로 분리했습니다. `.agent/build_env`, `.agent/build_deps`와 현재 `.agent/dist`는 유지합니다. 과거 EXE 중복 사본·한 폴더 빌드·PyInstaller 작업물·테스트 임시 폴더는 정리했으며, 보관 목록은 `.agent/archive/INDEX.md`에 있습니다.

## 현재 배포 정책: 영문 변경 기록과 Cloudflare 설치

`CHANGELOG.md`는 기존 형식대로 영어로 유지합니다. 다른 문서의 한국어 사용 원칙과 구분하며, 사용자가 요청하기 전에는 변경 기록의 언어를 바꾸지 않습니다.

`cloudflared.exe`는 배포 ZIP에 넣지 않습니다. 표준 빌드에서 이 파일을 배포 폴더로 복사하지 않으며, 그 폴더에 남아 있는 이전 파일도 ZIP 생성 시 이름의 대소문자나 하위 경로와 관계없이 제외합니다. 공용 HTTPS를 사용할 PC에서는 `SKC_setup.exe`의 다운로드 버튼을 사용합니다. 바이너리 없이 서비스 시작을 누르면 기존 설정 마법사가 다운로드를 제안합니다. 이미 설치한 파일은 삭제하지 않습니다.

실제 ZIP을 만드는 회귀 테스트로 설정 마법사·번역 EXE는 포함되고 기존 `cloudflared.exe`는 제외되는 것을 확인했습니다. 어느 EXE든 없으면 ZIP 생성 전에 오류로 중단합니다. 기존 실행 전용 ZIP의 설정 마법사 누락을 지적받은 뒤 두 EXE를 v3.4.2로 재빌드했습니다. 이전 ZIP·EXE는 당시 기록으로 보존하며, 아래의 Cloudflare 동봉 기록은 현재 정책보다 앞선 빌드입니다.

## 2026-11-09 주일 전 최종 체크포인트

최신 패키지는 `.agent/dist/SKC_live_translation_v3.4.4_20261109_final.zip`입니다. 공용 HTTPS 접기 표시를 재생 버튼으로 보일 수 있는 문자에서 일반 텍스트 `▸` / `▾`로 바꿨습니다. 키보드 포커스 테두리는 유지합니다. 설정 검사 7개와 현재 PDF 경로를 사용하는 패키징 검사 5개가 통과했습니다. 네이티브 설정 창의 최종 시각 확인은 도구 연결 제한으로 수행하지 못했습니다.

정리된 현재 한국어 PDF 경로는 `/docs/한인교회 통역 시스템 처음 셋업 (SKC-translate).pdf`입니다. 직전 `_EXE안내.pdf`와 해시가 같아 내용을 다시 만들지 않았습니다. README·생성 도구·패키저도 기본 파일명으로 맞췄습니다. 아래 접미사 파일명과 초안 보존 설명은 당시 기록입니다.

두 EXE의 소스·자산·영문 변경 기록 일치, 기존 앱 모듈·네이티브 인코더 동일성, 직접 EXE 관리자·로고·도움말 HTTP 200과 종료를 확인했습니다. HTTP 준비는 5.700초였고 기존 SIGINT 경로의 종료 코드 2를 그대로 기록했습니다. 한영 PDF는 직전 16쪽 시각 검증본과 같습니다. ZIP 13개 파일의 CRC·해시·개인 키 값 부재를 확인했으며, ZIP은 121,121,315바이트, SHA-256 `a27f05a3b4a503052a81efcf9449e6f3b89503a2b5a7d9c7b791bee5fc3d9354`입니다. 증거는 `.agent/archive/2026-11-09-fold-indicator/`에 있습니다.

구현·패키지 준비는 마쳤고 실제 주일예배 결과는 아직 확인하지 않았습니다. 평소 실행에서 생기는 시작 로그·운영 로그·해당 세션 기록·MP3를 바탕으로 예배 후 검토하며, 추가 유료 평가나 기능은 그때 논의합니다. 이번 정리에서 설치 폴더·로컬 마이크 설정·Git 인덱스는 바꾸지 않았습니다.

## 2026-11-09 v3.4.4 최종 안내: EXE 직접 실행과 한국어·영어 PDF

직전 배포본은 `.agent/dist/SKC_live_translation_v3.4.4_20261109_bilingual.zip`입니다. 예배 때는 `SKC_translation.exe`를 직접 실행하고 처음 설정할 때는 `SKC_setup.exe`를 사용합니다. `SKC_start.bat`은 저장소의 개발용 도구로만 남기고 배포에서 제외합니다. 이전 파일이 배포 폴더나 하위 폴더에 남아 있어도 이름의 대소문자와 관계없이 ZIP에서 제외합니다.

한국어·영어 설치 안내는 각각 8쪽입니다. 예시는 Jackson Korean Church(JKC), `jkc.local:8080`, `JKC-Live-Translation`으로 통일했습니다. 실제 교회 설정은 바꾸지 않았습니다. Fifine·DJI 등 제품명과 교회별 수신기 모드 안내는 배포 문서와 한영 HTML에서 제외했습니다. 한국어 원본은 기존 Markdown이며, 영문 원본은 `/docs/SKC_Live_Translation_Setup_Guide_EN.md`입니다. PDF는 `/docs/한인교회 통역 시스템 처음 셋업 (SKC-translate)_EXE안내.pdf`, `/docs/SKC_Live_Translation_Setup_Guide_EN.pdf`입니다. ZIP의 한국어 PDF는 접미사 없는 이름을 사용합니다.

최종 16쪽을 렌더링해 확인하고 텍스트·글꼴·링크·오래된 예시와 장비 안내 부재를 검사했습니다. 한국어는 맑은 고딕, 영어는 Segoe UI를 내장하고 같은 Letter 레이아웃과 예시 상자를 사용합니다. 영문 PDF는 아래처럼 생성합니다.

```bat
conda activate agent
python tools\build_installation_guide.py
python tools\build_installation_guide.py --language en
```

두 EXE도 새 한영 HTML·영문 변경 기록으로 다시 빌드했습니다. 모듈 17개·자산 25개가 소스와 일치하며, 앱 모듈과 네이티브 MP3 인코더는 앞서 실제 녹음 검증을 통과한 v3.4.4와 같습니다. 이번에는 같은 녹음 시험을 반복하지 않았습니다. 배치 없이 EXE를 시작해 관리자·로고·도움말 HTTP 200, 내장 도움말 일치와 시작 로그를 확인했습니다. HTTP 준비는 6.234초입니다. 종료 요청 후 기존 SIGINT 경로의 로그와 종료 코드 2를 확인했으므로 코드 0의 정상 종료로 기록하지 않았습니다. 앞선 배치 실행 검사는 래퍼의 종료 코드를 확인한 기록입니다.

패키징 검사 5개가 통과했습니다. 어느 EXE 또는 어느 언어의 PDF든 빠지면 ZIP 생성 전에 중단합니다. ZIP의 13개 파일 CRC·해시·개인 키 값 부재를 확인했으며 배치·Cloudflare 바이너리·실제 환경 파일은 넣지 않았습니다. ZIP은 121,121,063바이트, SHA-256 `f90f42d6f8d820def22d039dd31bd27b51225ee68b29f10da89a2b51e94c3618`입니다. 검증은 `.agent/archive/2026-11-09-exe-distribution/`에 있습니다. 유료 API·실제 마이크·외부 설치 변경·커밋은 없었습니다. 네이티브 설정 창의 시각 검사는 이전과 같이 도구 연결 제한으로 수행하지 못했습니다.

아래 절은 이전 배포본 기록입니다.

## 2026-11-09 v3.4.4 문서 수정: 처음 사용하는 교회 담당자 안내

당시 문서 수정본은 `.agent/dist/SKC_live_translation_v3.4.4_20261109_guide2.zip`입니다. 아래 v3.4.4 원본 ZIP에서 PDF 한 파일만 바꿨습니다. 13개 파일의 이름·구성을 유지하며 두 EXE와 나머지 파일은 원본과 바이트 단위로 같습니다. 기존 실행 검증을 반복하거나 EXE를 다시 빌드하지 않았습니다.

설치 안내는 사용자가 제공한 Obsidian PDF의 차분한 순서를 참고해 처음 설치하는 담당자에게 말하는 문장으로 다시 썼습니다. 예시 입력과 버튼을 누른 뒤의 결과를 설명하고, README·CHANGELOG 배포 정책 같은 개발자 설명은 뺐습니다. 잘 작동하는 기존 키를 무조건 교체하거나 특정 선불 금액을 고정하는 이전 참고 문서의 지침은 옮기지 않았습니다.

ReportLab의 글자 단위 줄바꿈 대신 Chrome의 인쇄 엔진을 사용합니다. 맑은 고딕을 내장하고 본문 13.5pt, Letter 용지, `word-break: keep-all`과 예시 상자를 사용합니다. 최종 8쪽을 렌더링해 확인했으며 텍스트·링크·글꼴도 검사했습니다. 원본 PDF와 `_test.pdf`, 이전 `_맑은고딕.pdf`는 보존했습니다. 현재 소스 PDF는 `/docs/한인교회 통역 시스템 처음 셋업 (SKC-translate)_읽기개선.pdf`이며 ZIP에서는 접미사 없는 이름입니다.

패키징 회귀 검사 4개 통과, ZIP CRC와 PDF 외 모든 항목의 동일성을 확인했습니다. ZIP은 121,034,218바이트, SHA-256 `3919dfd84cc4d43960ff1f84d3b63e094d678effdc3e8107515d083208a41326`입니다. 증거는 `.agent/archive/2026-11-09-guide-readability/`에 있습니다. 외부 설치 변경·API 호출·커밋은 하지 않았습니다.

PDF 재생성에는 프로젝트에서 사용하는 Playwright CLI·Chrome과 Conda `agent`의 `markdown-it-py`가 필요합니다. Obsidian이나 ReportLab은 필요하지 않습니다. 다음 CMD 명령으로 만들며, 임시 HTML·브라우저 프로필은 `.agent/` 안에 둡니다. 문서용 브라우저와 임시 HTTP 서버는 생성 후 종료합니다.

```bat
conda activate agent
python tools\build_installation_guide.py
```

아래 절은 원본 앱 빌드와 이전 PDF의 기록입니다.

## 2026-11-09 v3.4.4: QR 자동 생성과 읽기 쉬운 설치 안내

원본 앱 빌드 패키지는 `.agent/dist/SKC_live_translation_v3.4.4_20261109.zip`입니다. 두 EXE와 한국어 설치·사용 PDF, 버전이 표시된 HTML 안내를 포함하는 13개 파일 구성입니다. 루트 README·CHANGELOG를 제외하고 PDF에서 GitHub로 안내합니다. 라이선스 고지와 실제 사용 중인 `config/name_corrections.yaml`은 유지합니다. 시작 시 CHANGELOG를 다시 추출하지 않습니다. `cloudflared.exe`·실제 `.env`·활성 `config.yaml`·녹음·평가 자료는 포함하지 않았습니다.

설정 저장 후 `qr_local.png`와 자체 포함 `qr_local_print.html`이 생성됩니다. Public HTTPS를 펼칠 때 Windows 서비스 조회가 UI를 막지 않도록 백그라운드로 옮겼습니다. 중복 조회를 억제하고 완료 후 상태를 갱신합니다. 작은 창의 고급 설정은 스크롤로 접근하며 저장 버튼은 고정합니다. 비밀번호는 선택적 현장 운영자 인증이므로 1번에 유지하고 잘리던 설명을 고쳤습니다.

- 실제 Tk 위젯·느린 서비스 조회·최소 창 크기·QR 생성·ZIP 검사: 15개 통과. 제한 환경에서 Tcl 읽기와 기본 임시 폴더 접근이 실패해, 프로젝트 안에 임시 경로를 지정하고 제한 밖에서 검증했습니다.
- 전용 Playwright: 한영 QR 저장·인쇄 안내와 v3.4.4 표시, QR 이미지 로드, 생성 안내문과 운영자 모달의 인쇄 호출을 확인했습니다. A4·Letter 출력은 각각 1쪽이며 QR을 독립 디코딩해 저장 URL과 일치했습니다. 기존 도움말 표·툴팁의 좁은 화면 가로 넘침은 남겨 두었습니다.
- PDF: 맑은 고딕·굵은 글꼴 내장, 본문 13.5pt 검정, 6쪽을 렌더링해 확인했습니다. 사용자가 열어 둔 이전 PDF와 `_test.pdf`는 보존했습니다. 로컬 새 사본은 `/docs/한인교회 통역 시스템 처음 셋업 (SKC-translate)_맑은고딕.pdf`이고 ZIP 안에서는 접미사 없는 이름을 사용합니다.
- 번역 EXE: 모듈 17개·자산 25개 일치, 모노·스테레오 및 강제 종료 MP3 디코딩 통과. 수정하지 않은 기존 설치 설정의 복사본을 사용했습니다. 관리자·로고 HTTP 200, 준비 6.097초, 정상 종료와 별도 CHANGELOG 재생성 없음도 확인했습니다.
- 설정 EXE: 테스트한 소스·QR 템플릿·Tk 런타임과 일치합니다. 네이티브 시각 검사 도구 연결이 안 되어 실제 설정 창을 눈으로 확인한 것으로 보고하지 않습니다. 유료 API·실제 마이크·외부 설치 변경은 없었습니다.

ZIP은 121,008,738바이트, SHA-256 `a60391de8f0663c209389fdb7a7481267d48c2a89deea9ccdfd77066ec854c3e`입니다. 번역 EXE의 SHA-256은 `8dc38d91355518fd457717fda17a7c5bc71c6e5add74564a081a9e274cbaab1e`, 설정 EXE는 `de8aec22411a62225c6d63d6af67c5da63f9790e004ac8712bd13a7dabf14de5`입니다. ZIP CRC·허용 목록·해시·실제 개인 키 값 부재와 두 EXE의 영문 변경 기록 일치를 확인했습니다. 증거는 `.agent/archive/2026-11-09-v3.4.4-release/`에 있습니다.

당시 PDF는 ReportLab으로 만들었으며, 현재 재생성 방법은 위 문서 수정 절을 따릅니다. 패키저는 이미 만든 PDF를 복사합니다.

아래 절은 이전 패키지 기록입니다.

## 2026-11-09 v3.4.3: 선택적 고급 접속 설정

최신 패키지는 `.agent/dist/SKC_live_translation_v3.4.3_20261109.zip`입니다. 설정 마법사의 `3. PUBLIC HTTPS (Advanced)`는 처음에 접혀 있으며, 현장 Wi-Fi만 사용하는 경우 펼칠 필요가 없습니다. 공용 URL과 터널 정보가 없으면 하단의 Cloudflare 배지도 숨깁니다. 입력한 설정과 실제 연결 시험 통과를 구분해 표시하며, 준비상태 갱신만으로 Windows 서비스를 반복 조회하지 않습니다.

새 설치용 기본값과 템플릿은 `enable_tunnel: false`, 빈 `public_url`을 사용합니다. 기존 설정 파일은 바꾸지 않으며, 접힌 상태에서 다른 설정을 저장해도 변경하지 않은 공용 URL 때문에 터널을 다시 켜지 않습니다. `config/name_corrections.yaml`은 확정 자막과 전사에 사용하므로 배포에 유지했습니다.

실제 Tk 위젯·기본 설정·기존 설정 보존·교정·로컬 모드·ZIP 관련 **23개 검사 통과**를 확인했습니다. 그중 1개는 제한 환경의 Tcl 파일 읽기 실패를 확인한 뒤 제한 밖에서 통과했습니다. 한국어·영어 도움말은 전용 Playwright 브라우저에서 확인했고 브라우저와 임시 서버를 종료했습니다. 네이티브 UI 도구는 `Computer Use native pipe is unavailable (os error 2)`로 연결되지 않아 설정 창의 시각적 배치는 검증하지 못했습니다. 설정 EXE는 테스트한 소스·템플릿·영문 변경 기록 및 Tk 런타임의 포함을 확인했으며, 이번 버전의 실제 창을 눈으로 확인했다고 보고하지 않습니다.

번역 EXE의 앱 모듈 17개·자산 24개 일치, 모노·스테레오 MP3와 강제 종료본 디코딩, 관리자·로고 HTTP 200, 준비 6.122초와 정상 종료를 확인했습니다. 유료 API와 실제 마이크는 사용하지 않았습니다. 두 EXE·영문 변경 기록·한국어 설치 안내 등 ZIP 14개 파일의 CRC·해시·실제 개인 키 값 부재도 확인했습니다. `cloudflared.exe`와 실제 `.env`·활성 `config.yaml`은 넣지 않았습니다.

- ZIP: 119,791,207바이트, SHA-256 `35501f93dd88078d6fff8c2b98c4ccf277874322f1b8df46de8562003e110bad`
- 번역 EXE: SHA-256 `c4163ce9785a6ae7ebcd94dabda86122787c4cc01e676cf087ca5ce47950caf6`
- 설정 EXE: SHA-256 `3762bd9b34cb7fb14d45936e3e5345611bf64cf1db13c061ee6639af455bc971`

검증과 패키지 명세는 `.agent/archive/2026-11-09-v3.4.3-release/`에 있습니다. 이전 배포본을 보존했으며 교회 설치 폴더는 수정하지 않았습니다. 아래 절은 이전 전체 패키지 기록입니다.

## 2026-11-09 v3.4.2 전체 교체 패키지

최신 배포 파일은 `.agent/dist/SKC_live_translation_v3.4.2_20261109.zip`입니다. `SKC_translation.exe`와 `SKC_setup.exe`를 모두 포함하는 14개 파일 구성입니다. `cloudflared.exe`, `.env*`, 활성 `config.yaml`, 로그·녹음·평가 자료는 넣지 않았습니다. 두 EXE에 내장한 `CHANGELOG.md`도 영문 원본과 일치합니다.

| 검증 | 결과 |
|---|---|
| 패키징 회귀 테스트 | 3개 통과: 바이너리 제외, 설정 마법사 누락 거부, 번역 EXE 누락 거부 |
| 설정 마법사 EXE | 실제 기본 창 생성 3.062초, 정상 종료, 설정·키 파일 생성이나 변경 없음 |
| 번역 EXE | 앱 모듈 17개·자산 24개 일치, 모노·스테레오 MP3와 강제 종료본 디코딩 통과 |
| 서버 시작 | 관리자·로고 HTTP 200, 준비 6.126초, 정상 종료 |
| ZIP | CRC·14개 파일 허용 목록·해시·실제 개인 키 값 부재 확인 |

번역 EXE는 75,877,315바이트, SHA-256 `0b4679504abb34cd394eee46a21c473ac41f419907a52a153aa968cba8bfd25e`입니다. 설정 EXE는 44,685,636바이트, SHA-256 `1bf45bd6731063c30d5c9ff63aaca5e49113b60c643414285dcf1cff774940da`입니다. ZIP은 119,788,813바이트, SHA-256 `a1f61016abf6fc0cba6e4ec4e396d4a01c613009e1e659a4d26a8f9ceab42420`입니다.

이 패키지는 B1과 두 로그 억제 수정을 포함합니다. 이번에는 유료 API나 실제 마이크를 사용하지 않았으며, 앞선 전체 예배 재생을 반복하지 않았습니다. 설정 마법사의 다운로드를 실제로 수행한 시험은 아니며, 기존 다운로드 기능은 유지했습니다. 새 설치와 기존 폴더 교체 절차는 ZIP의 한국어 `설치_안내.txt`에 있습니다. 원본 설정 복사본·실행·빌드·ZIP 명세는 `.agent/archive/2026-11-09-v3.4.2-release/`에 보존했습니다. 외부 설치 폴더는 수정하지 않았습니다.

## 2026-11-09 후속: 두 로그의 반복 기록 억제

현재 교체 EXE는 `.agent/dist/quiet-logs/SKC_translation.exe`입니다. 앞서 제공한 `quiet-token-log/`의 수정에 더해 `startup.log`의 반복 시간 기록도 억제했습니다. 시작 중 측정은 모두 유지하고, 요약 이후에는 아직 기록하지 않은 작업의 첫 측정만 남깁니다. 요약 전에 시작해 뒤에 끝난 작업도 완료 시간을 보존합니다. 상태 조회·작업 실행과 예외 전달은 그대로이며 기존 로그를 지우지는 않습니다.

관련 테스트 **10개 통과**를 확인했습니다. 100회 반복 조회가 로그를 늘리지 않는지, 시작 전후를 걸친 작업과 최초 선택적 작업이 남는지, 예외가 유지되는지를 포함합니다. 새 EXE의 앱 모듈 17개·자산 24개 일치, 모노·스테레오 MP3, 강제 종료본 디코딩, 수정하지 않은 설치 설정의 복사본, 관리자·로고 HTTP 200과 정상 종료도 확인했습니다. 서버 준비는 6.117초였습니다. 유료 API와 실제 마이크는 사용하지 않았습니다.

최종 EXE는 **75,878,596바이트**, SHA-256 `29a823ded50d42347abd4fd561f4d5d50cd3f0d2d320888a8a7055d819ec9963`입니다. 프로그램을 완전히 종료한 뒤 기존 설치 폴더의 `SKC_translation.exe`만 교체하고 다시 실행합니다. 외부 설치 폴더는 수정하지 않았고 기존 EXE·ZIP은 보존했습니다. 기록은 `.agent/archive/2026-11-09-quiet-logs/`에 있습니다. 아래 `quiet-token-log/` 절은 먼저 완료한 빌드 기록입니다.

## 2026-11-09 후속: 선택적 토큰 파일 로그 억제

상태 조회마다 반복되던 `Token candidate ... exists but is not readable` DEBUG 메시지만 제거했습니다. 읽을 수 없는 선택적 파일은 계속 건너뛰며 환경 변수·다른 토큰 파일·서비스 탐색과 실제 터널 시작 실패 기록은 유지합니다. 파일 권한이나 보안 설정을 바꾸는 수정은 아닙니다.

기존 Cloudflare 테스트 2개를 통과한 뒤 같은 프로젝트 빌드 환경으로 번역 EXE를 재빌드했습니다. 앱 모듈 17개·자산 24개 일치, 모노·스테레오 MP3와 강제 종료본 디코딩, 수정하지 않은 설치 설정의 복사본, 관리자·로고 HTTP 200과 정상 종료를 확인했습니다. 준비 시간은 6.106초였으며 유료 API나 실제 마이크를 쓰지 않았습니다.

교체 EXE는 `.agent/dist/quiet-token-log/SKC_translation.exe`, 75,877,911바이트, SHA-256 `540682de2a10467e5da9a32fa7eea97ee3f7fa505fde30ace3128008a1b927be`입니다. 기존 v3.4.1 EXE·ZIP은 그대로 보존했습니다. 설치 프로그램을 완전히 종료한 뒤 이 EXE만 복사하고 다시 시작해야 반영됩니다. 외부 설치 폴더는 수정하지 않았습니다. 검증 기록은 `.agent/archive/2026-11-09-token-log/`에 있습니다.

## 2026-11-09 v3.4.1 B1 검증과 교체 패키지

[전체 예배 검증](/docs/b1_full_service_validation.md)을 마친 소스로 번역 EXE를 한 번 빌드했습니다. 아래는 이번 패키지의 기록이며, 그 뒤 절은 이전 빌드 당시의 기록입니다. 설치 폴더는 변경하지 않았습니다.

- 프로젝트 `.agent/build_env`의 Python 3.14.7과 SDK 2.33.0을 사용했습니다. 실행 중인 다른 프로그램을 종료하는 배치 대신 번역 spec만 별도 작업 폴더에서 빌드했고, 82.325초에 완료됐습니다.
- 최종 EXE는 **75,877,149바이트**, SHA-256 `f526a73e24413a0e2dc0323550bf38c0ca94cff8371efd92b9e89cf6678ae544`입니다. 네이티브 `lameenc.cp314-win_amd64.pyd`와 검증 소스의 앱 모듈 17개·자산 24개 일치를 확인했습니다.
- 기존 설치 `config.yaml`의 수정하지 않은 복사본을 사용했습니다. SHA-256은 `c760b5c1a7c1f626606cea24a8e398c89fb3d59d057e04b6b49b85bae3068834`입니다. 제거된 옛 설정을 복구하거나 파일을 새 형식으로 바꿀 필요는 없었습니다.

| 최종 EXE 검사 | 결과 |
|---|---|
| 16 kHz 모노 MP3, 정상 종료 | 6.084초, 97,344바이트, 128 kbps |
| 48 kHz 스테레오 MP3, 정상 종료 | 8.040초, 128,640바이트, 양쪽 채널 신호 |
| 16 kHz 모노 MP3, 강제 종료 | 남은 2.360초, 37,761바이트 디코딩 성공 |
| 최초 실제 저장 로그 | 각 녹음에 `MP3 backup writing:` 1회 |
| 배치 시작과 종료 | 관리자·로고 HTTP 200, 준비 6.091초, 정상 종료 |
| 자동 시작 요약 | 5.513초, 가장 긴 차단 단계는 포트 확인 2.025초 |
| 도움말 | 전용 Playwright 브라우저에서 한국어·영어의 문맥 유지·초기화 안내 확인 |

녹음 시험은 합성 PCM을 사용했습니다. 시작 시험은 터널·mDNS·기본 브라우저를 끄고 시험 키 문자열을 사용했습니다. 이 바이너리 시험에는 실제 마이크나 유료 API 호출이 없었습니다. 실제 속도 API 재생은 위 전체 예배 보고서에 구분해 기록했습니다. 회귀 테스트는 새 재개 동작과 기존 기능을 포함해 **161개 통과**했습니다. 제한 환경의 네이티브 DLL 접근 거부가 난 MP3 7개는 원인을 확인한 뒤 제한 밖에서 통과했습니다.

교체 파일은 `.agent/dist/SKC_live_translation_v3.4.1_20261109.zip`입니다. 크기는 **94,319,036바이트**, SHA-256은 `33da79421f4793bf64051e286857f6b9862752809de80d449b2ccdf743ec864c`입니다. 기존과 같은 실행 전용 14개 파일 구성으로 한국어 `설치_안내.txt`를 포함했습니다. ZIP CRC와 허용 목록·파일 해시·실제 개인 키 값 부재를 확인했습니다. `.env`, `.env.example`, 활성 `config.yaml`, 설정 마법사, 로그·녹음·평가 자료는 넣지 않았습니다. `cloudflared.exe`는 기존 2026.10.4와 같습니다.

기존 폴더를 보존하고 새 폴더에 압축을 푼 뒤 `.env`, `config.yaml`, `branding/`을 복사해 `SKC_start.bat`을 실행합니다. 이전·새 프로그램을 동시에 실행하지 않습니다. 새 프로그램에 문제가 있으면 먼저 종료한 뒤 이전 폴더로 돌아갑니다. 이전의 녹음 검증 EXE는 `.agent/archive/2026-11-09-b1-full-service/baseline-v3.4.0/`에 보존했습니다. 오래된 v3.4.0 ZIP은 이 인코더 수정 EXE와 다른 파일이므로 혼동하지 않습니다.

원시 검증과 패키지 명세는 `.agent/archive/2026-11-09-b1-full-service/`의 `verification.json`, `startup.json`, `source_integrity.json`, `package_manifest.json`에 있습니다. 설치 후 별도 진단 실행은 필요하지 않으며 평소 시작 로그와 예배 요약으로 관찰합니다. 찬양의 오인식이 모두 해결된 것은 아니고 송영의 일본어 오류는 남았습니다.

## 2026-11-09 실제 배포본의 MP3 인코더 누락 수정

`20261109_001608/session.json`과 같은 실행의 `ops.log`에는 `encoder_unavailable`, `ModuleNotFoundError: No module named 'lameenc'`가 기록됐습니다. 이 세션은 MP3를 저장하지 못했습니다. 정상 저장 위치는 세션 전사 폴더와 별개인 `<설치 폴더>/logs/recordings/current_week/`입니다.

원인은 빌드 환경 선택이었습니다. `build_exe.bat`은 `skc_build`가 없으면 공유 `agent` 환경을 사용했고, 이 환경에는 `lameenc`가 없었습니다. 설치 EXE와 당시 `/.agent/dist/`의 EXE는 같은 SHA-256이었으며, 인코더 라이선스만 있고 실제 `.pyd`는 없었습니다. PyInstaller의 `hiddenimports` 선언만으로 누락 패키지가 설치되거나 빌드가 실패하지는 않았습니다.

- `build_exe.bat`은 이제 프로젝트의 `.agent/build_env`만 활성화합니다. 환경이 없으면 중단하며 공유 환경으로 대신 빌드하지 않습니다. `build_parallel.py`는 사용한 Python 경로도 출력합니다.
- 번역 스펙은 의존성 분석 전에 인코더를 불러와 실제 MP3 바이트 생성을 검사합니다. 인코더가 없는 `agent`에서 빌드가 종료 코드 1로 거부되는 것을 확인했습니다.
- 일반 시작 시험에서 발견한 `_ssl` DLL 오류도 수정했습니다. Conda 기반 venv를 분석할 때 다른 설치의 `mingw64` DLL이 선택되지 않도록 두 스펙의 OpenSSL·decimal DLL은 `sys.base_prefix/Library/bin`에서 수집합니다. 이번에 실제로 재빌드·실행 검증한 파일은 번역 EXE입니다.

기존 환경이 준비된 이 프로젝트에서는 평소처럼 `build_exe.bat`을 실행합니다. 로그의 `Build Python`이 `.agent/build_env/Scripts/python.exe`인지 확인합니다. 새 PC에서 환경을 준비할 때도 `requirements.txt`의 `lameenc==1.9.0`을 빼지 않습니다. 배포 PC에는 Python 패키지를 따로 설치하지 않습니다.

검증한 EXE는 `.agent/dist/SKC_translation.exe`에 있습니다. 크기 **75,874,956바이트**, SHA-256 `cd90e0f588c395f2b84718fdcc869c17acdf94111a0ab44026adc813096fba2c`입니다. 실제 `lameenc.cp314-win_amd64.pyd` 포함과 빌드 시점의 앱 코드·화면 자산 등 42개 일치를 확인했습니다. 기록용 문서는 이 검증 뒤 갱신했습니다.

| 최종 EXE의 실제 MP3 저장·디코딩 검사 | 결과 |
|---|---|
| 16 kHz 모노, 정상 종료 | 6.084초, 97,344바이트, 128 kbps |
| 48 kHz 스테레오, 정상 종료 | 8.040초, 128,640바이트, 128 kbps, 양쪽 채널 신호 확인 |
| 16 kHz 모노, 프로세스 강제 종료 | 남은 2.360초, 37,761바이트 디코딩 성공 |
| 최초 실제 쓰기 로그 | 각 녹음에 `MP3 backup writing:` 1회 |
| 일반 배치 시작·서버 종료 | 관리자·로고 HTTP 200, 준비 6.714초, 정상 종료 |

녹음 시험은 수정하지 않은 설치 설정의 복사본과 합성 PCM을 사용했습니다. 일반 시작은 별도의 안전한 복사본에서 터널·mDNS·기본 브라우저를 끄고 시험 키 문자열을 사용했습니다. 실제 마이크·유료 API를 사용하지 않았고, 교회 수신기의 물리적 동작을 대신 검증한 결과는 아닙니다. 앱 소스가 바뀌지 않아 직전 154개 통과한 전체 테스트는 반복하지 않았습니다.

새 ZIP은 만들지 않았습니다. 이전 EXE와 오래된 v3.4.0 ZIP은 `.agent/archive/2026-11-09-mp3-build/before-fix/`에 보관했습니다. 현재 설치 폴더는 변경하지 않았으므로 프로그램 종료 후 검증한 EXE를 설치 폴더에 복사해야 반영됩니다. 설정·키·기존 녹음은 유지합니다. 자세한 증거는 같은 아카이브의 `inspection.json`, `preflight.json`, `verification.json`, `startup.json`, `workspace-result.json`에 있습니다.

## 2026-11-08 MP3 인코더 접근 거부의 해석과 대응

첫 실패는 에이전트의 제한된 실행 환경에서 프로젝트 내부 `lameenc` 확장 모듈을 읽을 때 발생했습니다. 원래 오류는 `ImportError: DLL load failed while importing lameenc: Access is denied.`였습니다. 제한을 벗어난 동일 소스의 전체 테스트와 실제 EXE 녹음은 통과했습니다. 당시 23:10~23:25(미국 중부 시각)의 Defender·CodeIntegrity 관련 차단 이벤트도 조회되지 않았습니다. 다른 보안 제품이나 정책까지 배제한 것은 아니지만, **백신 차단을 확인한 사례는 아닙니다.** 이 오류와 교회 PC의 느린 시작이 같은 원인이라고 판단할 근거도 없습니다.

단일 EXE는 네이티브 확장 모듈을 임시 `_MEI...` 폴더에 풀어 실행합니다. 따라서 실제 PC의 임시 폴더 실행 정책이나 보안 제품이 영향을 줄 가능성은 있습니다. 현재 빌드는 UPX를 사용하지 않습니다. 한 폴더 배포는 임시 추출 경로 문제를 검토할 대안이지만 차단·시작 속도 해결을 보장하지 않으며, 이번에 배포 방식을 바꾸지는 않았습니다. [PyInstaller 실행 방식](https://pyinstaller.org/en/stable/operating-mode.html).

인코더는 서버 시작 중이 아니라 첫 녹음 데이터를 처리하는 녹음 스레드에서 불러옵니다. 실패하면 `error_kind`, 원래 예외 종류·내용과 폴더를 상태·`logs/ops.log`·운영자 이벤트의 펼칠 수 있는 상세에 보존합니다. 경고는 인코더 로드 실패, 파일 저장 실패와 인코딩 실패를 구분해 한영으로 표시합니다. 이미 기록된 부분은 남을 수 있지만, 이후 백업은 없거나 불완전하며 번역은 계속됩니다. 파일 정리 중 추가 오류가 나도 최초 원인을 덮어쓰지 않습니다.

다음 평상시 실행에서 `logs/ops.log`의 **`MP3 backup writing:`**은 인코더를 만들었다는 추정이 아니라 실제 MP3 바이트 쓰기가 성공했다는 기록입니다. 녹음당 한 번만 남기며 경로·샘플레이트·채널·비트레이트를 포함합니다. 별도 진단 실행은 필요하지 않습니다.

녹음 경고가 나오면 `logs/ops.log`의 원래 오류와 `/api/status`의 `audio.recording` 상태를 확인합니다. 인코더 로드 실패와 녹음 폴더 쓰기 실패를 구분해야 합니다. Windows의 실제 차단 여부는 **Windows 보안 > 바이러스 및 위협 방지 > 보호 기록**에서 같은 시각의 앱·파일 경로와 대조합니다. 보호 기록은 보통 2주간 유지됩니다. [Microsoft 보호 기록 안내](https://support.microsoft.com/en-us/windows/security/windows-security/protection-history-in-the-windows-security-app).

- DLL 로드가 거부됐다면 해당 인코더 경로와 보안 이벤트를 확인합니다. 백신의 오탐으로 확인된 경우에만 신뢰할 수 있는 빌드인지 검증하고 해당 탐지의 허용·재심사 절차를 검토합니다.
- 녹음 폴더 쓰기가 거부됐다면 폴더 권한·디스크 공간과 제어된 폴더 액세스의 해당 앱 차단 여부를 확인합니다. DLL 로드 문제와는 별개입니다. [Microsoft 폴더 접근 안내](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/virus-and-threat-protection-in-the-windows-security-app).
- 백신을 끄거나 임시 폴더 전체를 예외로 만들지 않습니다. 에이전트의 제한을 벗어나 테스트한 사실은 교회에서 관리자 권한으로 앱을 실행하라는 뜻이 아닙니다.
- 교회 PC의 시작 지연은 평상시 `logs/startup.log`로 별도 판단합니다. 보호 기록에 같은 시각의 차단이 있는 경우에만 함께 대조하며, 오류 메시지만으로 백신 원인을 확정하지 않습니다.

아래 EXE·ZIP 검증은 이 진단 보완 전 빌드의 기록입니다. 보완 코드는 다음 평소 빌드에 반영하며, 별도 진단 실행·새 ZIP·보안 설정 변경은 추가하지 않습니다.

진단 보완 후 소스의 전체 테스트는 **153개 통과**, 기존 SDK deprecation 경고 1건, 15.82초였습니다. 모의 DLL 접근 거부·인코더 누락, 저장·마무리 오류, 최초 원인 보존, 실제 MP3 쓰기 로그와 기존 녹음 동작을 확인했습니다.

## 2026-11-08 추가 요청: 상시 시작 기록과 교체 패키지

`SKC_start.bat`으로 실행할 때마다 `logs/startup.log`에 날짜·시각과 단계별 경과 시간을 남깁니다. 브라우저 실행 요청을 마치면 `startup_summary` 한 줄에 전체 경과 시간과 가장 오래 걸린 단계를 적습니다. Python에 들어오기 전 시간도 `before_python`으로 비교하므로 EXE 압축 해제 등을 포함한 구간이 요약에서 빠지지 않습니다. 브라우저 실행 요청이 수락됐다는 기록은 화면이 다 그려졌다는 뜻이 아닙니다. 실행이 중간에 실패하면 종료 시 그 상태를 요약합니다.

현재 로그가 1 MiB에 이르면 `startup.log.1`로 교체하며 이전 백업은 덮어씁니다. 최신 로그와 백업 한 개만 유지합니다. 서버가 포트를 연 뒤 시작한 선택적 네트워크 작업은 `background: true`, 시작 요약 이후의 기록은 `after_startup: true`로 구분하며 시작을 막은 가장 느린 단계로 집계하지 않습니다. 화면이나 설정 항목은 추가하지 않았습니다.

이전 설정 호환성은 당시 `.agent/dist/config.yaml`의 수정하지 않은 복사본으로 검증했습니다. 2026-11-03 배포 설정이며 현재 `.agent/dist`의 새 설정을 뜻하지 않습니다. 보존한 복사본은 `.agent/archive/2026-11-08-worship-review/single_operator_build/legacy_config_compatibility/config.yaml`이며 SHA-256은 `69a6afd5975b944c087116422219a873abc596e574b701ffc91ea62799686370`입니다. 설정 로더와 언어 검증을 통과했고 파일도 바뀌지 않았습니다. 제거된 `preferred_model`, `fallback_model`, `context_seed`, `expected_source_language`는 무시하며 고정 모델과 `ko/en` 기본값을 사용합니다. 이는 보존된 배포 설정의 호환성 검증이며, 교회 노트북에서 이후 수정한 파일이나 실제 음향 장치를 확인한 결과는 아닙니다. 근거는 같은 보관 폴더의 `legacy_config_compatibility.json`에 있습니다.

아래 진단 ZIP과 측정은 추가 요청 전 기록입니다. 새 교체 패키지에는 실제 `.env`뿐 아니라 `.env.example`도 넣지 않습니다. 기존 `cloudflared 2026.10.4` 실행 파일과 같은 버전의 Apache 2.0 고지를 포함하며, 오래된 설정 마법사 실행 파일은 넣지 않습니다.

### 교체 패키지 최종 검증

소스와 화면 자산을 고정한 뒤 새 작업 폴더에서 단일 EXE를 한 번 빌드했습니다. 빌드는 106.9초에 완료됐으며, EXE 내부의 앱 코드·템플릿·JavaScript·설정·고지 등 42개 파일이 현재 소스와 모두 일치했습니다. 시작 기록 테스트 6개와 전체 테스트 150개가 통과했습니다. 이번 추가 요청에서는 한 폴더 빌드를 다시 만들지 않았습니다.

| 개발 PC에서 측정한 새 EXE | 결과 |
|---|---:|
| 배치 기록부터 Python 진입 | 1.867초 |
| 앱 임포트 | 2.294초 |
| 로컬 포트 사용 여부 확인 | 2.018초 |
| 배치 기록부터 서버 포트 준비 | 6.231초 |
| 배치 기록부터 브라우저 실행 요청 수락 | 6.382초 |
| 런처 프로세스 실행부터 `/admin` HTTP 200 확인 | 6.707초 |

격리 폴더에서 터널과 mDNS를 끄고 테스트 키 문자열을 사용했습니다. 실제 브라우저 대신 테스트 명령에 실행 요청을 보냈습니다. 관리자 페이지와 로고는 HTTP 200이었고 정상 종료 후 테스트 서버는 남기지 않았습니다. 요약은 한 번만 기록됐습니다.

```text
Startup 2026-11-08 23:22:02: browser launch requested after 6.4s (slowest phase application_imports 2.3s)
```

실행 전에 약 1 MiB의 이전 기록을 넣어 배치 파일부터 로그 교체를 검증했습니다. 실행 후 `startup.log`는 3,054바이트, `startup.log.1`은 1,048,554바이트였으며 백업은 한 개였습니다. 이 측정은 개발 PC 한 번의 실행 결과이며 교회 노트북의 지연 원인을 확정하지 않습니다.

실제 EXE의 `--verify-recording` 경로에서 정상 녹음은 수정하지 않은 이전 배포 설정을 사용했습니다. 설정 SHA-256은 실행 전후 같았습니다. 정상 MP3는 8.040초, 강제 종료 MP3는 3.638초였고 둘 다 48 kHz 스테레오 128 kbps로 디코딩됐습니다. 마이크나 유료 API는 사용하지 않았습니다. 이 PC에는 `C:/ProgramData/cloudflared/token`이 존재하므로 이전 설정의 일반 서버 경로를 그대로 실행하면 실제 터널을 열 수 있습니다. 이를 피하려고 이전 설정의 EXE 검증은 녹음 경로에서 수행하고 일반 서버 실행은 안전한 격리 설정으로 검증했습니다.

해당 ZIP의 보관 경로는 `.agent/archive/2026-11-08-worship-review/addendum2_release/SKC_live_translation_v3.4.0_20261108.zip`입니다. 당시 교체용 산출물이며 현재 소스를 대신하지 않습니다. 크기는 **94,382,581바이트**, SHA-256은 `b93821a1c6dcf466153ae4ce5c786434f04cb79809a04defa98b02c33d640e8f`입니다. EXE는 **75,941,241바이트**, SHA-256은 `ef4030f05cd5238f7fa0bd53ec1c4925b06427797599560a978d3fe56f6089a5`입니다.

ZIP의 CRC, 필수 파일 목록, `.env*` 파일과 활성 `config.yaml` 부재, 기존 비밀 값의 미포함을 검사했습니다. `cloudflared.exe`는 보존된 2026.10.4 파일과 SHA-256이 같습니다. `설치_안내.txt`는 기존 폴더를 복구용으로 보관하고 새 폴더에 압축을 푼 뒤 기존 `.env`, `config.yaml`, `branding/`만 복사하도록 안내합니다. 복구할 때는 새 프로그램과 콘솔을 먼저 종료하도록 적었습니다. 교회 설치 폴더에 배포하지 않았습니다.

근거는 `.agent/archive/2026-11-08-worship-review/addendum2_release/verification.json`, `embedded_check.json`, `package_manifest.json`에 보관합니다. 당시에는 소스를 고정하고 빌드·실행을 검증했습니다. 이후 진단·UI 소스 수정이 있으므로 이 기록을 현재 소스 전체의 EXE 검증으로 해석하지 않습니다.

## 2026-11-08 시작 지연 진단과 MP3 녹음 검증

저장소에서 확인한 기존 `SKC_start.bat`은 Conda를 활성화한 뒤 `python main.py`를 실행했습니다. EXE를 실행하지 않았으므로 개발 PC의 약 5초와 교회 노트북의 1~2분이 같은 빌드에서 측정된 값인지는 확인되지 않았습니다. 기존 배치 파일에는 정상 경로의 대기나 폴링이 없었고, 오류 때의 `pause`와 Conda 활성화만 있었습니다. Python 쪽에서는 앱 전체 임포트, 설정과 오디오 백엔드 초기화, 키 확인, 터널 시작, 포트 확인, 배너 출력, Uvicorn 시작이 이어졌으며 브라우저는 별도 스레드에서 무조건 2초를 기다린 뒤 열렸습니다.

현재 런처는 같은 폴더의 `SKC_translation.exe`를 우선 실행하고, EXE가 없을 때만 개발 환경을 사용합니다. 실행 전에 배치 시각을 기록합니다. `logs/startup.log`에는 실행 시작부터 지난 초와 단계별 소요 시간이 JSON 한 줄씩 저장됩니다.

| 기록 | 확인할 내용 |
|---|---|
| `batch_start` → `python_main_start` | Python 코드가 실행되기 전의 시간. 단일 EXE 압축 해제와 보안 검사 등을 조사할 구간이며, 이 값만으로 원인을 확정하지 않습니다. |
| `application_imports_*`, 설정 로드, 오디오 백엔드와 장치 열거 | 앱 임포트와 로컬 장치 초기화 시간 |
| `server_listening`, `browser_open_*`, `browser_launch_requested` | 서버가 실제로 포트를 연 시점과 브라우저 실행 요청 시간. 이전 진단 빌드는 `browser_opened`라는 이벤트 이름을 사용했습니다. |
| mDNS, 터널 서비스, 공용 HTTPS 검사 | 각 로컬 서비스 호출과 네트워크 요청의 시간 및 실패 유형 |

브라우저는 고정 2초 대기 대신 서버가 포트를 연 직후에 실행합니다. 필수가 아닌 터널과 mDNS 작업은 그 뒤에 시작합니다. 공용 링크 검사는 urllib 요청에 최대 10초, Windows curl 재시도에 최대 10초(프로세스 대기 한도 12초)를 사용합니다. 서비스 질의는 호출마다 최대 5초이고, 터널 상태 재검사는 30초 간격입니다. 이 작업들은 브라우저를 여는 선행 조건이 아닙니다.

교회 노트북에서 할 일은 한 가지입니다. **평소처럼 `SKC_start.bat`을 실행한 뒤 `logs/startup.log` 파일을 보내주세요.** 교회 노트북의 로그를 받기 전에는 압축 해제, 백신, Wi-Fi, Zeroconf, 오디오 드라이버 중 어느 것이 지연의 원인인지 확정하지 않습니다.

이번 빌드 환경은 공유 Conda 환경을 바꾸지 않고 프로젝트 내부 `.agent/build_env/`에 만들었습니다. Python 3.14.7, Google GenAI SDK 2.33.0, PyInstaller 6.22.3, lameenc 1.9.0을 사용합니다. 전체 패키지 버전은 `.agent/build_requirements_frozen.txt`에 남깁니다. PyAudio 0.2.14는 Python 3.14용 Windows 휠이 없어 기존 PortAudio 헤더와 라이브러리를 프로젝트 안으로 복사하여 컴파일했습니다. 런타임 PortAudio DLL도 함께 묶습니다.

MP3 검증에는 실제 배포 대상 EXE의 내부 명령 `--verify-recording fixture.wav`를 사용합니다. 이 경로는 100 ms 간격으로 PCM16 입력을 실제 `WeeklyRecording`에 전달하며 마이크나 Gemini API를 열지 않습니다. 정상 종료와 강제 종료 후 파일을 각각 디코딩하여 인코더 포함 여부와 이미 기록된 MP3 프레임의 재생 가능 여부를 확인합니다. 이 검증은 USB 수신기의 실제 오디오 품질을 검증한 것으로 보지 않습니다.

단일 파일과 한 폴더 빌드는 같은 스펙을 사용합니다. 비교할 때만 `SKC_BUILD_LAYOUT=onedir`를 설정합니다. `tools/` 평가 도구는 EXE에서 제외하며, MP3 인코더의 고지와 라이선스는 `licenses/`에 보관합니다. 이번 산출물은 검증용이며 배포하지 않습니다.

### 개발 PC의 첫 검증 빌드 결과

아래 값은 같은 소스와 스펙의 단일 파일 및 한 폴더 빌드를 각각 한 번 실행해 측정했습니다. 터널과 mDNS는 격리 설정에서 끄고, 실제 API 키 대신 테스트 문자열을 사용했습니다. 평소 사용하는 브라우저를 열지 않도록 실행 요청을 테스트 명령으로 보냈으므로 브라우저 행은 실행 요청 수락 시점이며 화면이 그려진 시점이 아닙니다. 캐시와 파일 검사 조건을 통제한 반복 실험이 아니므로 두 형식 중 어느 것이 항상 빠르다는 결론을 내리지 않습니다.

| 측정 항목 | 단일 EXE | 한 폴더 |
|---|---:|---:|
| 배치 기록부터 Python 진입 | 1.803초 | 0.965초 |
| 앱 임포트 | 2.034초 | 15.180초 |
| PortAudio 초기화 | 0.919초 | 0.926초 |
| 배치 기록부터 서버 포트 준비 | 5.909초 | 18.351초 |
| 배치 기록부터 브라우저 실행 요청 수락 | 6.015초 | 18.446초 |
| 런처 프로세스 실행부터 `/admin` HTTP 200 확인 | 6.703초 | 19.456초 |

한 폴더 실행에서는 Python 진입 후 PortAudio 초기화 전의 임포트 구간이 길었습니다. 단일 EXE 압축 해제가 교회 노트북 지연의 원인이라는 가설은 이 결과로 확정할 수 없습니다. 교회 노트북의 실제 로그가 필요합니다. 두 실행 모두 관리자 페이지와 로고가 HTTP 200으로 응답했으며 종료 후 테스트 서버는 남기지 않았습니다.

첫 검증 빌드의 단일 EXE 크기는 75,933,248바이트(약 72.4 MiB)입니다. 48 kHz, 2채널 PCM16 fixture를 실제 EXE의 녹음 경로에 넣은 결과는 다음과 같습니다.

| 종료 방식 | MP3 길이 | 파일 크기 | 디코딩 결과 |
|---|---:|---:|---|
| 정상 종료 | 8.040초 | 128,640바이트 | 성공, 두 채널에서 신호 확인 |
| 녹음 도중 프로세스 트리 강제 종료 | 3.638초 | 58,205바이트 | 저장된 프레임 디코딩 성공, 두 채널에서 신호 확인 |

두 파일 모두 MP3, 48 kHz, 스테레오, 128,000 bps로 확인했습니다. 외부 FFmpeg는 검증 시 디코더로만 사용했고 EXE에는 포함하지 않았습니다. 원본 검증 결과와 SHA-256은 `.agent/archive/2026-11-08-worship-review/single_operator_build/verification.json`에 있습니다. 강제 종료 파일의 메타데이터는 마지막 정상 종료 시점으로 정리되지 않았으므로 오디오 복구 가능성과 구분해야 합니다.

### 최종 산출물의 소스 일치 확인

빌드 도중 설정 함수가 삭제되었을 때 PyInstaller가 이전 PYZ 캐시를 재사용하여, 분석을 다시 해도 삭제한 함수가 EXE 안에 남는 사례를 확인했습니다. 최종 빌드는 소스를 고정한 뒤 새 작업 폴더에서 만들었으며, EXE 내부의 앱 코드와 템플릿·JavaScript·설정·가이드·변경 기록 등 41개 파일을 현재 소스와 비교해 모두 일치함을 확인했습니다. 빌드 성공 메시지만으로 소스가 최신이라고 판단하지 않습니다.

당시 진단 패키지에는 실제 `.env`, 토큰, 로그, 예배 오디오와 평가 데이터를 넣지 않았습니다. 개발용 설정은 `config.example.yaml`이라는 예제 이름으로만 제공했고 기존 `.env`, `config.yaml`, `branding/`을 유지하도록 안내했습니다. 압축 해제된 중복 폴더와 중간 EXE는 정리하고 ZIP·검증 로그·MP3·설정 복사본을 보관했습니다. 최종 검증은 `verification_final.json`, 패키지 목록과 해시는 `package_manifest.json`에 남아 있습니다.

최종 빌드도 정상 종료와 강제 종료 MP3 검증을 통과했습니다. 정상 파일은 8.040초, 강제 종료 파일은 3.638초이며 둘 다 48 kHz 스테레오 128 kbps로 디코딩되었습니다. 최종 EXE는 75,931,346바이트이고 SHA-256은 `b010e313a2dca24e0c36cb96466a54d7e59c84dfb469ebb1f590137fca5a9965`입니다.

| 최종 빌드 측정 | 단일 EXE | 한 폴더 |
|---|---:|---:|
| 배치 기록부터 Python 진입 | 1.826초 | 0.959초 |
| 앱 임포트 | 1.998초 | 15.453초 |
| 로컬 포트 사용 여부 확인 | 2.021초 | 2.029초 |
| 배치 기록부터 서버 포트 준비 | 5.882초 | 18.593초 |
| 배치 기록부터 브라우저 실행 요청 수락 | 5.988초 | 18.691초 |
| 런처 프로세스 실행부터 `/admin` HTTP 200 확인 | 7.226초 | 19.417초 |

포트 확인이 개발 PC에서 약 2초를 차지한다는 것을 추가로 측정했습니다. 이 값만으로 교회 노트북의 1~2분 지연을 설명할 수 없으므로 포트 검사나 빌드 형식의 추가 변경은 하지 않았습니다. ZIP은 `.agent/archive/2026-11-08-worship-review/single_operator_build/SKC_single_operator_diagnostic_20261108.zip`이며 크기는 75,533,982바이트, SHA-256은 `380e6adf9da1c886fc465a9b9e35fe7a16853e8a90f48bea6dc7c78ca843b1be`입니다. 교회 설치 폴더에 복사하거나 실행하지 않았습니다.

---

## 📌 목차
1. [목표](#1-목표)
2. [시도 1 — 3 GB exe (실패)](#2-시도-1--3-gb-exe-실패)
3. [시도 2 — scipy 누락 (실패)](#3-시도-2--scipy-누락-실패)
4. [시도 3 — unittest 누락 (실패)](#4-시도-3--unittest-누락-실패)
5. [시도 4 — uvicorn 모듈 임포트 실패 (실패)](#5-시도-4--uvicorn-모듈-임포트-실패-실패)
6. [시도 5 — config.yaml 없음 (테스트 실수)](#6-시도-5--configyaml-없음-테스트-실수)
7. [시도 6 — 완전 성공 ✅](#7-시도-6--완전-성공-)
8. [frozen exe 호환을 위한 코드 변경 사항](#8-frozen-exe-호환을-위한-코드-변경-사항)
9. [PyInstaller 스펙 주요 결정](#9-pyinstaller-스펙-주요-결정)
10. [재빌드 방법](#10-재빌드-방법)
11. [skc_build 환경 필수 패키지](#11-skc_build-환경-필수-패키지)

---

## 1. 목표

Python 서버(`main.py` + `app/`)를 **단일 `.exe`** 로 패키징하여, 자원봉사자가 Python·conda·터미널 설정 없이 더블클릭으로 실행할 수 있게 한다.

**배포 대상: 한 폴더에 파일 3개**
- `SKC_translation.exe` — 바이너리 (~70 MB)
- `config.yaml` — 수정 가능: `device_index`, `port`, `model`
- `.env` — 수정 가능: `GEMINI_API_KEY=...`

도구: **PyInstaller** (`pyinstaller SKC_translation.spec`)

---

## 2. 시도 1 — 3 GB exe (실패)

**명령어:**
```bat
conda run -n agent pyinstaller SKC_translation.spec --onefile
```

**결과:** exe 크기 약 3 GB. 기술적으로 작동하지만 배포 불가.

**원인:** `agent` 환경에는 CUDA 지원 PyTorch (~2.5 GB)가 포함되어 있다. PyInstaller는 활성 환경에서 찾을 수 있는 모든 패키지를 번들에 포함하며, 앱이 실제로 임포트하지 않는 패키지도 포함한다.

**해결:** 앱에 실제로 필요한 패키지만 담은 최소 빌드 환경을 별도로 생성.

```bat
conda create -n skc_build python=3.11 --yes
conda run -n skc_build pip install google-genai fastapi "uvicorn[standard]" pyaudio numpy ^
    python-dotenv pyyaml "qrcode[pil]" Pillow sse-starlette pyinstaller
```

---

## 3. 시도 2 — scipy 누락 (실패)

**결과:** 빌드 성공 (~40 MB). 그러나 실행 시:
```
ModuleNotFoundError: No module named 'scipy'
```

**원인:** `app/audio.py`가 리샘플러의 버터워스 안티앨리어싱 필터에 `from scipy import signal`을 사용한다. `scipy`가 초기 `skc_build` 설치 목록에 없었고, 스펙의 `excludes` 목록에도 명시적으로 포함되어 있어 이중으로 제외되었다.

**해결:**
```bat
conda run -n skc_build pip install scipy
```
`SKC_translation.spec`에서:
- `excludes`에서 `"scipy"` 제거
- `hiddenimports`에 추가: `"scipy"`, `"scipy.signal"`, `"scipy.signal._upfirdn"`, `"scipy.signal._upfirdn_apply"`

---

## 4. 시도 3 — unittest 누락 (실패)

**결과:** 빌드 ~70 MB. 실행 시:
```
ModuleNotFoundError: No module named 'unittest'
```

**원인:** 스펙 `excludes`에 `"test"`, `"unittest"`가 있었다. 안전해 보였지만 scipy가 이를 전이적으로 요구한다:

```
scipy.signal → scipy._lib._array_api → scipy._lib.array_api_compat.numpy
    → numpy.testing → unittest
```

**해결:** 스펙 `excludes`에서 `"test"`와 `"unittest"` 제거.

---

## 5. 시도 4 — uvicorn 모듈 임포트 실패 (실패)

**결과:** 서버 시작, API 키 확인, 용어집 로드 완료 — 그 후:
```
ERROR: Error loading ASGI app. Could not import module "main"
```

**원인:** `main.py`가 원래 문자열 기반 임포트를 사용했다:
```python
uvicorn.run("main:app", ...)
```
일반 Python 환경에서는 uvicorn이 `sys.path`에서 `main` 모듈을 이름으로 임포트한다. PyInstaller frozen exe 안에서는 디스크에 `main.py`가 없고 모든 것이 바이너리 내부에 번들되어 있어 문자열 `"main:app"`이 실패한다.

**해결:** 문자열 대신 앱 객체를 직접 전달:
```python
uvicorn.run(app, ...)  # frozen·일반 환경 모두 동작
```

---

## 6. 시도 5 — config.yaml 없음 (테스트 실수)

**결과:** uvicorn이 포트에 바인드했지만:
```
FileNotFoundError: No such file or directory: '.../dist/config.yaml'
```

**원인:** `app/config.py`에는 이미 `sys.frozen` 체크가 있어 exe 옆에서 `config.yaml`을 찾도록 되어 있었다. 하지만 테스트 시 `dist/`에 `config.yaml`을 복사하지 않은 것이 문제였다. 코드 버그가 아닌 테스트 설정 실수.

**해결:** exe와 같은 폴더에 `config.yaml`과 `.env`를 복사한 후 실행.

---

## 7. 시도 6 — 서버 작동, 로고 누락

**결과:** 서버 시작, 브라우저 자동 열림, 운영자 UI HTTP 200 응답. 그러나 헤더의 PCA 로고가 표시되지 않음 (`/logo.webp` HTTP 404).

**원인:** `app/server.py`는 `Path(__file__).parent / "pca-logo-white-small.webp"`로 로고를 찾는다. frozen exe 내부에서 `__file__`은 올바르게 PyInstaller 임시 압축 해제 폴더(`_MEIPASS/app/server.py`)를 가리키므로 경로 자체는 맞다. 문제는 `pca-logo-white-small.webp` 파일이 스펙의 `datas`에 포함되지 않아 번들 안에 없었던 것.

**해결:** `SKC_translation.spec`의 `datas`에 한 줄 추가:
```python
datas += [("app/pca-logo-white-small.webp", "app")]
```

---

## 8. 시도 7 — 완전 성공 ✅

**결과:** 서버 시작, 브라우저 자동 열림, 운영자 UI HTTP 200, 로고 정상 표시.

실행 시 콘솔 출력:
```
INFO  httpx     HTTP Request: GET .../v1beta/models "HTTP/1.1 200 OK"
INFO  session   Auto-selected Gemini model: gemini-3.5-live-translate-preview
INFO  ops       Glossary loaded: 14 direct entries, 5 review-only
INFO            Started server process [...]
INFO            Uvicorn running on http://0.0.0.0:8001
```
2초 후 브라우저가 `http://localhost:8001/`에 자동으로 열린다.

---

## 8. frozen exe 호환을 위한 코드 변경 사항

### `app/config.py` — 경로 해석

```python
# frozen 상태에서 __file__은 PyInstaller의 임시 압축 해제 폴더를 가리킨다.
# 사용자가 편집하는 파일(config.yaml, .env)은 exe 옆에 있어야 한다.
_ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"
load_dotenv(_ROOT / ".env")
```

### `app/logger.py` — 로그 디렉터리

```python
import sys as _sys
_log_base = Path(_sys.executable).parent if getattr(_sys, "frozen", False) else Path(".")
_log_dir  = _log_base / _cfg.get("log_dir", "logs")
```

이 변경 없이는 로그가 임시 압축 해제 폴더(`%TEMP%\_MEIxxxxxx\logs\`) 안에 기록되고 exe 종료 시 사라진다.

### `main.py` — uvicorn 앱 참조

```python
uvicorn.run(app, ...)        # 올바름 — frozen·일반 환경 모두 동작
uvicorn.run("main:app", ...) # frozen exe에서 실패
```

### `main.py` — 포트 충돌 감지

uvicorn 시작 전에 추가:
```python
def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0
```
포트가 사용 중이면 안내 메시지를 출력하고, 브라우저를 이미 실행 중인 서비스로 열고, 깔끔하게 종료한다.

### `main.py` — 브라우저 자동 열기

```python
def _open_browser():
    import time; time.sleep(2)
    webbrowser.open(f"http://localhost:{port}/")

threading.Thread(target=_open_browser, daemon=True).start()
```
2초 대기는 uvicorn이 바인드를 완료하기 전에 브라우저가 요청을 보내는 것을 방지한다.  
`SKC_start.bat`에 있던 중복 브라우저 열기 로직은 이 변경 후 제거했다.

---

## 9. PyInstaller 스펙 주요 결정

### `hiddenimports`가 필요한 이유

PyInstaller는 정적 분석으로 임포트를 탐색한다. 다음 항목은 놓친다:
- 런타임에 동적으로 로드되는 패키지 (uvicorn의 프로토콜/루프 백엔드)
- `collect_submodules()`가 완전히 열거하지 못하는 패키지
- `scipy`의 내부 확장 모듈

### `collect_data_files()`가 필요한 이유

일부 패키지는 CA 인증서, proto 정의, 템플릿 등 Python이 아닌 파일을 포함한다. PyInstaller에게 명시하지 않으면 복사되지 않는다. 해당 패키지: `google.genai`, `google.api_core`, `google.auth`, `grpc`, `certifi`, `sse_starlette`.

### `console=True`인 이유

exe가 콘솔 창을 유지한다. 예배 중 운영자가 실시간 번역 로그, 세션 상태, 오류, 재연결 이벤트를 볼 수 있다.

### `upx=False`인 이유

UPX는 exe를 압축하지만 교회 컴퓨터처럼 신뢰 이력이 없는 기기에서 Windows Defender 오탐을 유발할 수 있다. 마찰을 피하기 위해 생략했다.

### PyAudio 바이너리

conda의 PyAudio 빌드는 PortAudio를 `_portaudio.cp311-win_amd64.pyd` 안에 정적 링크한다. 별도의 `portaudio_x64.dll`이 필요하지 않다. 스펙의 `binaries`에 명시적으로 포함한다.

---

## 10. 재빌드 방법

```bat
build_exe.bat
```

또는 직접:
```bat
conda run -n skc_build pyinstaller SKC_translation.spec --noconfirm ^
    --workpath .agent\build ^
    --distpath .agent\dist
```

모듈 누락 오류가 발생하면 패턴은 항상 같다:
1. traceback에서 누락된 모듈 확인
2. `skc_build`에 설치: `conda run -n skc_build pip install <모듈>`
3. 정적 분석이 놓치는 경우 스펙 `hiddenimports`에 추가
4. 재빌드

---

## 11. `skc_build` 환경 필수 패키지

```
google-genai
fastapi
uvicorn[standard]
pyaudio
numpy
scipy
python-dotenv
pyyaml
qrcode[pil]
Pillow
sse-starlette
pyinstaller
```

Python 버전: **3.11** (PyAudio 바이너리 접미사 `cp311`과 일치해야 함).

---

## 12. 병렬 멀티스레드 빌드 러너 (`build_parallel.py`) — Release v3.0.0

### 배경 및 필요성
`SKC_translation.exe` (메인 서버)와 `SKC_setup.exe` (2열 설정 마법사) 두 개의 실행 파일을 순차 빌드할 경우 빌드 시간이 2배로 소요됩니다.

### 구현 및 사용법
`build_parallel.py`는 CPU 멀티코어를 활용하여 두 `.spec` 파일을 병렬 프로세스로 동시 컴파일합니다.

```bat
# 기본 실행 (시스템 CPU 코어 기반 자동 병렬 실행)
python build_parallel.py

# 워커 수 지정 (예: 4코어)
python build_parallel.py -j 4

# 일괄 원클릭 실행 스크립트
build_exe.bat
```

### 주요 기능
1. **실시간 마일스톤 스트리밍**: `[Translation]` 및 `[Setup]` 프리픽스와 경과 시간을 실시간으로 콘솔에 출력.
2. **정확한 실제 경과 시간 (Wall-Clock Time) 측정**: 순차 연산 시간의 단순 합산이 아닌 실제 작업 완료까지 걸린 현실 시간을 측정하여 속도 향상(Speedup Factor)을 보고.
3. **독립 격리 빌드**: 각 빌드가 충돌 없이 별도 작업 디렉토리를 사용하도록 보장.
4. **빌드 후 필수 자산 자동 패키징 (`_safe_copy`)**:
   - `branding/church-logo.png` 및 `church-logo.webp`
   - `config.yaml`
   - `CHANGELOG.md` (버전별 릴리즈 변경 내역)
   - `how_to_use.html` (사용자 가이드)
   - `cloudflared.exe`는 ZIP에서 제외하며 설정 마법사에서 다운로드
   - `.env.example` (템플릿 파일; 보안을 위해 개발자 활성 키가 담긴 `.env`는 복사 및 압축 대상에서 원천 제외)
   - 파일 잠금 충돌(`PermissionError: [Errno 13]`) 방지: 파일 크기가 동일하거나 실행 중인 경우 안전하게 건너뛰며, `build_exe.bat` 시작 시 `taskkill`로 잔여 프로세스를 선제 정리.
5. **원클릭 배포 압축 파일 자동 생성 (`create_distribution_zip`)**:
   - 빌드 완료 즉시 `CHANGELOG.md`의 최신 버전을 파싱하여 `.agent\dist\SKC_translate_v{version}.zip`을 자동 생성.
   - **보안 격리**: 민감한 API 키가 포함된 `.env`, `token.txt`, 런타임 로그(`logs/`), 내부 캐시(`var/`, `__pycache__`), 동적 생성 QR(`qr_*.png`)을 엄격히 제외하고 순수 배포 에셋만 패키징.
   - 옵션: `--no-zip` (압축 건너뛰기), `--package-only` (바이너리 재컴파일 없이 자산 복사 및 배포 압축만 수행).
