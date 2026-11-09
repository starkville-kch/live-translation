# 단일 운영자 단순화: 제거 전 사용처

2026-11-08 인계문에 따른 작업 범위입니다. 줄 번호를 포함한 전체 검색 결과는 `.agent/archive/2026-11-08-worship-review/single_operator/dependencies-before-removal.txt`에 보관했습니다. 기존 예배 로그와 전사 자료는 삭제하지 않습니다.

| 제거 대상 | 실행 코드와 설정 사용처 | 화면 사용처 | 검증·문서 사용처 |
|---|---|---|---|
| 음악/발화 분류와 챕터 | `app/audio_classifier.py`, `app/chapters.py`, `app/translation_manager.py`, `app/server.py`의 분류 상태·세션 트랙·챕터 파일·현재/캐시/보관 API | `app/templates/operator.html`, `app/static/js/operator.js`, `app/static/css/operator.css` | `tests/test_audio_classifier.py`, `tests/test_chapters.py`, `docs/PLAN.md`, `how_to_use.html`, 이전 `CHANGELOG.md`·`docs/WALKTHROUGH.md` 기록 |
| 어휘 힌트·추천·주석 | `app/glossary.py`, `config/glossary.yaml`, `app/gemini_session.py`, `app/broadcast.py`, `app/translation_manager.py`, `app/server.py`의 glossary API·정지 후 검토용 보관, `SKC_translation.spec` | `operator/_glossary_modal.html`, `operator.html`, `operator.js` | `tests/test_glossary_adaptation.py`, `test_anti_contamination.py`, `test_drift_recovery.py`, `README.md`, `how_to_use.html`, `docs/PLAN.md`, `STACK.md`, `TECHNICAL.md` |
| 입력 언어 선택·자동 감지 | `app/config.py`의 불러오기·검증·저장, `app/languages.py`의 입력 언어 파싱, `GeminiSession`의 힌트·이탈 허용 범위·로그·전사 라벨, `TranslationManager`의 입력 검증·출력 충돌·용어집 선택, `server.py`의 시작/언어 API·상태·내보내기, `config.yaml`, `config/config.template.yaml` | `_language_targets.html`, `operator.js`, `operator.css` | `test_translation_config.py`, `test_translation_manager.py`, `test_gemini_multilingual.py`, `test_operator_multilingual.py`, `test_operator_ui.py`, `test_server_multilingual_routes.py`, `test_multi_target_logging.py`, `test_multilingual_edge_cases.py`, `test_attendee_multilingual.py`, `test_languages.py`, 위 용어집·이탈 테스트와 관련 문서 |
| 자동 언어 이탈 리셋 | `gemini_session.py`의 감시·리셋 호출, `translation_manager.py`의 전파, `server.py`의 두 설정 API·상태, `config.py`의 관련 설정 | `_status_telemetry.html`, `operator.js`, `operator.css` | `test_drift_recovery.py`, `test_anti_contamination.py`, `test_gemini_multilingual.py`, 운영 안내 문서 |
| 실행 중 모델 카탈로그·폴백 | `app/model_resolver.py`, `gemini_session.py`, `server.py`, `main.py`, `config.py`, `config.yaml`, 설정 템플릿 | `_status_telemetry.html`, `operator.js`의 선택·알림·잠금 | `test_model_resolver.py`, `test_public_host_guard.py`, `test_anti_contamination.py`, 빌드·기술 문서 |

입력 언어 선택은 모델 힌트뿐 아니라 잘못된 언어의 허용 집합, 출력 언어와의 충돌 검사, 용어집 적용, 상태·전사 라벨에도 사용되고 있었습니다. 첫 단순화에서는 `translation.expected_source_languages: [ko, en]`를 로컬 출력 보호에 남겼으나, 2026-11-09에 다국어 사용을 막는 제한을 제거했습니다. 현재는 이 설정을 무시하며 출력 언어만 선택합니다. 입력과 같은 출력 언어도 차단하지 않습니다.

`setup_gui.py`의 `client.models.list()`는 초기 설정의 연결 검사에서만 실행되며 실행 프로그램의 시작 카탈로그 검색과 별개입니다. 인계문의 실행 시 검색 제거 범위를 넘어서므로 그대로 두고 9번 감사 표에서 다룹니다. 초기 설정은 `gemini_model()`로 설정 모델명을 읽으므로 해당 함수는 유지합니다.

제거 대상 외 기능의 삭제는 하지 않습니다. 새 고유명사 치환은 별도 `config/name_corrections.yaml`을 읽어 커밋된 자막과 전사에만 적용하며 Google 전송이나 합성 음성을 바꾸지 않습니다. 기존 `.agent/` 예배 증거·과거 인계문은 보존합니다.
