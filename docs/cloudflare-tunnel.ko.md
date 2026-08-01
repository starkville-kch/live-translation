# Cloudflare HTTPS 터널 운영 가이드 (Cloudflare Tunnel Guide)
### 스파크빌 한인교회(PCA) — 실시간 한영 예배 번역 시스템

본 문서는 Cloudflare Tunnel을 통해 인터넷 참석자에게 보안 HTTPS 접속을 제공하고, 로컬 Wi-Fi 참석자에게 초고속 mDNS 접속을 제공하기 위한 운영 방법, 점검 명령, 장애 조치 및 보안 지침을 다룹니다.

---

## 1. 개요 및 공용 서비스 사양

* **공용 참석자 URL**: `https://live.starkvillekoreanchurch.org/live`
* **공용 호스트네임**: `live.starkvillekoreanchurch.org`
* **Cloudflare 터널 이름**: `skc-live-translation`
* **터널 상태**: Cloudflare Zero Trust 대시보드 기준 `Healthy` 상태 유지 필수
* **게이트웨이 서비스 타입**: `HTTP`
* **로컬 오리진 대상**: `127.0.0.1:8080` (번역 서버 PC 포트 8080)
* **기본 예외 경로 (Catch-All)**: `http_status:404`

> [!IMPORTANT]
> **라우터 포트 포워딩 금지**:
> Cloudflare Tunnel은 번역 PC에서 Cloudflare 엣지 서버로의 아웃바운드(Outbound) 보안 터널을 형성합니다. 공유기 포트 포워딩을 설정하거나 8080 포트를 외부 인터넷에 직접 개방하지 마십시오.

---

## 2. 로컬 LAN 접속 및 이중 QR 아키텍처

* **현장 예배 참석자용 기본 URL**: `http://skc-live.local:8080/live`
* **mDNS/Zeroconf 호스트네임**: 프로그램 시작 시 `skc-live.local`을 동적으로 네트워크에 광고하며, PC의 DHCP IP 변경을 자동으로 추적합니다.
* **운영자 비상 IP**: 로컬 IP 주소(예: `http://<IP>:8080/live`)는 mDNS 불통 시 운영자 비상용이며 `/api/status`에서 동적으로 확인합니다. 임의의 `192.168.x.x` 주소를 영구 주보나 QR 코드 안내문에 하드코딩하지 마십시오.
* **공용 HTTPS의 역할**: 원격 방송 시청자, 셀룰러 데이터(LTE/5G) 사용자, `.local` mDNS를 지원하지 않는 기기 접속용입니다.

---

## 3. 운영 진단 및 헬스 체크 명령

번역 PC 터미널에서 아래 명령을 실행하여 시스템 상태를 점검할 수 있습니다:

```bash
# 1. 로컬 8080 포트 앱 응답 점검
curl -I http://127.0.0.1:8080/live

# 2. 로컬 mDNS 네트워크 응답 점검
curl -I http://skc-live.local:8080/live

# 3. 공용 HTTPS 도메인 터널 응답 점검
curl -I https://live.starkvillekoreanchurch.org/live

# 4. 앱 API 상태 JSON 수신 점검
curl http://127.0.0.1:8080/api/status
```

### 정상 응답 결과
- 모든 `/live` 경로 응답 코드는 **`HTTP 200 OK`**이어야 합니다.
- `/api/status` 결과에서 `"service_running": true` 및 `"tunnel_ready": true`를 확인합니다.

---

## 4. 장애 진단 및 원인 분석 가이드

