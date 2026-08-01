# SKC 실시간 예배 번역 시스템 v1.0.0 릴리즈 노트

Google Gemini Live API (`gemini-3.5-live-translate-preview`) 기반의 실시간 한영 예배 자막 및 음성 통역 단일 실행 시스템입니다.

---

## 🚀 실행 파일 (`SKC_translation.exe`) 구동 가이드

### 1. 파일 다운로드 및 폴더 구성
아래 **Assets** 항목에서 `SKC_translation.exe`를 다운로드합니다.

프로그램을 정상 구동하려면 Windows PC의 **동일한 폴더** 내에 아래 **3개 파일**이 반드시 함께 위치해야 합니다:

```
📂 LiveTranslation/
 ├── 📄 SKC_translation.exe  (다운로드한 실행 파일)
 ├── 📄 config.yaml          (시스템 설정 파일)
 └── 📄 .env                 (API 키 및 환경 변수 파일)
```

---

### 2. 제미나이 API 키 설정 (`.env`)

1. `SKC_translation.exe`와 동일한 폴더에 `.env` 텍스트 파일을 생성합니다. (또는 `.env.example` 파일의 이름을 `.env`로 변경)
2. [Google AI Studio](https://aistudio.google.com/apikey)에서 API 키를 발급받습니다. *(참고: 60분 이상 연속 예배 진행 시 자막 중단을 방지하기 위해 유료 티어(Paid Tier) 결제 등록이 권장됩니다).*
3. 메모장 등의 텍스트 편집기로 `.env` 파일을 열고 발급받은 API 키를 입력합니다:
   ```env
   GEMINI_API_KEY=AIzaSy실제발급받은API키입력
   ```
4. 파일을 저장합니다.

---

### 3. 오디오 장치 및 설정 (`config.yaml`)

`config.yaml` 파일을 열어 오디오 입력 장치 번호(`device_index`) 및 포트를 확인하고 필요 시 수정합니다:
```yaml
port: 8080
device_index: 1  # 연결된 USB 믹서 또는 마이크 인덱스 번호
```
PC에 연결된 입력 장치 번호 목록은 터미널에서 `python -m app.audio --list` 명령어로 확인하거나, 프로그램 실행 후 관리자 콘솔 드롭다운 메뉴에서 확인하실 수 있습니다.

---

### 4. 프로그램 실행
`SKC_translation.exe` 파일만을 더블 클릭하여 실행합니다.
- 콘솔 창이 열리며 시스템 가동 로그가 출력됩니다.
- 약 2초 후 기본 웹 브라우저에 관리자 콘솔 (`http://localhost:8080/`)이 자동으로 열립니다.
- 화면에 표시된 QR 코드를 참석자가 스마트폰으로 스캔하면 모바일 브라우저 (`http://<서버IP>:8080/live`)를 통해 실시간 자막과 음성 통역을 이용할 수 있습니다.
