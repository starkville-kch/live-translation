# SKC 실시간 번역 자막 시스템 가이드 / Volunteer Guide

예배의 모든 은혜로운 순서를 실시간 한영 번역 자막으로 온전히 전달하기 위한 봉사자 안내서입니다. 오디오 입력 설정부터 AI 번역 구동, 그리고 자막 방송 운영 절차까지 필요한 모든 내용을 안내합니다.  
*This volunteer guide serves to ensure every grace-filled moment of the service is beautifully conveyed through real-time Korean-to-English translation. Find everything you need to configure audio capture, launch the Gemini AI translator, and manage live caption streams.*

---

## 📖 시스템 작동 원리 / How the System Works

목사님용 무선 핀마이크와 단상 마이크 등의 소리를 믹서에서 받아 PC가 캡처합니다. 이후 구글 **제미나이 Live API**를 통해 실시간으로 번역하고, 번역된 텍스트와 오디오(24kHz PCM)를 교회 내부 와이파이(WiFi)를 통해 참석자들의 폰으로 전송합니다. 참석자는 QR 코드를 스캔하여 브라우저에서 편리하게 자막과 오디오(선택 가능)를 이용할 수 있습니다.

*Audio from independent wireless lapel mics (pastor & podium) is captured on this local Windows PC, streamed to Google **Gemini Live API** for real-time translation, and broadcasted as live text captions (SSE) and audio streams (WebSocket) to attendee phones over the church WiFi. Attendees scan a QR code to connect instantly.*

*   🎙️ **오디오 캡처 (Audio Capture)**: 두 개의 독립된 무선 핀마이크 소리를 믹서 및 USB 단자를 통해 PC에서 캡처합니다. (*Captures high-quality audio from lapel mics via the USB mixer input.*)
*   🤖 **제미나이 Live API (Gemini Live API)**: 구글의 실시간 통역 모델을 활용해 지연 시간(Latency, 약 2.2초) 없이 고품질 한영 번역을 제공합니다. (*Streams audio to Gemini for instant translations with pinned deep voice playback.*)
*   📱 **폰 자막 & 오디오 (Phone Broadcast)**: 브라우저 자막 및 이어폰 착용자를 위한 24kHz PCM16 실시간 영어 음성 합성을 지원합니다. (*Displays live captions and routes translation audio directly to phones using 24kHz PCM16 streams.*)

---

## 🛠️ 최초 1회 설정 / One-Time Setup

> [!IMPORTANT]
> 이 설정은 시스템을 처음 구축하거나 오디오 믹서 환경이 변경되었을 때 1회만 수행하면 됩니다.  
> *This setup only needs to be completed once during initial installation or after changing mixer hardware.*

### 1. 파이썬 패키지 설치 / Install Python Dependencies
프로젝트 폴더에서 터미널을 열고 다음 명령어를 실행합니다:  
*Open a terminal in the project folder and run:*
```bash
conda activate agent
pip install -r requirements.txt
```

