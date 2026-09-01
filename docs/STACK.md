# 기술 결정 및 아키텍처 원칙 (Technical Decisions & Rationale)
### 실시간 예배 번역 시스템 / Live Translation System

이 문서는 시스템의 주요 기술적 결정, 대안 비교, 실패 경험 및 불변 원칙(Invariants)을 영구 보존하기 위한 기술 아키텍처 문서입니다.

---

## 1. Gemini Live Translate — Developer API Language-Hint Constraint

### 배경 및 제약사항
한국어 설교 중 목회자가 다음 세대(2세)를 위해 영어 표현을 혼용(Code-switching)하므로, 입력 언어를 한국어로 단일 고정(Hard-lock)할 수 없습니다.

초기 시도:
```python
types.AudioTranscriptionConfig(
    language_codes=["ko", "en"]
)
```

런타임 결과:
```text
ValueError: language_codes parameter is only supported in Gemini Enterprise Agent Platform mode, not in Gemini Developer API mode.
```

### 결정 및 표준 설정
Google GenAI SDK의 Google AI Studio Developer API 모드에서는 `language_codes` 파라미터가 지원되지 않습니다. 따라서 다음 설정을 표준으로 사용합니다:

```python
input_audio_transcription=types.AudioTranscriptionConfig()
output_audio_transcription=types.AudioTranscriptionConfig()
```

> **불변 원칙**: Google Developer API 공식 문서 및 SDK 사양에 명시적 변경이 없는 한, `language_codes=["ko", "en"]`를 다시 도입하거나 재테스트하지 마십시오.

---

## 2. 세션 오염 방지 및 무결 세션 리셋 아키텍처 (Anti-Contamination)

### 찬양/음악 소음과 언어 이탈(Drift)의 근본 원인
찬양 시간의 악기 및 환경 소음이 Gemini Live 세션에 유입되면 컨텍스트 윈도우가 오염되어, 설교 시작 후에도 한국어를 일본어(ja)나 베트남어(vi)로 잘못 인식하여 오번역하는 현상이 발생합니다.

과거 수동으로 **서비스 종료(Stop) → 시작(Start)**을 수행했을 때 이 문제가 즉시 해결되었던 이유는 **오염된 Gemini 세션 컨텍스트를 파괴하고 완전히 새로운 세션을 열었기 때문**입니다.

### 결정: Pause → Resume을 하드 세션 경계로 설계
이 관찰 결과를 바탕으로, 일시정지(Pause)와 재개(Resume)를 단순한 음소거가 아닌 **무결 세션 리셋(Clean Session Reset)** 경계로 구현했습니다:

1. **Pause 동작**:
   - 마이크 레벨 미터링은 유지하되, 캡처된 PCM 프레임은 큐 진입 직전에 즉시 폐기합니다.
   - 기존 Gemini WebSocket 연결을 닫고, 세션 재개 토큰(`self._resumption_handle`)을 폐기합니다.
   - 서버 및 클라이언트의 잔여 오디오 큐를 플러시하여 이전 세션 오디오가 남지 않도록 합니다.
   - **모델 잠금 유지**: 현재 서비스에 고정된 번역 모델(`locked_model`)은 그대로 유지됩니다 (모델 재선택 방지).
2. **Resume 동작**:
   - `_session_epoch`를 1 증가시킵니다.
   - 잠금된 동일 모델로 신규 Gemini Live 세션을 생성하여 **깨끗한 컨텍스트(Fresh Context)**에서 번역을 시작합니다.
3. **세션 세대(Epoch) 격리**:
   - 이전 세션에서 비동기로 뒤늦게 도착한 전사본이나 오디오 청크는 `epoch != self._session_epoch` 검사를 통해 즉시 폐기됩니다.

---

## 3. 치명적 오류(Fatal)와 일시적 오류(Transient)의 격리

### 결정
오류 유형을 명확히 구분하여 불필요한 재연결 및 서비스 재시작 루프를 차단합니다:

- **일시적 네트워크 오류 (Transient)**: `GoAway`, WebSocket 연결 끊김, 타임아웃 등 → 지수 백오프(Exponential Backoff) 재연결 및 자동 복구 루프 수행.
- **치명적 설정/스키마 오류 (Non-retryable)**: `ValueError`, `TypeError` (잘못된 LiveConnectConfig, 지원되지 않는 SDK 필드 등) → **즉시 FAILED 상태로 전이**하고 재시도 및 자동 서비스 재시작을 중단합니다.

---

## 4. 모델 라이프사이클 및 2단계 폴백 캐스케이드 (Model Selection & Fallback)

### 정의
- **호환 모델 (Compatible)**: 경량 라이브 핸드셰이크(`verify_model_compatibility`) 연결에 성공한 모델.
- **검증 모델 (Verified / Last Known Good)**: 실제 예배 세션에서 정상적으로 번역 결과(텍스트/오디오)를 전달한 모델. 검증된 모델만 `var/runtime/model_state.json`에 LKG로 저장됩니다.