| 증상 | 원인 (Probable Cause) | 조치 방법 (Action) |
|---|---|---|
| **로컬 점검 실패 (`HTTP 000` / 연결 거부)** | 번역 프로그램이 실행되지 않았거나 8080 포트 점유 중 | `SKC_translation.exe` 또는 `python main.py` 실행 및 로그 확인 |
| **로컬은 정상이나 공용 접속 실패** | `cloudflared` 윈도우 서비스 중지 또는 연결 해제 | 윈도우 서비스 상태 확인 (`sc query cloudflared`) 및 Cloudflare 대시보드 `Healthy` 점검 |
| **공용 DNS 실패 (NXDOMAIN / 도메인 찾을 수 없음)** | Cloudflare에 호스트네임 경로 미등록 또는 DNS 미연결 | Cloudflare Zero Trust 대시보드 → Public Hostnames에서 `live.starkvillekoreanchurch.org` 등록 확인 |
| **공용 접속 시 `502 Bad Gateway`** | `cloudflared` 서비스는 동작 중이나 로컬 8080 포트 응답 없음 | 번역 프로그램이 `http://127.0.0.1:8080/live`에서 수신 중인지 확인 |
| **공용 접속 시 `404 Not Found`** | 경로 매핑 오류 또는 기본 예외 경로에 걸림 | Public Hostnames 설정의 경로(`/live`) 및 기본 Catch-All(`http_status:404`) 상태 확인 |

---

## 5. 프로토콜 접속 스키마 규칙

- **공용 주소 (Public URL)**: 반드시 **`HTTPS`** (`https://live.starkvillekoreanchurch.org/live`)를 사용해야 합니다.
- **로컬 주소 (LAN URL)**: **`HTTP`** (`http://skc-live.local:8080/live`)를 사용합니다.

---

## 6. 운영 체크리스트

### 📋 주일 예배 전 점검 체크리스트
1. 번역 애플리케이션 실행 (`SKC_translation.exe` 또는 `python main.py`).
2. 로컬 점검: `curl -I http://127.0.0.1:8080/live` 실행 후 `HTTP 200` 확인.
3. 로컬 mDNS 점검: 교회 Wi-Fi에 연결된 스마트폰에서 `http://skc-live.local:8080/live` 접속 확인.
4. 공용 HTTPS 점검: `curl -I https://live.starkvillekoreanchurch.org/live` 실행 후 `HTTP 200` 확인.
5. Cloudflare 터널 `skc-live-translation` 상태가 `Healthy`인지 확인.

### 🔄 PC 재부팅 / 네트워크 변경 후 체크리스트
1. mDNS zeroconf 등록(`skc-live.local`) 복구 확인.
2. `cloudflared` 서비스 자동 재연결 및 터널 `Healthy` 상태 확인.
3. 로컬 및 공용 URL 재검증.

---

## 7. 참석자 QR 코드 안내 정책

- 🏛️ **현장 예배 참석자 (Sanctuary Wi-Fi)**:
  - 접속 URL: `http://skc-live.local:8080/live`
  - 특징: 1.6ms 초고속 반응 속도로 성전 Wi-Fi 참석자에게 실시간 자막 제공.
- 📺 **온라인 방송 / 모바일 데이터 시청자**:
  - 접속 URL: `https://live.starkvillekoreanchurch.org/live`
  - 특징: 보안 HTTPS 연결로 유튜브 라이브 시청자 및 LTE/5G 모바일 참석자 접속.

> [!TIP]
> 인쇄용 주보나 안내 화면을 출력하기 전에 반드시 실제 모바일 기기(iOS 및 Android)에서 두 QR 코드를 각각 스캔하여 테스트하십시오.

---

## 8. 보안 규칙 및 정보 보호

- **비밀키 Git 커밋 절대 금지**: Cloudflare 터널 토큰, API 키, JSON 자격 증명, `.env` 파일 또는 보안 정보가 포함된 스크린샷을 Git에 올리지 마십시오.
- **토큰 표기 규격**: 예시 코드 작성 시 반드시 `<CLOUDFLARE_TUNNEL_TOKEN>` 치환자를 사용하십시오.
- **네트워크 정보 은닉**: 사설 IP, Wi-Fi 비밀번호, 공유기 내부망 정보를 공개 문서에 기재하지 마십시오.