### 2. Gemini API 키 발급 및 등록 / Obtain & Configure Gemini API Key
1. [Google AI Studio](https://aistudio.google.com/)에 접속하여 구글 계정으로 로그인합니다.  
   *Sign in to Google AI Studio.*
2. **Get API Key** 버튼을 눌러 새 API 키를 생성합니다.  
   *Click Get API Key and generate a new key.*
3. **[중요 - 유료 결제 계정 등록 필수]**: 60분 이상 연속으로 번역을 가동하기 위해 반드시 **결제 계정(Billing/Paid Tier)이 연동된 유료 키**를 사용해야 합니다. 무료 티어 키는 엄격한 분당 요청 제한(Rate Limit)으로 인해 예배 도중 통신이 끊어집니다.  
   ***[Important - Enable Billing]**: To support unbroken streaming for 60+ minute church services, you must link a credit card to enable Billing (Paid Tier). Free-tier keys will hit rate limits and disconnect mid-service.*
4. 프로젝트 폴더 루트에 `.env` 파일을 새로 생성하고, 다음과 같이 발급받은 API 키를 적어줍니다:  
   *Create a `.env` file in the root of the project and insert your API key:*
```env
GEMINI_API_KEY=your_actual_api_key_here
```

### 3. USB 믹서 장치 인덱스 확인 / Find Mixer Device Index
USB 믹서를 연결한 뒤 다음 스크립트를 실행해 시스템이 인식한 오디오 장치 목록을 출력합니다:  
*Connect the USB mixer to the PC, and list all active inputs:*
```bash
conda activate agent
python -m app.audio --list
```
목록에서 USB 코덱 장치 번호(예: `[2] USB Audio Codec`)를 확인하세요.  
*Find the correct input device (e.g., `[2] USB Audio Codec`) and note its index number.*

### 4. config.yaml 파일 설정 / Update config.yaml
`config.yaml` 파일을 텍스트 에디터로 열고, 확인한 오디오 장치 인덱스 번호를 저장합니다:  
*Open `config.yaml` in an editor and set your target input device index:*
```yaml
audio:
  device_index: 2   # ← 여기에 믹서 장치 번호(Index)를 입력합니다
```

### 5. 오디오 캡처 테스트 / Test Audio Capture
오디오 신호가 깨끗하게 잡히는지 테스트 모드로 먼저 검증합니다:  
*Record a brief sample locally to verify the audio signal is clean and clear:*
```bash
python -m app.audio --test 2 30
# 30초간 오디오를 녹음한 뒤 test_capture_2.wav 파일로 저장합니다.
# 미디어 플레이어로 열어서 마이크 소리가 노이즈 없이 잘 들리는지 확인하세요.
```

---

## 📅 주일 예배 운용 절차 / Every-Service Workflow

### Step 1: 번역 서버 실행 / Start the Server
터미널을 열고 번역 서버를 구동합니다. 예배가 진행되는 동안 터미널을 닫지 말고 그대로 열어두세요:  
*Open your terminal, activate conda, and run the FastAPI server. Keep this window open during service:*
```bash
conda activate agent
python main.py
```

### Step 2: 관리자 콘솔 접속 / Open the Operator Console
이 PC의 브라우저에서 `http://localhost:8000` 주소로 접속합니다.  
*Open `http://localhost:8000` in a browser on this host PC.*

### Step 3: 마이크 장치 선택 및 서비스 시작 / Select Device and Click Start
화면의 마이크 입력 드롭다운에서 예배 마이크와 연결된 USB 오디오 코덱을 확인하고, **▶ Start** 버튼을 누릅니다. 마이크 입력 레벨 미터(Level Meter)가 소리에 따라 올라가고, 상태 표시 등이 수 초 내로 **Live** 상태로 바뀝니다.  
*Select your USB Audio Codec from the dropdown list, then click **▶ Start**. The level meter should respond to microphone audio, and the status badge will change to **Live** within a few seconds.*

### Step 4: 참석자들에게 QR 코드 공유 / Share QR Code
관리자 페이지 상단에 띄워진 QR 코드는 참석자들이 자막과 통역 오디오를 볼 수 있는 웹 페이지 주소입니다. 이 QR 코드를 인쇄하거나 예배당 입구 또는 스크린에 띄워 공유해 주세요. 참석자들은 스캔 후 영어 자막 및 실시간 영어 음성 통역(이어폰 착용 필수)을 선택하여 이용할 수 있습니다.  
*Attendees scan the QR code to open the English captioning and audio page. You can print the QR code or display it on a sanctuary screen. Attendees can see the live text and enable translation voice playback (earphones recommended).*

### Step 5: 예배 종료 후 중지 / Stop Service
예배가 끝나면 관리자 페이지에서 **■ Stop** 버튼을 눌러 연결을 정상 종료합니다. 종료 시 자동으로 해당 세션의 한영 대조 전사록(`.txt` 포맷)이 `logs/sessions/현재날짜_시간/` 디렉토리에 저장되어 예배 기록 검증에 유용하게 쓸 수 있습니다. **저장 완료를 확인한 후, 화면 하단에 있는 [🔴 프로그램 완전 종료 (Exit System)] 버튼을 누르면 서버가 안전하게 종료됩니다. (이후 남은 검은색 터미널 창은 그냥 닫아주시면 됩니다.)**  
*When the service finishes, click the **■ Stop** button. The system will automatically write session transcript export files under `logs/sessions/YYYYMMDD_HHMMSS/` for pastoral review. **Once saved, simply click the [🔴 프로그램 완전 종료 (Exit System)] button at the bottom of the page to shut down the server gracefully. You can then safely close the remaining black Command Prompt window.***




---

## 🚥 상태 표시등 설명 / Status Badge Explanations

| 표시등 / Badge | 의미 / Meaning | 조치 조언 / Action Required? |
| :--- | :--- | :--- |
| **Live** (녹색) | 번역 서비스 및 캡처가 정상 작동 중입니다. <br> *Translation and audio capture running normally.* | 없음 <br> *None* |
| **Starting** (황색) | 구글 제미나이 Live API 연결을 시도하고 있습니다. <br> *Initiating WebSocket connection to Gemini Live.* | 연결이 수 초 내로 완료될 때까지 기다리세요. <br> *None — wait a few seconds.* |
| **Reconnecting** (황색) | 일시적인 오류 혹은 10분 연결 주기 갱신으로 인한 자동 재연결 중입니다. <br> *Automatic reconnect in progress.* | 기다려 주세요 (자동으로 2~3초 내에 복구됩니다). <br> *None — captions resume in 2–3s.* |
| **Error** (적색) | 인터넷 불안정 등으로 인해 API 연결이 중단되었습니다. <br> *Sustained API connection failure.* | 네트워크 상태를 확인하고 Stop → Start를 다시 눌러주세요. <br> *Check internet connection. Click Stop then Start again.* |

> [!NOTE]
> 구글 API 스펙상 실시간 라이브 세션은 약 10분 주기로 재갱신이 필요하므로 중간에 2~3초 정도 **Reconnecting** 과정이 발생하는 것은 지극히 정상적인 오류 복구 과정입니다. 참석자는 새로고침 없이 그냥 두면 다시 정상적으로 자막을 보실 수 있습니다.  
> *Reconnections are expected. The Gemini Live API WebSocket triggers a refresh roughly every 10 minutes. Attendees will see a brief 2–3 second pause, after which captions and audio resume automatically without requiring a page reload.*

---

## 🔗 웹 페이지 주소 종류 / System Pages

| URL | 대상 사용자 / User | 기능 및 역할 / Description |
| :--- | :--- | :--- |
| `http://<ip>:8000/` | 운영 봉사자 (Operator) | 마이크 입력 장치 변경, 시작/중지, 일시정지, 실시간 자막 모니터링, 비용/오디오 레벨 확인 <br> *Start/stop translation, pause billing, adjust volumes, level meters, event log.* |
| `http://<ip>:8000/live` | 예배 참석자 (Attendees) | 실시간 대형 영어 자막, 폰 크기 조정, 이어폰 착용자를 위한 실시간 인-브라우저 영어 음성 통역 <br> *Large typography live captions, font size, active Web Audio playback.* |

---

<details>
<summary><b>⚙️ 설정 파일 가이드 / Configuration File Guide (클릭하여 펼치기 / Click to expand)</b></summary>

<br>

`config.yaml` 파일은 시스템의 핵심 작동 설정을 보관하는 텍스트 파일입니다. 대부분의 경우 수정할 필요가 없으나, 서버 포트 충돌 해결이나 오디오 장치 디버깅 등의 상황에서 참조 및 편집할 수 있습니다.
*The `config.yaml` file holds the core runtime parameters of the system. While editing it is rarely needed in day-to-day operations, it serves as a critical reference for addressing network port conflicts or diagnosing audio input issues.*

> [!WARNING]
> **주석 관련 주의사항 (Comment Preservation Warning)**:  
> 시스템 구동 시 모델명을 감지하여 자동 업데이트하거나, 브라우저 관리자 페이지에서 입력 마이크 장치를 변경하면 `config.yaml` 파일이 프로그램에 의해 새로 덮어쓰기 됩니다. 이 과정에서 파일 내부에 직접 작성한 **YAML 주석(#)은 모두 삭제되므로**, 각 설정값에 대한 상세 설명은 이 가이드나 `app/config.py` 소스코드의 주석을 참고하십시오.  
> *When the system starts up or when you modify the audio input device from the operator dashboard, the program rewrites `config.yaml`. This process **strips all inline comments (#) from the file**. Refer to this guide or the docstring in `app/config.py` for parameter explanations rather than writing inline comments inside the file.*

### 설정 항목 설명 / Configuration Parameters

*   **`audio`** (오디오 입력 설정 / Audio Capture Settings)
    *   **`device_index`**: Windows PC가 마이크를 캡처할 오디오 입력 장치 번호입니다. `python -m app.audio --list` 결과에 맞추어 기입합니다. (*The target input audio device index. Run `python -m app.audio --list` to identify the correct value.*)
    *   **`auto_stop_timeout_min`**: 무음 감지 시 자동 종료 시간(분)입니다. 마이크 입력이 끊어지거나 소리가 나지 않을 때 이 시간이 지나면 리소스 절약을 위해 시스템을 자동으로 종료합니다. `0`으로 지정하면 자동 종료 기능이 꺼집니다. (*Silence auto-stop timeout in minutes. The system automatically stops when no signal is detected for this duration to conserve resources. Set to `0` to disable.*)
    *   **`sample_rate`**: 오디오 샘플링 속도 (기본 `16000` Hz, 제미나이 Live API 전용 규격으로 변경을 권장하지 않습니다). (*Input sampling rate in Hz. Set to `16000` by default as required by the Gemini Live API. Do not change.*)
    *   **`channels`**: 오디오 채널 수 (기본 `1` - 모노). (*Audio input channels. Set to `1` (mono) by default. Do not change.*)
    *   **`chunk_ms`**: 전송되는 오디오 버퍼 조각 크기 (기본 `100`ms). (*Buffer chunk duration sent to Gemini. Set to `100`ms. Do not change.*)

*   **`gemini`** (제미나이 API 설정 / Gemini Engine Configuration)
    *   **`model`**: 사용 중인 제미나이 번역 모델 이름입니다. 서버가 구동될 때마다 API를 통해 자동으로 최신 모델을 검색하여 변경하므로 수동으로 편집할 필요가 없습니다. (*The active model name. Auto-detected and updated by the server at boot; no manual configuration is required.*)

*   **`logging`** (로그 기록 방식 설정 / System Logging Preferences)
    *   **`log_dir`**: 로그 파일이 저장될 상대 또는 절대 경로입니다 (기본 `logs`). (*Directory where operational logs are saved. Defaults to `logs`.*)
    *   **`max_bytes`**: 하나의 로그 파일이 가질 수 있는 최대 바이트 크기입니다 (기본 `10485760` - 10MB). (*Maximum size in bytes of a single log file before rotation. Defaults to 10MB.*)
    *   **`backup_count`**: 보관할 백업 로그 파일 개수입니다 (기본 `5`). (*Number of rotated log backup files to preserve. Defaults to `5`.*)

*   **`network`** (네트워크 포트 바인딩 설정 / Server Network Configurations)
    *   **`host`**: 서버가 수신 대기할 IP 주소입니다. WiFi를 통한 참석자 폰 접속을 위해 기본값 `0.0.0.0` (모든 대역 수신)을 유지해야 합니다. (*The binding host address. Keep `0.0.0.0` to allow phones to connect over WiFi.*)
    *   **`port`**: FastAPI 웹 서버의 포트입니다 (기본 `8000`). PC 내에 다른 웹 프로그램과 포트 충돌이 생기면 이 값을 `8080` 등으로 변경해 문제를 피합니다. (*The server listener port. Change from `8000` to an alternate port if conflict occurs.*)

</details>

---

## 🔍 오류 및 문제 해결 가이드 / Troubleshooting Guide

### 1. 상태창에 "No signal"이 뜨는데 마이크 소리가 전달되지 않는 경우
*   **원인/해결**: 관리자 콘솔 화면에서 장치 드롭다운이 올바른 USB 오디오 코덱으로 잡혀 있는지 검토해 주세요. 장치 순서가 바뀌었을 수 있으므로 `python -m app.audio --list` 명령어로 장치 번호를 재확인하고 `config.yaml`에 올바른 인덱스를 반영한 뒤 서버를 재시작해 주세요.
*   *Verify the USB mixer is selected in the device selector dropdown. If the mixer's hardware ordering index changed, run `python -m app.audio --list` to identify the new index, write it to config.yaml, and restart.*

### 2. 예배 도중 자막이나 번역이 멈추는 경우
*   **원인/해결**: 관리자 창 상단의 상태 등을 확인하세요. **Reconnecting** 상태라면 자동으로 재연결 중이니 잠시 대기하세요. **Error** 표시 등 상태로 유지되는 경우엔 인터넷 공유기 연결 상태를 확인하고 관리자 페이지에서 Stop 버튼을 누른 다음 다시 Start 버튼을 누르세요.
*   *Check the status badge on the operator dashboard. If it displays Reconnecting, the system is recovering. If it is stuck on Error, verify internet availability and cycle the service by clicking Stop and then Start.*

### 3. 참석자 스마트폰이 자막 페이지에 전혀 접속되지 않는 경우
*   **원인/해결**: 자막 페이지는 로컬 공유기 IP를 주소로 사용합니다. 반드시 모든 참석자의 스마트폰이 **교회 전용 내부 WiFi**망에 접속되어 있어야 합니다. 외부 셀룰러 데이터(5G/LTE)망이나 다른 와이파이망에 접속된 상태에서는 보안 및 공유기 IP 특성상 로컬 서버 주소로 들어올 수 없습니다.
*   *Ensure attendee devices are connected to the same church WiFi network as the host PC. The QR code points to the host's local network IP, which is inaccessible from cellular data networks or external routers.*

### 4. 참석자 페이지에 "Translation unavailable" 경고 문구가 뜨는 경우
*   **원인/해결**: 구글 제미나이 API 라이브 서버 연결에 실패한 상태입니다. PC의 유선/무선 인터넷 연결 상태를 우선 점검하고, 관리자 콘솔에서 Stop 후 Start를 다시 눌러 연결을 재설정해 줍니다. 서버가 정상 연결 상태로 복구되면 참석자 페이지도 새로고침 없이 자동으로 활성화됩니다.
*   *The server was unable to establish a connection to the Gemini Live server after multiple retry attempts. Check internet access, then click Stop and Start again. Attendees do not need to refresh; they will resume captions automatically once translation starts.*

> [!WARNING]
> **보안 주의 (Security Warning)**: 절대 외부로 `.env` 설정 파일을 공유하거나 커밋하지 마세요. 해당 파일은 교회 고유의 제미나이 API 비밀 키를 보관하고 있으므로 유출 시 원치 않는 API 요금이 발생할 수 있습니다.  
> *Never share or upload the `.env` file. It holds your private Gemini API Key. Keep it safe on the host server to prevent unauthorized usage charges.*

---

## 📄 예배 전사록 보관 및 로그 파일 / Export Transcripts & Session Logs

이전 예배의 통역 기록을 열람하거나 한영 통역 대조 스크립트가 필요한 경우, 서버 구동 폴더 안의 `logs/sessions/` 폴더로 이동합니다. 각 세션마다 예배를 중지한 시간에 맞추어 텍스트 파일들이 저장되며, `aligned.txt` 파일을 열어 보시면 시간별로 매치된 한국어 원래 소리와 번역된 영어 스크립트가 대조되어 있습니다.

*To review previous sermon scripts or export study materials, navigate to the `logs/sessions/` directory in the project directory. Every stop triggers an export of `ko.txt` (Korean source), `en.txt` (English translation), and `aligned.txt` (interleaved, human-readable).*
