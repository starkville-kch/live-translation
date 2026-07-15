# 실시간 예배 번역 시스템 / Live Translation System
### 스탁빌 한인 교회 (PCA) 실시간 한영 번역 서비스 / Real-Time Korean-English Translation for Church Services

[🐍 Python](https://www.python.org/) | [⚡ FastAPI](https://fastapi.tiangolo.com/) | [🎙️ PyAudio](https://people.csail.mit.edu/hubert/pyaudio/) | [♊ Gemini Live API](https://ai.google.dev/gemini-api/docs/live-api) | [🔊 Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API) | [💬 Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)

---

언어 선택 / Select Language:
- 🇰🇷 [한국어 — 실시간 예배 번역 시스템](#korean-section)
- 🇺🇸 [English — Live Translation System](#english-section)

***

<details open>
<summary><b>🇰🇷 한국어 버전 (클릭하여 접기/펼치기)</b></summary>
<a name="korean-section"></a>

## 📌 목차
1. [시스템 소개](#1-시스템-소개)
2. [📖 문서 가이드 디렉토리](#2-문서-가이드-디렉토리)
3. [🖥️ 사용자 및 관리자 화면 구성](#3-사용자-및-관리자-화면-구성)
4. [💻 로컬 개발 환경 실행](#4-로컬-개발-환경-실행)
5. [💰 서비스 운영 비용 분석](#5-서비스-운영-비용-분석)

---

## 1. 시스템 소개

예배용 실시간 한영 번역 시스템입니다. 마이크 오디오 입력을 캡처하고 Google Gemini Live API를 통해 번역하여 로컬 WiFi 네트워크 내의 참석자 모바일 브라우저로 자막과 오디오를 스트리밍합니다. 스탁빌 한인 교회(PCA)를 위해 제작되었으나 다른 교회에서도 설정하여 사용할 수 있습니다.

---

## 2. 문서 가이드 디렉토리

시스템 운영, 유지보수 및 편집을 위한 모든 세부 문서는 아래 개별 가이드로 분리되어 관리됩니다. 필요한 가이드의 링크를 클릭하여 확인하십시오.

### 👥 1. 봉사자 및 운영자용

* **봉사자 운영 매뉴얼** — 마이크 입력 확인, 시작 및 중지 조작, QR 코드 제공 등 운영 절차 안내
  * **[🇺🇸 영어 가이드](docs/HOW_TO_USE.en.md)** · **[🇰🇷 한국어 가이드](docs/HOW_TO_USE.ko.md)**

### 🛠️ 2. 기술 유지보수자용

* **기술 유지보수 및 아키텍처 플랜** — 파일 구조, 단계별 개발 현황, 기술 스택, 설정 참고
  * **[🇰🇷 한국어](docs/PLAN.ko.md)** · **[🇺🇸 English](docs/PLAN.en.md)**

* **개발 빌드 및 히스토리 로그** — 세션별 개발 기록, 검증 프로토콜(V0–V6), 기술 의사결정 회고
  * **[🇰🇷 한국어](docs/WORKTHROUGH.ko.md)** · **[🇺🇸 English](docs/WORKTHROUGH.en.md)**

* **기술 참고서 (코드 레벨 구현 상세)** — FastAPI 라우트, Gemini 세션, 오디오 파이프라인, asyncio 패턴
  * **[🇰🇷 한국어](docs/TECHNICAL.ko.md)** · **[🇺🇸 English](docs/TECHNICAL.en.md)**

* **단일 실행 파일 빌드 기록** — PyInstaller 빌드 7회 시도 기록, spec 설정, frozen exe 코드 변경
  * **[🇰🇷 한국어](docs/BUILD_EXE.ko.md)** · **[🇺🇸 English](docs/BUILD_EXE.en.md)**

### 🔐 3. 운영 및 소유권 관리

* **사이트 운영 및 비밀키 관리**
  * **API 키 발급 및 등록**: [Google AI Studio](https://aistudio.google.com/)에서 API 키를 발급받고 결제 정보(Billing)를 등록해야 합니다. 무료 키는 60분 연속 가동 시 분당 한도 초과로 자막 중단이 발생할 수 있습니다.
  * **환경 변수 파일 설정**: 발급받은 키는 프로젝트 루트 디렉토리의 `.env` 파일에 `GEMINI_API_KEY=your_key` 형태로 저장하여 보안을 관리합니다.
  * **로컬 인프라 제어**: `config.yaml` 파일을 통해 로컬 PC의 입력 믹서 인덱스 등을 지정합니다.
  * **소유권 이양**: 교회 시스템의 영속성을 위해 GitHub 권한과 Google Billing 소유권을 다음 유지보수자에게 안전하게 인계하는 원칙을 정의합니다.

---

## 3. 사용자 및 관리자 화면 구성

### 1. 관리자 제어 콘솔 (`/`)
참석자용 QR 코드 생성, 오디오 입력 기기 설정 및 제미나이 번역 엔진의 시작/일시정지/종료 제어 등을 담당하는 중앙 관리 화면입니다.

![Operator Console](app/operator_screen.png)

* **주요 요소 설명**:
  * **오디오 장치 설정 (Audio Device Index)**: 현재 Windows PC에 연결된 오디오 입력 장치 번호를 입력하고 저장합니다.
  * **제어 스위치 (Start / Pause / Stop)**:
    * `Start`를 눌러 AI 번역 세션을 열고, 예배 도중 잠시 멈출 때는 `Pause`를, 예배 종료 시엔 `Stop`을 눌러 자막 텍스트 저장을 수행합니다.
  * **레벨 미터 & 상태 표시 (Level Meter & Status Logs)**: 마이크 입력 감도를 측정하는 실시간 데시벨(dB) 게이지와 Gemini API 통신 상태를 실시간 콘솔 로그로 모니터링합니다.
  * **음성 통역 모니터 (Audio Monitor)**: 관리자가 헤드폰이나 이어폰을 착용하고 실제 참석자들에게 송출되는 실시간 영어 번역 음성 스트림을 서버 PC에서 실시간으로 모니터링하고 볼륨을 제어할 수 있는 채널입니다.
  * **QR 코드 & 스트림 URL (QR Share Panel)**: 예배당 참석자들이 스마트폰으로 즉시 자막 주소에 접속할 수 있도록 QR 코드를 화면에 크게 송출합니다.

### 2. 참석자 자막 및 오디오 수신 페이지 (`/live`)
예배당 내 영어권 참석자들이 스마트폰 브라우저를 통해 실시간 번역 자막을 읽고 음성을 청취하는 페이지입니다.

![Attendee Caption Page](app/user_screen.png)

* **주요 요소 설명**:
  * **하단 정렬 자막 스트림 (Bottom-aligned Captions)**: 새로 추가되는 자막 텍스트 라인이 화면 하단에 차례대로 흘러나오며 자연스러운 눈높이를 제공합니다.
  * **글꼴 크기 슬라이더 (Font Size Slider)**: 시력에 맞춰 실시간으로 자막 크기를 세밀하게 조절합니다.
  * **오디오 활성화 버튼 (Audio Playback Control)**: 이어폰을 소지한 사용자가 실시간 AI 통역 오디오(Orus 보이스)를 들을 수 있도록 실시간 웹소켓 PCM 버퍼링 오디오 채널을 키고 끕니다.
  * **상태 인디케이터 (Status Badge)**: `● Live` 혹은 `● Reconnecting` 배지를 통해 연결 상태를 실시간으로 확인합니다.

---

## 4. 로컬 개발 환경 실행

로컬 개발 환경 설정에 관한 자세한 사양은 기술 유지보수 가이드를 참고하시기 바라며, 아래 핵심 명령어로 즉시 시작할 수 있습니다.

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

---

## 5. 서비스 운영 비용 분석

이 시스템은 Google Gemini 3.5 Live Translate API 유료 티어(Paid Tier) 요금을 기준으로 작동합니다. 아래는 일반적인 교회 주일 예배 운영 시 예상되는 비용 예시입니다.

### 1. API 요금 기준
* **입력 오디오 (Input Audio)**: $3.50 / 1M tokens (약 $0.0053 / 분)
* **출력 오디오 (Output Audio)**: $21.00 / 1M tokens (약 $0.0315 / 분)
* **합산 분당 요율 (Combined Rate)**: **약 $0.0368 / 분**

### 2. 예배당 예상 비용 계산 예시
* **1회 예배 기준 (60분 가동 시)**:
  * $0.0368/분 × 60분 = **약 $2.21** / 회
* **월간 기준 (4주 예배 가동 시)**:
  * $2.21 × 4주 = **약 $8.84** / 월

> [!NOTE]
> 서버에서 하나의 API 세션만 열어 오디오/자막을 팬아웃(broadcast)하므로, **동시 접속한 참석자 수가 늘어나도 API 비용은 동일하게 유지됩니다.**

</details>

***

<details>
<summary><b>🇺🇸 English Version (Click to Collapse/Expand)</b></summary>
<a name="english-section"></a>

## 📌 Table of Contents
1. [System Overview](#1-system-overview)
2. [Documentation Directory](#2-documentation-directory)
3. [User and Operator Interfaces](#3-user-and-operator-interfaces)
4. [Local Development Setup](#4-local-development-setup)
5. [Service Operational Cost Analysis](#5-service-operational-cost-analysis)

---

## 1. System Overview

This is a real-time Korean to English translation system for church services. It captures audio from a microphone input, translates it using the Google Gemini Live API, and streams captions and audio to attendees' mobile web browsers over a local WiFi network. It was originally created for Starkville Korean Church (PCA) but can be set up for other churches.

---

## 2. Documentation Directory

All detailed guides for running, maintaining, and editing the system are managed in separate files below. Click on the hyperlinks to access them.

### 👥 1. For Operators & Volunteers

* **Volunteer & Operator Manual** — check mic inputs, start/stop translation, share the QR code
  * **[🇺🇸 English Guide](docs/HOW_TO_USE.en.md)** · **[🇰🇷 Korean Guide](docs/HOW_TO_USE.ko.md)**

### 🛠️ 2. For Technical Maintainers

* **Technical Maintainer & Architecture Plan** — file map, phase history, tech stack, config reference
  * **[🇰🇷 Korean](docs/PLAN.ko.md)** · **[🇺🇸 English](docs/PLAN.en.md)**

* **Build Workthrough & History Log** — chronological sessions, verification protocol (V0–V6), retrospective
  * **[🇰🇷 Korean](docs/WORKTHROUGH.ko.md)** · **[🇺🇸 English](docs/WORKTHROUGH.en.md)**

* **Technical Reference (Code-Level Details)** — FastAPI routes, Gemini session, audio pipeline, asyncio patterns
  * **[🇰🇷 Korean](docs/TECHNICAL.ko.md)** · **[🇺🇸 English](docs/TECHNICAL.en.md)**

* **Single Executable Build Record** — PyInstaller 7-attempt log, spec decisions, frozen exe code changes
  * **[🇰🇷 Korean](docs/BUILD_EXE.ko.md)** · **[🇺🇸 English](docs/BUILD_EXE.en.md)**

### 🔐 3. Site Governance & API Key Registry

* **System Governance & Credentials**
  * **API Key Procurement**: API keys must be generated via [Google AI Studio](https://aistudio.google.com/) with **Billing enabled (Paid Tier)**. Free keys will hit rate limits and fail during standard 60+ minute church services.
  * **Environment Configuration**: Keys are secured locally in a `.env` file (`GEMINI_API_KEY=your_key`) at the root directory.
  * **Local Device Binding**: Local mixer device configurations are bound via the local `config.yaml` file.
  * **Governance & Handoff**: Outlines access rights transfer, repository delegation, and billing ownership handover rules for future church volunteers.

---

## 3. User and Operator Interfaces

### 1. Operator Control Console (`/`)
This page acts as the central control room for volunteers to generate attendee QR codes, bind local audio devices, and start/mute/stop the Gemini Live session.

![Operator Console](app/operator_screen.png)

* **Element Explanations**:
  * **Audio Device Index**: Enter and save the audio input device number currently connected to the Windows PC.
  * **Control Switches (Start / Pause / Stop)**:
    * Press `Start` to open the AI translation session. Press `Pause` during brief breaks, and `Stop` at the end of the service to save the caption transcript.
  * **Level Meter & Status Logs**: A real-time decibel (dB) gauge monitoring microphone input sensitivity, alongside live console logs showing Gemini API communication status.
  * **Audio Monitor**: Allows the operator to listen to the real-time translated voice via headphones to audit output quality and control the monitor volume.
  * **QR Share Panel**: Displays a large QR code so sanctuary attendees can instantly access the caption URL on their smartphones.

### 2. Attendee Caption Page (`/live`)
This layout serves real-time English text captions and live translation audio directly to attendees' mobile web browsers.

![Attendee Caption Page](app/user_screen.png)

* **Element Explanations**:
  * **Bottom-aligned Captions**: New caption lines flow up from the bottom of the screen, providing a natural reading eye level.
  * **Font Size Slider**: Allows each attendee to fine-tune caption size to their vision needs in real time.
  * **Audio Playback Control**: Enables attendees with earphones to receive the live AI translation audio (Orus voice) via a real-time WebSocket PCM buffering audio channel.
  * **Status Badge**: Displays `● Live` or `● Reconnecting` to show connection status in real time.

---

## 4. Local Development Setup

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

---

## 5. Service Operational Cost Analysis

This system operates under the Google Gemini 3.5 Live Translate Paid Tier. Below is a cost estimation for a typical Sunday service operation:

### 1. Pricing Basis
* **Input Audio**: $3.50 / 1M tokens (approx. $0.0053 / min)
* **Output Audio**: $21.00 / 1M tokens (approx. $0.0315 / min)
* **Combined Rate**: **approx. $0.0368 / min**

### 2. Typical Cost Scenario
* **Per Service (60-minute session)**:
  * $0.0368/min × 60 min = **approx. $2.21** / service
* **Monthly Estimate (4 Sundays)**:
  * $2.21 × 4 weeks = **approx. $8.84** / month

> [!NOTE]
> Because the server broadcasts captions and audio from a single central API session, **API costs remain constant regardless of the number of connected attendee devices.**

</details>
