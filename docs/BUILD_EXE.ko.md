# 단일 실행 파일 빌드 기록 / Single Executable Build Log
### 실시간 예배 번역 시스템 / Live Translation System

> **English version**: [BUILD_EXE.en.md](BUILD_EXE.en.md)

단일 `.exe` 빌드 과정에서 시도하고 실패하고 성공한 내용을 기록합니다. 재빌드 시 참고하세요.

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
    --workpath .agent\scratch\exe\build ^
    --distpath .agent\scratch\exe\dist
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
