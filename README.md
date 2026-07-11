# Live Translation System / 실시간 예배 번역 시스템

[🐍 Python](https://www.python.org/) | [⚡ FastAPI](https://fastapi.tiangolo.com/) | [🎙️ PyAudio](https://people.csail.mit.edu/hubert/pyaudio/) | [♊ Gemini Live API](https://ai.google.dev/gemini-api/docs/live-api) | [🔊 Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API) | [💬 Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)

This is a real-time Korean to English translation system for church services. It captures audio from a microphone input, translates it using the Google Gemini Live API, and streams captions and audio to attendees' mobile web browsers over a local WiFi network. It was originally created for Starkville Korean Church (PCA) but can be set up for other churches.  
예배용 실시간 한영 번역 시스템입니다. 마이크 오디오 입력을 캡처하고 Google Gemini Live API를 통해 번역하여 로컬 WiFi 네트워크 내의 참석자 모바일 브라우저로 자막과 오디오를 스트리밍합니다. 스탁빌 한인 교회(PCA)를 위해 제작되었으나 다른 교회에서도 설정하여 사용할 수 있습니다.

---

## 📖 문서 가이드 디렉토리 / Documentation Directory

시스템 운영, 유지보수 및 편집을 위한 모든 세부 문서는 아래 개별 가이드로 분리되어 관리됩니다. 필요한 가이드의 링크를 클릭하여 확인하십시오.  
All detailed guides for running, maintaining, and editing the system are managed in separate files below. Click on the hyperlinks to access them.

### 👥 1. 봉사자 및 운영자용 / For Operators & Volunteers

* **[실시간 번역 시스템 사용 가이드 / Bilingual Volunteer Guide](how_to_use.html)**
  * 봉사자가 마이크 입력 확인, 시작 및 중지 조작, QR 코드 제공 등을 할 수 있는 운영 매뉴얼입니다.
  * An operator manual for volunteers to check mic inputs, start/stop translation, and share the QR code.
  * **[📝 사용 가이드 열기 / Open Volunteer Guide →](how_to_use.html)**

### 🛠️ 2. 기술 유지보수자용 / For Technical Maintainers

* **[기술 유지보수 및 아키텍처 플랜 / Technical Maintainer & Architecture Plan](docs/PLAN.md)**
  * FastAPI 서버 구조, PyAudio 캡처 파이프라인, 제미나이 Live API 세션 복구(Session Resumption), 슬라이딩 윈도우 컨텍스트 압축 등 개발자 지침서입니다.
  * System architecture, FastAPI server structure, PyAudio capture pipelines, Gemini Live API session resumption, sliding window context compression, and developer specifications.

* **[개발 빌드 및 히스토리 로그 / Build Workthrough & History Log](docs/WORKTHROUGH.md)**
  * 모델 선정, 검증 테스트, 요금 정보 업데이트 및 다국어 지원에 대한 개발 일지입니다.
  * A chronological build log documenting model selections, verification testing, pricing updates, and multi-language support implementation.


### 🔐 3. 운영 및 소유권 관리 / Site Governance & API Key Registry

* **사이트 운영 및 비밀키 관리**
  * GitHub 리포지토리 권한 설정, Google AI Studio API 키 보안 관리(`.env`), 로컬 오디오 장치 설정(`config.yaml`) 및 재난 복구(Disaster Recovery) 가이드를 포함합니다.
  * Access credentials, API key security (`.env`), local audio configuration (`config.yaml`), and disaster recovery guides.

---

## 💻 로컬 개발 환경 실행 / Local Development Setup

로컬 개발 환경 설정에 관한 자세한 사양은 기술 유지보수 가이드를 참고하시기 바라며, 아래 핵심 명령어로 즉시 시작할 수 있습니다.  
Refer to the Technical Maintainer Guide for full setup details. Run the following commands to get started locally:

```bash
# 1. 가상 환경 활성화 / Activate Conda Environment
conda activate agent

# 2. 의존성 패키지 설치 / Install dependencies
#    Windows: PyAudio requires a pre-built wheel — install via pipwin if plain pip fails
pip install -r requirements.txt
#    If PyAudio fails: pip install pipwin && pipwin install pyaudio

# 3. API 키 설정 / Set up API key
#    Copy .env.example to .env and paste your Gemini API key
cp .env.example .env

# 4. 오디오 장치 목록 확인 / List active audio devices
python -m app.audio --list

# 5. 오디오 캡처 로컬 검증 (장치 인덱스 2, 30초 녹음) / Test audio capture (index 2, 30s)
python -m app.audio --test 2 30

# 6. 로컬 개발 서버 실행 / Start local dev server
python main.py
```

교회 번역 시스템이 특정 개인에게 종속되지 않고 영속성을 갖도록 모든 변경사항 및 권한 양도는 기술 지침과 운영 원칙을 엄격하게 준수하여 주십시오. Please strictly adhere to the guidelines in the Technical Maintainer Guide and Governance rules for all modifications and handoffs to ensure the long-term continuity of the translation system.