### 폴백 캐스케이드
버전 번호만으로 호환성을 맹신하지 않고 실제 연결 가능 여부에 따라 자동 전환합니다:

```text
선호 모델 (preferred_model)
     │
     ├─ 성공 ──► 세션 연결 (LOCKED)
     │
     └─ 실패
          ▼
   최근 검증 모델 (Last Known Good / LKG)
          │
          ├─ 성공 ──► 세션 연결 (LOCKED)
          │
          └─ 실패
               ▼
        설정된 기본 모델 (fallback_model)
```

- 운영자가 드롭다운에서 모델을 변경하면 즉시 `preferred_model`로 저장됩니다.
- 한 번 연결이 수립되면 해당 예배 시간 동안 모델이 고정(`locked_model`)되며, 세션 중 재연결이나 일시정지 후 재개 시에도 동일 모델을 유지합니다.

---

## 5. 자동 언어 이탈 감시 (Language Drift Watchdog)

### 결정
1. **완료 턴(Completed Turn) 단위 검사**: 부분 스트리밍 조각이 아닌 완성된 발화 문장 단위로 언어 코드를 판별합니다.
2. **신호 우선순위**:
   - 1차 신호: Gemini가 응답에 반환하는 BCP-47 `language_code` (`ko`/`en` $\to 0$, 기타 언어 $\to +1$, 비영어 출력 $\to +2$).
   - 2차 신호: `language_code`가 누락된 경우에 한해 문자셋 휴리스틱(히라가나/가타카나/태국어 등)을 보조 활용.
3. **기본값 OFF (안전 모드)**:
   - 자동 리셋은 기본적으로 OFF(`auto_drift_correction: false`)이며, 수동 **`Pause` → `Resume`**이 가장 안전하고 권장되는 복구 수단입니다.
   - 운영자가 상태 카드에서 필요 시 런타임에만 일시적으로 ON/OFF를 전환할 수 있습니다.

---

## 6. Public HTTPS, Named Cloudflare Tunnel & PublicHostGuard 보안 경계 (Release v3.0.0)

### 1. 포트 8080 영구 표준화
- Windows 환경에서 포트 80은 IIS, Apache, Docker, VMware, 대학 전산망 백그라운드 서비스와의 충돌 가능성이 높습니다.
- 포트 8080을 표준으로 고정(`config.yaml`, `app/config.py`, `app/tunnel.py`, `check_skc_live.bat`, Cloudflare Tunnel ingress)하여 충돌 위험을 원천 제거하고, 로컬 Wi-Fi(`http://skc.local:8080`) 및 공인 HTTPS(`https://live.starkvillekoreanchurch.org`) 양쪽 모두에서 일관된 동작을 보장합니다.

### 2. PublicHostGuardMiddleware 기본 거부 (Default-Deny) 원칙
- 공인 Cloudflare 터널 도메인으로 들어오는 모든 HTTP 요청은 `PublicHostGuardMiddleware`를 통과합니다.
- **공개 허용**: `/live`, `/stream`, `/audio-stream`, `/ws/telemetry`, `/logo*`, `/static/*`.
- **자동 리다이렉트**: 공인 도메인의 루트(`/`) 접속 시 참석자 화면(`/live`)으로 자동 rewrite.
- **원천 차단 (404)**: `/admin`, `/api/devices`, `/api/start`, `/api/stop`, `/api/pause`, `/api/resume`, `/api/qr.png` 등 모든 관리자 및 제어 라우트는 공인 도메인 유입 시 404를 반환하여 외부에서의 공격 표면(Attack Surface)을 0으로 만듭니다.

### 3. 암호학적 운영자 인증 (HMAC-SHA256 Session Cookies)
- 운영자 기능 보호를 위해 `.env`의 `SKC_OPERATOR_PASSWORD` 기반 HMAC-SHA256 서명 세션 쿠키(`HttpOnly`, `SameSite=Strict`)를 구현했습니다.

### 4. 텔레메트리 지연 시간 분해 모델
- 체감 지연 시간을 세 단계로 분해하여 투명하게 모니터링합니다:
  $$\text{공용 체감 지연 (E2E)} = \text{Gemini AI 처리 지연 (Turn-Onset)} + \text{네트워크 RTT} + 200\text{ms (재생 버퍼)}$$
- 운영자 콘솔에서는 세부 RTT 및 지연 수치를 접이식 아코디언에 보관하고, 대시보드에는 핵심 지표(`공용 체감 지연 ~1.1s`)를 3x2 그리드로 컴팩트하게 노출합니다.

### 5. 병렬 멀티스레드 빌드 파이프라인
- `build_parallel.py`를 통해 `SKC_translation.spec`과 `SKC_setup.spec`을 멀티코어 환경에서 병렬 실행하여 바이너리 패키징 시간을 50% 단축하고, 콘솔에 실시간 진행 마일스톤과 실제 경과 시간(Wall-clock time)을 출력합니다.
