/**
 * operator.js — Operator Console UI Controller
 * ============================================
 * Starkville Korean Church (PCA) — Live Translation System
 */

// ============================================================
// DOM REFERENCES & GLOBAL STATE
// ============================================================
let polling = null;
let captionEs = null;
let hasInitializedAutoStop = false;
let lastEventId = -1;
let userScrolledUp = false;
let eventPollTimer = null;
let lastAutoRestartAttempt = 0;
let lastQrUrl = null;
let _cachedDeviceName = "";
let _isRefreshingDevices = false;
let isTestingModel = false;
let autoDriftCorrectionEnabled = false;
let _serviceRunning = false;
let _paused = false;
let _pauseTimerLocal = 0;
let _pauseTimerInterval = null;

const ORIGINAL_TITLE = document.title || "SKC Live Translation — Operator Console";

const serviceStatusPill = document.getElementById('service-status-pill');
const btnPrimaryAction  = document.getElementById('btn-primary-action');
const btnStop           = document.getElementById('btn-stop');
const btnShutdown       = document.getElementById('btn-shutdown');
const preview           = document.getElementById('preview');
const previewWrap       = document.getElementById('preview-wrap');
const logEl             = document.getElementById('log');
const modal             = document.getElementById('earphone-modal');
const btnAudio          = document.getElementById('btn-audio');
const previewMonitorTargetWrap  = document.getElementById('preview-monitor-target-wrap');
const monitorTargetSelect       = document.getElementById('monitor-target-select');
const audioMonitorLangDisplay   = document.getElementById('audio-monitor-lang-display');
let _monitorTarget              = 'en';
const selDevice         = document.getElementById('device-select');
const btnRefreshDevices = document.getElementById('btn-refresh-devices');
const radioDriftManual  = document.getElementById('drift-manual');
const radioDriftAuto    = document.getElementById('drift-auto');


const SESSION_COLOR = { connected:'ok', reconnecting:'warn', failed:'err', connecting:'warn', stopped:'' };
const AUDIO_COLOR   = { connected:'ok', no_signal:'warn', disconnected:'err', stopped:'' };

function formatDuration(totalSeconds) {
  const m = Math.floor(totalSeconds / 60);
  const s = Math.floor(totalSeconds % 60);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

function fmtRuntime(s) {
  return Math.floor(s/60) + ':' + String(Math.floor(s%60)).padStart(2,'0');
}

function fmtTs(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

function playBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
  } catch (e) {
    console.error("Could not play beep", e);
  }
}

// ============================================================
// AUTHENTICATION
// ============================================================
async function checkAuth() {
  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();
    const modalEl = document.getElementById('auth-modal');
    if (data.auth_enabled && !data.authenticated) {
      if (modalEl) {
        modalEl.classList.remove('hidden');
        modalEl.style.display = 'flex';
      }
      const input = document.getElementById('auth-password');
      if (input) input.focus();
    } else {
      if (modalEl) {
        modalEl.classList.add('hidden');
        modalEl.style.display = 'none';
      }
    }
  } catch {}
}

async function submitAuth() {
  const pwdInput = document.getElementById('auth-password');
  const pwd = pwdInput ? pwdInput.value : '';
  const errMsg = document.getElementById('auth-err-msg');
  const btn = document.getElementById('btn-auth-submit');
  if (btn) btn.disabled = true;
  if (errMsg) errMsg.style.display = 'none';
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd }),
    });
    const data = await res.json();
    if (data.ok) {
      const modalEl = document.getElementById('auth-modal');
      if (modalEl) {
        modalEl.classList.add('hidden');
        modalEl.style.display = 'none';
      }
      if (pwdInput) pwdInput.value = '';
      loadDevices();
      pollEvents();
      startStatusPoll();
    } else {
      if (errMsg) {
        errMsg.textContent = '암호가 일치하지 않습니다.';
        errMsg.style.display = 'block';
      }
    }
  } catch (e) {
    if (errMsg) {
      errMsg.textContent = '로그인 통신 오류가 발생했습니다.';
      errMsg.style.display = 'block';
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ============================================================
// SERVICE CONTROLS
// ============================================================
async function loadDevices(options = {}) {
  const { rescan = false, silent = false } = options;
  const sel = document.getElementById('device-select');
  const btnRefresh = document.getElementById('btn-refresh-devices');

  if (rescan && btnRefresh) {
    btnRefresh.classList.add('spinning');
    btnRefresh.disabled = true;
  }

  if (sel && sel.options && sel.selectedIndex >= 0 && sel.options[sel.selectedIndex]) {
    const currentOpt = sel.options[sel.selectedIndex];
    const txt = currentOpt.textContent || "";
    const match = txt.match(/^\[\d+\]\s*(.*)$/);
    _cachedDeviceName = match ? match[1] : txt;
  }

  try {
    const url = rescan ? '/api/devices?rescan=true' : '/api/devices';
    const [devices, status] = await Promise.all([
      fetch(url).then(r => r.json()).catch(() => []),
      fetch('/api/status').then(r => r.json()).catch(() => null)
    ]);

    if (!sel) return;
    if (!Array.isArray(devices) || devices.length === 0) {
      sel.innerHTML = '<option value="">장치 없음 (No devices found)</option>';
      return;
    }

    sel.innerHTML = devices.map(d => `<option value="${d.index}" data-name="${encodeURIComponent(d.name)}">[${d.index}] ${d.name}</option>`).join('');

    let matchedIndex = -1;
    if (_cachedDeviceName) {
      const matchOpt = Array.from(sel.options).find(opt => {
        const dName = decodeURIComponent(opt.getAttribute('data-name') || '');
        return dName && (dName === _cachedDeviceName || dName.includes(_cachedDeviceName) || _cachedDeviceName.includes(dName));
      });
      if (matchOpt) {
        matchedIndex = parseInt(matchOpt.value);
      }
    }

    if (matchedIndex === -1 && status && status.device_index !== undefined) {
      const optExists = Array.from(sel.options).some(opt => parseInt(opt.value) === status.device_index);
      if (optExists) matchedIndex = status.device_index;
    }

    if (matchedIndex !== -1) {
      sel.value = matchedIndex;
    } else if (sel.options.length > 0) {
      sel.selectedIndex = 0;
      const newIdx = parseInt(sel.value);
      if (!isNaN(newIdx)) {
        fetch('/api/devices/select', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index: newIdx })
        }).catch(() => {});
      }
    }
  } catch (err) {
    console.error('Failed to load audio devices:', err);
  } finally {
    if (btnRefresh) {
      setTimeout(() => {
        btnRefresh.classList.remove('spinning');
        btnRefresh.disabled = false;
      }, 400);
    }
  }
}

if (selDevice) {
  selDevice.addEventListener('change', async () => {
    const idx = parseInt(selDevice.value);
    if (selDevice.options[selDevice.selectedIndex]) {
      const txt = selDevice.options[selDevice.selectedIndex].textContent || "";
      const match = txt.match(/^\[\d+\]\s*(.*)$/);
      _cachedDeviceName = match ? match[1] : txt;
    }
    await fetch('/api/devices/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: idx })
    }).catch(() => {});
  });
}

if (btnRefreshDevices) {
  btnRefreshDevices.addEventListener('click', () => {
    loadDevices({ rescan: true });
  });
}

if (typeof navigator !== 'undefined' && navigator.mediaDevices) {
  if (typeof navigator.mediaDevices.addEventListener === 'function') {
    navigator.mediaDevices.addEventListener('devicechange', () => {
      loadDevices({ rescan: true, silent: true });
    });
  } else {
    navigator.mediaDevices.ondevicechange = () => {
      loadDevices({ rescan: true, silent: true });
    };
  }
}

// ============================================================
// LANGUAGE DRIFT CORRECTION UI
// ============================================================
function updateDriftUI(enabled) {

  const isEn = getOperatorUiLanguage() === 'en';
  const descEl = document.getElementById('stat-drift-status');
  const isKoreanSource = (_expectedSource === 'ko');

  if (radioDriftAuto) {
    radioDriftAuto.disabled = !isKoreanSource;
    if (!isKoreanSource) radioDriftAuto.checked = false;
  }

  if (!isKoreanSource) {
    if (radioDriftManual) radioDriftManual.checked = true;
    if (descEl) {
      descEl.textContent = isEn
        ? 'Auto language recovery is currently available for Korean speech.'
        : '자동 언어 복구는 현재 한국어 발화에만 지원됩니다.';
      descEl.style.color = 'var(--color-text-muted)';
    }
    return;
  }

  if (enabled) {
    if (radioDriftAuto) radioDriftAuto.checked = true;
    if (descEl) {
      descEl.textContent = isEn ? 'Auto recovery on unexpected language drift' : '잘못된 언어 감지 시 세션 자동 리셋 (Clean Reset)';
      descEl.style.color = 'var(--color-navy-900)';
    }
  } else {
    if (radioDriftManual) radioDriftManual.checked = true;
    if (descEl) {
      descEl.textContent = isEn ? 'Manual recovery on unexpected drift (Pause → Resume)' : '잘못된 언어 감지 시 수동 교정 (Pause → Resume)';
      descEl.style.color = 'var(--color-text-muted)';
    }
  }
}

if (radioDriftManual) {
  radioDriftManual.addEventListener('change', () => {
    if (radioDriftManual.checked) {
      autoDriftCorrectionEnabled = false;
      updateDriftUI(false);
      fetch('/api/drift-correction', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_drift_correction: false })
      }).catch(() => {});
    }
  });
}
if (radioDriftAuto) {
  radioDriftAuto.addEventListener('change', () => {
    if (_expectedSource !== 'ko') {
      radioDriftManual.checked = true;
      return;
    }
    if (radioDriftAuto.checked) {
      autoDriftCorrectionEnabled = true;
      updateDriftUI(true);
      fetch('/api/drift-correction', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_drift_correction: true })
      }).catch(() => {});
    }
  });
}

// ============================================================
// UI LANGUAGE SELECTION
// ============================================================

const _serverDefaultUiLang = (document.body && document.body.dataset && document.body.dataset.defaultUiLang) || 'ko';
let _currentUiLang = localStorage.getItem('skc_ui_lang') || _serverDefaultUiLang;

function getOperatorUiLanguage() {
  return _currentUiLang;
}

function setOperatorUiLanguage(lang, syncToServer = false) {
  _currentUiLang = (lang === 'en') ? 'en' : 'ko';
  localStorage.setItem('skc_ui_lang', _currentUiLang);
  document.documentElement.setAttribute('lang', _currentUiLang);

  if (syncToServer) {
    fetch('/api/config/ui-language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ default_ui_language: _currentUiLang })
    }).catch(() => {});
  }

  const btnEn = document.getElementById('btn-ui-en');
  const btnKo = document.getElementById('btn-ui-ko');
  if (btnEn && btnKo) {
    if (_currentUiLang === 'en') {
      btnEn.classList.add('active');
      btnKo.classList.remove('active');
    } else {
      btnKo.classList.add('active');
      btnEn.classList.remove('active');
    }
  }

  if (window._lastStatusSnapshot) {
    updateControlBar(window._lastStatusSnapshot);
  } else {
    updateControlBar({ state: _serviceRunning ? (_paused ? 'paused' : 'running') : 'stopped' });
  }
  updateDriftUI(autoDriftCorrectionEnabled);
  renderAudioButton();
}


function updateControlBar(st) {
  if (!serviceStatusPill) return;
  const statusMain = serviceStatusPill.querySelector('.pill-main');
  const pillSub = document.getElementById('pill-sub');
  const isEn = getOperatorUiLanguage() === 'en';

  const isRunning = Boolean(st && st.service_running);
  const isPaused = Boolean(st && st.paused);
  const stateStr = st ? (st.state || (isRunning ? 'running' : 'stopped')) : 'stopped';
  const sessionStatus = st && st.session ? st.session.status : '';

  if (!isRunning && stateStr === 'stopped') {
    serviceStatusPill.className = 'service-status-pill status-stopped';
    if (statusMain) statusMain.innerHTML = `<span class="status-dot"></span><span class="status-text">${isEn ? '○ Standby (STOPPED)' : '○ 대기 중 (STOPPED)'}</span>`;
    if (pillSub) pillSub.style.display = 'none';

    if (btnPrimaryAction) {
      btnPrimaryAction.className = 'btn-action btn-start';
      btnPrimaryAction.innerHTML = isEn ? '<span>▶ Start Translation</span>' : '<span>▶ 번역 시작 (Start)</span>';
      btnPrimaryAction.disabled = false;
    }
    if (btnStop) {
      btnStop.className = 'btn-action btn-stop';
      btnStop.innerHTML = isEn ? '<span>■ Stop Service</span>' : '<span>■ 서비스 종료 (Stop)</span>';
      btnStop.disabled = true;
    }

    document.title = ORIGINAL_TITLE;
    _serviceRunning = false;
    _paused = false;
    _pauseTimerLocal = 0;
  }
  else if (stateStr === 'starting') {
    serviceStatusPill.className = 'service-status-pill status-transient';
    if (statusMain) statusMain.innerHTML = `<span class="spinner-icon"></span><span class="status-text">${isEn ? '⟳ Connecting Translation…' : '⟳ 번역 연결 중... (Starting…)'}</span>`;
    if (pillSub) pillSub.style.display = 'none';

    if (btnPrimaryAction) {
      btnPrimaryAction.className = 'btn-action btn-start';
      btnPrimaryAction.innerHTML = isEn ? '<span>⏳ Connecting…</span>' : '<span>⏳ 연결 중… (Starting)</span>';
      btnPrimaryAction.disabled = true;
    }
    if (btnStop) {
      btnStop.className = 'btn-action btn-stop';
      btnStop.innerHTML = isEn ? '<span>■ Stop Service</span>' : '<span>■ 서비스 종료 (Stop)</span>';
      btnStop.disabled = false;
    }

    document.title = ORIGINAL_TITLE;
    _serviceRunning = true;
    _paused = false;
  }
  else if (stateStr === 'stopping') {
    serviceStatusPill.className = 'service-status-pill status-transient';
    if (statusMain) statusMain.innerHTML = `<span class="spinner-icon"></span><span class="status-text">${isEn ? '⟳ Stopping Service…' : '⟳ 번역 종료 중... (Stopping…)'}</span>`;
    if (pillSub) pillSub.style.display = 'none';

    if (btnPrimaryAction) {
      btnPrimaryAction.className = 'btn-action btn-start';
      btnPrimaryAction.innerHTML = isEn ? '<span>⏳ Stopping…</span>' : '<span>⏳ 종료 중…</span>';
      btnPrimaryAction.disabled = true;
    }
    if (btnStop) {
      btnStop.className = 'btn-action btn-stop';
      btnStop.innerHTML = isEn ? '<span>■ Stop Service</span>' : '<span>■ 서비스 종료 (Stop)</span>';
      btnStop.disabled = true;
    }

    document.title = ORIGINAL_TITLE;
  }
  else if (stateStr === 'failed' || sessionStatus === 'failed') {
    serviceStatusPill.className = 'service-status-pill status-failed';
    if (statusMain) statusMain.innerHTML = `<span class="status-text">${isEn ? '⚠ Translation Error (Failed)' : '⚠ 번역 연결 오류 (Failed)'}</span>`;
    if (pillSub) pillSub.style.display = 'none';

    if (btnPrimaryAction) {
      btnPrimaryAction.className = 'btn-action btn-start';
      btnPrimaryAction.innerHTML = isEn ? '<span>▶ Retry</span>' : '<span>▶ 다시 시도 (Retry)</span>';
      btnPrimaryAction.disabled = false;
    }
    if (btnStop) {
      btnStop.className = 'btn-action btn-stop';
      btnStop.innerHTML = isEn ? '<span>■ Stop Service</span>' : '<span>■ 서비스 종료 (Stop)</span>';
      btnStop.disabled = false;
    }

    document.title = ORIGINAL_TITLE;
    if (_serviceRunning && audioEnabled) {
      disableAudio();
    }
    _serviceRunning = false;
    _paused = false;
  }
  else if (isPaused) {
    const pauseSec = (st && typeof st.pause_duration_s === 'number' && st.pause_duration_s > 0)
      ? st.pause_duration_s
      : _pauseTimerLocal;
    const timeStr = formatDuration(pauseSec);

    serviceStatusPill.className = 'service-status-pill status-paused';
    if (statusMain) statusMain.innerHTML = `<span class="status-dot"></span><span class="status-text">${isEn ? `⏸ Paused ${timeStr}` : `⏸ 일시정지 ${timeStr}`}</span>`;

    if (pillSub) {
      pillSub.style.display = 'block';
      if (pauseSec >= 180) {
        pillSub.textContent = isEn ? '⚠ Check translation resumption' : '⚠ 번역 재시작을 확인하세요';
        pillSub.style.color = '#92400e';
        pillSub.style.fontWeight = '700';
      } else {
        pillSub.textContent = isEn ? 'Resume Required' : 'Resume 필요';
        pillSub.style.color = '#92400e';
        pillSub.style.fontWeight = '600';
      }
    }

    if (btnPrimaryAction) {
      btnPrimaryAction.className = 'btn-action btn-resume';
      btnPrimaryAction.innerHTML = isEn ? '<span>▶ Resume Translation</span>' : '<span>▶ 번역 다시 시작 (Resume)</span>';
      btnPrimaryAction.disabled = false;
    }
    if (btnStop) {
      btnStop.className = 'btn-action btn-stop';
      btnStop.innerHTML = isEn ? '<span>■ Stop Service</span>' : '<span>■ 서비스 종료 (Stop)</span>';
      btnStop.disabled = false;
    }

    document.title = isEn ? `⏸ [${timeStr}] Paused — ${ORIGINAL_TITLE}` : `⏸ [${timeStr}] 일시정지 — ${ORIGINAL_TITLE}`;
    if (!_paused && audioEnabled) {
      disableAudio();
    }
    _serviceRunning = true;
    _paused = true;
  }
  else if (sessionStatus === 'reconnecting' || sessionStatus === 'connecting') {
    serviceStatusPill.className = 'service-status-pill status-transient';
    if (statusMain) statusMain.innerHTML = `<span class="spinner-icon"></span><span class="status-text">${isEn ? '⟳ Reconnecting Translation…' : '⟳ 번역 다시 연결 중... (Reconnecting…)'}</span>`;
    if (pillSub) pillSub.style.display = 'none';

    if (btnPrimaryAction) {
      btnPrimaryAction.className = 'btn-action btn-pause';
      btnPrimaryAction.innerHTML = isEn ? '<span>⏳ Reconnecting…</span>' : '<span>⏳ 다시 연결 중…</span>';
      btnPrimaryAction.disabled = true;
    }
    if (btnStop) {
      btnStop.className = 'btn-action btn-stop';
      btnStop.innerHTML = isEn ? '<span>■ Stop Service</span>' : '<span>■ 서비스 종료 (Stop)</span>';
      btnStop.disabled = false;
    }

    document.title = ORIGINAL_TITLE;
    _serviceRunning = true;
    _paused = false;
  }
  else {
    serviceStatusPill.className = 'service-status-pill status-running';
    if (statusMain) statusMain.innerHTML = `<span class="status-dot"></span><span class="status-text">${isEn ? '● Live (RUNNING)' : '● 번역 중 (RUNNING)'}</span>`;
    if (pillSub) pillSub.style.display = 'none';

    if (btnPrimaryAction) {
      btnPrimaryAction.className = 'btn-action btn-pause';
      btnPrimaryAction.innerHTML = isEn ? '<span>⏸ Pause Translation</span>' : '<span>⏸ 번역 일시정지 (Pause)</span>';
      btnPrimaryAction.disabled = false;
    }
    if (btnStop) {
      btnStop.className = 'btn-action btn-stop';
      btnStop.innerHTML = isEn ? '<span>■ Stop Service</span>' : '<span>■ 서비스 종료 (Stop)</span>';
      btnStop.disabled = false;
    }

    document.title = ORIGINAL_TITLE;
    _serviceRunning = true;
    _paused = false;
    _pauseTimerLocal = 0;
  }
}


if (btnPrimaryAction) {
  btnPrimaryAction.addEventListener('click', async () => {
    if (!_serviceRunning) {
      if (_selectedTargets.length === 0) {
        const isEn = getOperatorUiLanguage() === 'en';
        alert(isEn ? 'Please select at least one translation target language.' : '최소 하나 이상의 통역 언어를 선택해야 합니다.');
        return;
      }
      updateControlBar({ state: 'starting', service_running: true });
      const idx = parseInt(document.getElementById('device-select').value);
      try {
        const res = await fetch('/api/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            device_index: idx,
            targets: _selectedTargets,
            expected_source_language: _expectedSource
          })
        });
        const startData = await res.json().catch(() => ({}));
        if (!res.ok || startData.ok === false) {
          throw new Error(startData.message || startData.error || 'Failed to start translation');
        }
        _serviceRunning = true;
        _paused = false;
        _pauseTimerLocal = 0;
        resetPreview();
        const rtEl = document.getElementById('stat-runtime');
        if (rtEl) rtEl.textContent = '—';
        const costEl = document.getElementById('stat-cost');
        if (costEl) costEl.textContent = '—';
        startStatusPoll();
        connectSSE();
      } catch (err) {
        console.error('Failed to start service:', err);
        _serviceRunning = false;
        updateControlBar({ state: 'stopped', service_running: false });
        alert(err.message || String(err));
      }
    } else if (_paused) {
      btnPrimaryAction.disabled = true;
      btnPrimaryAction.textContent = '⏳ 재개 중… (Resuming)';
      try {
        await fetch('/api/resume', { method: 'POST' });
        _paused = false;
        _pauseTimerLocal = 0;
        updateControlBar({ state: 'running', service_running: true, paused: false });
      } catch (err) {
        console.error('Failed to resume service:', err);
        btnPrimaryAction.disabled = false;
      }
    } else {
      btnPrimaryAction.disabled = true;
      btnPrimaryAction.textContent = '⏳ 정지 중… (Pausing)';
      try {
        await fetch('/api/pause', { method: 'POST' });
        _paused = true;
        _pauseTimerLocal = 0;
        updateControlBar({ state: 'running', service_running: true, paused: true, pause_duration_s: 0 });
      } catch (err) {
        console.error('Failed to pause service:', err);
        btnPrimaryAction.disabled = false;
      }
    }
  });
}

if (btnStop) {
  btnStop.addEventListener('click', async () => {
    updateControlBar({ state: 'stopping', service_running: true });
    try {
      await fetch('/api/stop', { method: 'POST' });
    } finally {
      _serviceRunning = false;
      _paused = false;
      _pauseTimerLocal = 0;
      updateControlBar({ state: 'stopped', service_running: false });
      updateLanguageTargets({ service_running: false, paused: false });
      if (captionEs) {
        captionEs.close();
        captionEs = null;
      }
    }

  });
}

if (btnShutdown) {
  btnShutdown.addEventListener('click', async () => {
    const ok = confirm(
      "전체 번역 시스템을 완전히 종료하시겠습니까?\n" +
      "이 작업은 서버 프로그램을 닫으므로 다시 사용하려면 바탕화면의 시작 파일을 실행해야 합니다.\n\n" +
      "Are you sure you want to completely exit the system?\n" +
      "This will close the server program."
    );
    if (!ok) return;

    btnShutdown.disabled = true;
    btnShutdown.textContent = '⏳ 종료 중 (Shutting down…)';
    try {
      await fetch('/api/shutdown', { method: 'POST' });
      document.body.innerHTML = `
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: sans-serif; background: #faf8f5; color: #1a1a1a; padding: 20px; text-align: center;">
          <div style="font-size: 60px; margin-bottom: 20px;">🔌</div>
          <h1 style="font-family: serif; font-size: 24px; margin-bottom: 12px;">시스템이 안전하게 종료되었습니다</h1>
          <p style="color: #4a4a4a; font-size: 15px; max-width: 450px; line-height: 1.6; margin-bottom: 24px;">
            번역 서버 프로그램이 정상 종료되었습니다. 이제 실행 중인 검은색 터미널 창을 닫으셔도 됩니다. 나중에 다시 시작하려면 바탕화면의 시작 배치 파일을 실행하세요.
          </p>
          <h1 style="font-family: serif; font-size: 20px; margin-bottom: 12px; color: #7a7a7a;">System Successfully Terminated</h1>
          <p style="color: #7a7a7a; font-size: 14px; max-width: 450px; line-height: 1.6;">
            The translation server has shut down gracefully. You may now close any remaining terminal windows.
          </p>
        </div>
      `;
    } catch {
      btnShutdown.disabled = false;
      btnShutdown.textContent = '🔴 프로그램 완전 종료 (Exit System)';
    }
  });
}

const autoStopSelect = document.getElementById('auto-stop-select');
if (autoStopSelect) {
  autoStopSelect.addEventListener('change', async (e) => {
    const mins = parseInt(e.target.value);
    try {
      await fetch('/api/config/auto-stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minutes: mins })
      });
    } catch (err) {}
  });
}

// ============================================================
// MODEL RESOLVER & AUTO DRIFT RECOVERY
// ============================================================
const modelSelect = document.getElementById('model-select');
if (modelSelect) {
  modelSelect.addEventListener('change', async (e) => {
    const preferred = e.target.value;
    try {
      await fetch('/api/models/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'manual', preferred_model: preferred })
      });
    } catch (err) {
      console.error('Failed to set preferred model:', err);
    }
  });
}

const btnTestModel = document.getElementById('btn-test-selected-model');
if (btnTestModel) {
  btnTestModel.addEventListener('click', async () => {
    const sel = document.getElementById('model-select');
    const modelName = sel ? sel.value : '';
    if (!modelName || isTestingModel) return;

    isTestingModel = true;
    const statusEl = document.getElementById('stat-model-status');
    if (statusEl) {
      statusEl.textContent = '⏳ Testing...';
      statusEl.style.color = 'var(--color-navy-900)';
      statusEl.title = 'Performing Live Translate handshake with Google servers...';
    }

    try {
      const res = await fetch('/api/models/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelName })
      });
      const data = await res.json();
      if (statusEl) {
        if (data.ok) {
          statusEl.textContent = '✓ Compatible';
          statusEl.style.color = 'var(--color-success-700)';
          statusEl.title = 'Live handshake passed: TranslationConfig & Audio Output confirmed';
        } else {
          statusEl.textContent = '⚠ Failed';
          statusEl.style.color = 'var(--color-error)';
          statusEl.title = data.message || 'Compatibility test failed';
        }
      }
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = '⚠ Error';
        statusEl.style.color = 'var(--color-error)';
        statusEl.title = err.message;
      }
    } finally {
      setTimeout(() => { isTestingModel = false; }, 3000);
    }
  });
}


// ============================================================
// ATTENDEE ACCESS
// ============================================================
function copyPublicLink() {
  const el = document.getElementById('qr-public-url');
  const url = el ? el.textContent.trim() : 'https://live.starkvillekoreanchurch.org';
  if (navigator.clipboard && navigator.clipboard.writeText) {

    navigator.clipboard.writeText(url).then(() => {
      const btn = document.getElementById('btn-copy-public-link');
      if (btn) {
        const orig = btn.innerHTML;
        btn.innerHTML = '✓ Copied!';
        setTimeout(() => btn.innerHTML = orig, 2000);
      }
    }).catch(() => {
      prompt('Copy attendee link:', url);
    });
  } else {
    prompt('Copy attendee link:', url);
  }
}

// Foldable cards (QR & Log)
['qr-toggle', 'log-toggle'].forEach(id => {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener('click', function() {
      this.classList.toggle('open');
      const bodyId = id === 'qr-toggle' ? 'qr-body' : 'log-body';
      const body = document.getElementById(bodyId);
      if (body) body.classList.toggle('hidden');
    });
  }
});

// ============================================================
// STATUS / TELEMETRY
// ============================================================
function toggleTelemetryDetail() {
  const body = document.getElementById('telemetry-body');
  const ch = document.getElementById('telemetry-chevron');
  if (!body) return;
  const isHidden = body.style.display === 'none' || body.style.display === '';
  body.style.display = isHidden ? 'block' : 'none';
  if (ch) ch.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
}

// ============================================================
// EVENT LOG
// ============================================================
if (logEl) {
  logEl.addEventListener('scroll', () => {
    const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 10;
    userScrolledUp = !atBottom;
  });
}

function appendLogEvent(ev) {
  if (!logEl) return;
  const placeholder = logEl.querySelector('.log-placeholder');
  if (placeholder) placeholder.remove();

  const hasDetails = ev.details && Object.keys(ev.details).length > 0;
  const d = document.createElement('div');
  d.className = 'log-entry' + (hasDetails ? ' has-details' : '');

  const mainLine = document.createElement('div');
  mainLine.textContent = fmtTs(ev.ts) + ' ' + ev.icon + ' ' + ev.message + (hasDetails ? ' ▸' : '');
  d.appendChild(mainLine);

  if (hasDetails) {
    const dd = document.createElement('div');
    dd.className = 'log-details';
    for (const [k, v] of Object.entries(ev.details)) {
      const row = document.createElement('div');
      row.textContent = k + ': ' + v;
      dd.appendChild(row);
    }
    d.appendChild(dd);
    d.addEventListener('click', () => dd.classList.toggle('open'));
  }

  logEl.appendChild(d);
  while (logEl.children.length > 50) logEl.removeChild(logEl.firstChild);
  if (!userScrolledUp) logEl.scrollTop = logEl.scrollHeight;
}

async function pollEvents() {
  try {
    const data = await fetch('/api/events?since=' + lastEventId).then(r => r.json());
    if (data.events && data.events.length > 0) {
      data.events.forEach(appendLogEvent);
      lastEventId = data.latest_id;
    }
  } catch {}
}

function startEventPoll() {
  if (eventPollTimer) clearInterval(eventPollTimer);
  pollEvents();
  eventPollTimer = setInterval(pollEvents, 1500);
}

// ============================================================
// CENTRAL STATUS POLLING
// ============================================================
function startStatusPoll() {
  if (polling) clearInterval(polling);
  pollStatus();
  polling = setInterval(pollStatus, 1000);
}

async function pollStatus() {
  let st;
  try {
    const res = await fetch('/api/status');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    st = await res.json();
  } catch (err) {
      const ssAudio = document.getElementById('ss-audio');
      if (ssAudio) ssAudio.className = 'modern-badge status-red';
      const ssGemini = document.getElementById('ss-gemini');
      if (ssGemini) ssGemini.className = 'modern-badge status-red';
      const ssTrans = document.getElementById('ss-translation');
      if (ssTrans) ssTrans.className = 'modern-badge status-red';
      return;
    }

    if (st.auto_restart_attempt !== undefined && st.auto_restart_attempt > 0) {
      if (st.auto_restart_attempt !== lastAutoRestartAttempt) {
        playBeep();
        lastAutoRestartAttempt = st.auto_restart_attempt;
      }
      const card = document.getElementById('status-card');
      if (card) {
        card.style.borderColor = 'var(--color-error)';
        card.style.backgroundColor = 'rgba(163, 59, 59, 0.05)';
      }
    } else {
      lastAutoRestartAttempt = 0;
      const card = document.getElementById('status-card');
      if (card) {
        card.style.borderColor = '';
        card.style.backgroundColor = '';
      }
    }

    if (!hasInitializedAutoStop && st.auto_stop_timeout_min !== undefined) {
      const asEl = document.getElementById('auto-stop-select');
      if (asEl) asEl.value = st.auto_stop_timeout_min;
      hasInitializedAutoStop = true;
    }

    if (st.auto_drift_correction !== undefined && st.auto_drift_correction !== autoDriftCorrectionEnabled) {
      autoDriftCorrectionEnabled = st.auto_drift_correction;
      updateDriftUI(autoDriftCorrectionEnabled);
    }

    const lvlBar = document.getElementById('level-bar');
    if (lvlBar) lvlBar.style.width = st.audio.level + '%';
    const lvlLbl = document.getElementById('level-label');
    if (lvlLbl) {
      lvlLbl.textContent = st.audio.level > 0 ? '레벨: ' + Math.round(st.audio.level) + '%' : '입력 레벨 — 신호 없음';
    }

    const auEl = document.getElementById('stat-audio');
    if (auEl) {
      auEl.textContent = st.audio.status + (st.audio.device ? ' — ' + st.audio.device : '');
      auEl.className = 'sg-val ' + (AUDIO_COLOR[st.audio.status] || '');
    }

    // Telemetry & Latency Breakdown
    const isEn = getOperatorUiLanguage() === 'en';
    if (st.telemetry) {
      const t = st.telemetry;
      const gLatEl = document.getElementById('stat-gemini-lat');
      if (gLatEl) gLatEl.textContent = (t.gemini_latency_ms !== null && t.gemini_latency_ms !== undefined) ? Math.round(t.gemini_latency_ms) + ' ms' : '—';

      const lRttEl = document.getElementById('stat-local-rtt');
      if (lRttEl) lRttEl.textContent = (t.local_rtt_ms !== null && t.local_rtt_ms !== undefined) ? Math.round(t.local_rtt_ms) + ' ms' : '—';

      const lEstEl = document.getElementById('stat-local-est-e2e');
      if (lEstEl) lEstEl.textContent = t.est_local_delay_s ? '~' + t.est_local_delay_s + ' s' : '—';

      const lListEl = document.getElementById('stat-local-listeners-badge');
      const lCount = t.local_listeners || 0;
      if (lListEl) lListEl.textContent = isEn ? `${lCount} ${lCount === 1 ? 'listener' : 'listeners'}` : `${lCount}명`;

      const pRttEl = document.getElementById('stat-public-rtt');
      if (pRttEl) pRttEl.textContent = (t.public_rtt_ms !== null && t.public_rtt_ms !== undefined) ? Math.round(t.public_rtt_ms) + ' ms' : '—';

      const pEstEl = document.getElementById('stat-public-est-e2e');
      if (pEstEl) pEstEl.textContent = t.est_public_delay_s ? '~' + t.est_public_delay_s + ' s' : '—';

      const pListEl = document.getElementById('stat-public-listeners-badge');
      const pCount = t.public_listeners || 0;
      if (pListEl) pListEl.textContent = isEn ? `${pCount} ${pCount === 1 ? 'listener' : 'listeners'}` : `${pCount}명`;
    }


    // Session metrics
    const attEl = document.getElementById('stat-attendees');
    if (attEl) attEl.textContent = st.attendees !== undefined ? st.attendees : 0;
    const recEl = document.getElementById('stat-reconnects');
    if (recEl) recEl.textContent = st.session ? st.session.reconnect_count : 0;
    const capEl = document.getElementById('stat-captions');
    if (capEl) capEl.textContent = st.captions || 0;

    const overallDelayEl = document.getElementById('stat-overall-delay');
    if (overallDelayEl) {
      if (st.telemetry && st.telemetry.est_public_delay_s) {
        overallDelayEl.textContent = '~' + st.telemetry.est_public_delay_s + ' s';
      } else if (st.telemetry && st.telemetry.est_local_delay_s) {
        overallDelayEl.textContent = '~' + st.telemetry.est_local_delay_s + ' s';
      } else if (st.session && st.session.latency_ms) {
        overallDelayEl.textContent = '~' + (st.session.latency_ms / 1000).toFixed(1) + ' s';
      } else {
        overallDelayEl.textContent = '—';
      }
    }

    // Update Model in Status Card
    if (st.models) {
      const m = st.models;
      const statusEl = document.getElementById('stat-model-status');
      const sel = document.getElementById('model-select');

      if (statusEl) {
        if (m.is_fallback) {
          statusEl.textContent = '⚠ Fallback';
          statusEl.style.color = '#c53030';
          statusEl.title = m.fallback_reason || 'Preferred model failed';
        } else if (st.service_running && m.is_locked) {
          statusEl.textContent = '🟢 Active';
          statusEl.style.color = 'var(--color-success-700)';
          statusEl.title = 'Model is actively in use for live translation';
        } else if (!isTestingModel) {
          statusEl.textContent = '✓ Ready';
          statusEl.style.color = 'var(--color-success-700)';
          statusEl.title = 'Model is ready for translation';
        }
      }

      if (sel) {
        sel.disabled = st.service_running || isTestingModel;
        const currentOpts = Array.from(sel.options).map(o => o.value);
        const newModels = m.available_models || [m.fallback_model];
        const isEn = getOperatorUiLanguage() === 'en';
        const langRendered = sel.getAttribute('data-lang-rendered');
        const currentLangKey = isEn ? 'en' : 'ko';
        if (JSON.stringify(currentOpts) !== JSON.stringify(newModels) || langRendered !== currentLangKey || sel.options.length === 0) {
          sel.innerHTML = '';
          sel.setAttribute('data-lang-rendered', currentLangKey);
          newModels.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            let label = name;
            if (name === m.fallback_model && name === m.preferred_model) {
              label += isEn ? ' — Default · Recommended' : ' — 기본 · 권장';
            } else if (name === m.fallback_model) {
              label += isEn ? ' — Default' : ' — 기본';
            } else if (name === m.preferred_model) {
              label += isEn ? ' — Recommended' : ' — 권장';
            } else if (name === m.last_known_good_model) {
              label += isEn ? ' — Last Verified' : ' — 최근 검증됨';
            } else {
              label += isEn ? ' — New Model' : ' — 새 모델 · 확인 중';
            }
            opt.textContent = label;
            if (name === m.preferred_model || name === m.active_model) opt.selected = true;
            sel.appendChild(opt);
          });
        } else if (document.activeElement !== sel) {
          const targetVal = m.preferred_model || m.active_model;
          if (targetVal && sel.value !== targetVal) {
            sel.value = targetVal;
          }
        }
      }
    }

    if (st.church) {
      if (st.church.name) {
        document.querySelectorAll('.title-full').forEach(el => el.textContent = st.church.name);
      }
      if (st.church.short_name) {
        document.querySelectorAll('.title-short').forEach(el => el.textContent = st.church.short_name);
      }
      if (st.church.default_ui_language && !localStorage.getItem('skc_ui_lang')) {
        setOperatorUiLanguage(st.church.default_ui_language, false);
      }
    }


    // Update Attendee Access Card
    const publicLiveLink = st.live_url_public || st.public_attendee_url || st.live_url_primary || 'https://live.starkvillekoreanchurch.org';
    const localLiveLink = st.live_url_local || st.live_url_fallback || 'http://skc.local:8080/live';


    const qrPubUrlEl = document.getElementById('qr-public-url');
    if (qrPubUrlEl) {
      qrPubUrlEl.textContent = publicLiveLink;
      qrPubUrlEl.href = publicLiveLink;
    }

    const btnOpenAtt = document.getElementById('btn-open-attendee');
    if (btnOpenAtt) btnOpenAtt.href = publicLiveLink;

    const qrLocUrlEl = document.getElementById('qr-local-url');
    if (qrLocUrlEl) {
      qrLocUrlEl.textContent = localLiveLink;
      qrLocUrlEl.href = localLiveLink;
    }

    const qrPubStatusEl = document.getElementById('qr-public-status');
    if (qrPubStatusEl) {
      if (st.tunnel_ready) {
        qrPubStatusEl.textContent = '✓ Public HTTPS ready';
        qrPubStatusEl.style.color = 'var(--color-success)';
      } else if (st.public_https_status === 'reconnecting') {
        qrPubStatusEl.textContent = '🟡 Public link connecting…';
        qrPubStatusEl.style.color = 'var(--color-gold-500)';
      } else {
        qrPubStatusEl.textContent = '⚠️ Public link offline (Local Wi-Fi ready)';
        qrPubStatusEl.style.color = 'var(--color-gold-500)';
      }
    }

    const qrRttEl = document.getElementById('qr-rtt-stat');
    if (qrRttEl && st.telemetry) {
      const pubRtt = st.telemetry.public_rtt_ms !== undefined ? st.telemetry.public_rtt_ms + 'ms' : '—';
      const locRtt = st.telemetry.local_rtt_ms !== undefined ? st.telemetry.local_rtt_ms + 'ms' : '—';
      qrRttEl.textContent = `RTT: Public ${pubRtt} · Local ${locRtt}`;
    }

    const qrImgEl = document.getElementById('qr-img');
    if (qrImgEl && st.live_url_primary && st.live_url_primary !== lastQrUrl) {
      lastQrUrl = st.live_url_primary;
      qrImgEl.src = '/api/qr.png?v=' + Date.now();
    }

    if (st.service_running) {
      const rtEl = document.getElementById('stat-runtime');
      if (rtEl) rtEl.textContent = fmtRuntime(st.runtime_s);
      const costEl = document.getElementById('stat-cost');
      if (costEl) costEl.textContent = '$' + st.cost_usd.toFixed(4);
    }

    function ssSet(id, statusClass) {
      const el = document.getElementById(id);
      if (el) el.className = 'modern-badge ' + statusClass;
    }
    const audioMap = {connected:'status-green', no_signal:'status-yellow', disconnected:'status-red', stopped:'status-blue'};
    ssSet('ss-audio', audioMap[st.audio.status] || 'status-blue');
    const geminiMap = {connected:'status-green', reconnecting:'status-yellow', connecting:'status-yellow', failed:'status-red', stopped:'status-blue'};
    if (st.paused) {
      ssSet('ss-gemini', 'status-yellow');
    } else {
      ssSet('ss-gemini', geminiMap[st.session.status] || 'status-blue');
    }
    if (st.state === 'running' && st.session.status === 'connected' && !st.paused) ssSet('ss-translation', 'status-green');
    else if (st.state === 'starting' || st.paused) ssSet('ss-translation', 'status-yellow');
    else if (st.state === 'failed' || st.session.status === 'failed') ssSet('ss-translation', 'status-red');
    else ssSet('ss-translation', 'status-blue');

    const publicMap = {available:'status-green', reconnecting:'status-yellow', unavailable:'status-red'};
    if (st.tunnel_ready) {
      ssSet('ss-public', 'status-green');
    } else if (st.public_https_status) {
      ssSet('ss-public', publicMap[st.public_https_status] || 'status-blue');
    } else {
      ssSet('ss-public', 'status-blue');
    }

    _serviceRunning = Boolean(st.service_running);
    _paused = Boolean(st.paused);
    updateControlBar(st);
    updateLanguageTargets(st);
  }, 1000);
}


// ============================================================
// AUDIO ENGINE & PLAYBACK
// ============================================================
let audioCtx = null, gainNode = null, audioEnabled = false, audioWs = null, nextPlayAt = 0;
const SAMPLE_RATE = 24000;

function ensureAudioCtx() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      try {
        audioCtx = new AudioContextClass({ sampleRate: SAMPLE_RATE });
      } catch (e) {
        audioCtx = new AudioContextClass();
      }
      gainNode = audioCtx.createGain();
      gainNode.gain.value = 1.0;
      gainNode.connect(audioCtx.destination);
      nextPlayAt = audioCtx.currentTime;
    }
  }
  if (audioCtx) {
    if (audioCtx.state === 'suspended') audioCtx.resume();
    try {
      const silentBuf = audioCtx.createBuffer(1, 1, 22050);
      const src = audioCtx.createBufferSource();
      src.buffer = silentBuf;
      src.connect(gainNode || audioCtx.destination);
      src.start(0);
    } catch (_) {}
  }
}

function playPCM16(arrayBuffer) {
  if (!audioEnabled || !audioCtx || !arrayBuffer || arrayBuffer.byteLength === 0) return;
  if (audioCtx.state === 'suspended') audioCtx.resume();
  const raw = new Int16Array(arrayBuffer);
  const buf = audioCtx.createBuffer(1, raw.length, SAMPLE_RATE);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < raw.length; i++) ch[i] = raw[i] / 32768;
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(gainNode || audioCtx.destination);
  const now = audioCtx.currentTime;
  if (nextPlayAt < now) nextPlayAt = now + 0.05;
  src.start(nextPlayAt);
  nextPlayAt += buf.duration;
}

function getTargetLanguageName(code, uiLang) {
  const c = (code || 'en').toLowerCase().trim();
  const KO_NAMES = {
    en: '영어',
    uk: '우크라이나어',
    zh: '중국어',
    es: '스페인어',
    ko: '한국어',
    vi: '베트남어',
    ja: '일본어',
    ru: '러시아어',
    fr: '프랑스어',
    de: '독일어'
  };
  if (uiLang === 'ko' && KO_NAMES[c]) {
    return KO_NAMES[c];
  }
  const info = _catalogMap ? _catalogMap.get(c) : null;
  if (info) return info.name;
  return c.toUpperCase();
}

function renderAudioButton() {
  if (!btnAudio) return;
  const isEn = getOperatorUiLanguage() === 'en';
  const targetCode = _monitorTarget || 'en';
  const info = _catalogMap ? _catalogMap.get(targetCode) : null;
  const displayName = info ? (info.native_name === info.name ? info.name : `${info.native_name} (${info.name})`) : targetCode.toUpperCase();
  const nameKo = getTargetLanguageName(targetCode, 'ko');
  const nameEn = getTargetLanguageName(targetCode, 'en');

  const wrap = document.getElementById('preview-monitor-target-wrap');
  const isSelectorVisible = wrap && wrap.style.display !== 'none';

  if (audioEnabled) {
    btnAudio.className = 'btn-playback-mini on';
    btnAudio.title = `${displayName} monitoring active — click to stop`;
    btnAudio.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>
      </svg>
      <span class="text">
        <span data-lang="ko">🔊 정지</span>
        <span data-lang="en">🔊 Stop</span>
      </span>
    `;
    if (volWrapper) volWrapper.style.display = 'inline-flex';
  } else {
    btnAudio.className = 'btn-playback-mini off';
    btnAudio.title = `Click to spot-check ${displayName} audio`;
    const labelKo = isSelectorVisible ? '🎧 듣기' : `🎧 ${nameKo} 듣기`;
    const labelEn = isSelectorVisible ? '🎧 Listen' : `🎧 Listen to ${nameEn}`;
    btnAudio.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 18v-6a9 9 0 0 1 18 0v6"/>
        <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>
      </svg>
      <span class="text">
        <span data-lang="ko">${labelKo}</span>
        <span data-lang="en">${labelEn}</span>
      </span>
    `;
    if (volWrapper) volWrapper.style.display = 'none';
  }
}

function updateAudioMonitorDisplay() {
  const displayEl = document.getElementById('audio-monitor-lang-display');
  if (displayEl) {
    const targetCode = _monitorTarget || 'en';
    const info = _catalogMap ? _catalogMap.get(targetCode) : null;
    const displayName = info ? (info.native_name === info.name ? info.name : `${info.native_name} (${info.name})`) : targetCode.toUpperCase();
    displayEl.textContent = displayName;
  }
  renderAudioButton();
}

function setMonitorTarget(newTarget) {
  if (!newTarget) return;
  const isChanged = (newTarget !== _monitorTarget);
  _monitorTarget = newTarget;

  if (monitorTargetSelect && monitorTargetSelect.value !== newTarget) {
    monitorTargetSelect.value = newTarget;
  }

  // 1. Auto-mute active audio (safe switching)
  if (audioEnabled) {
    disableAudio();
  }

  // 2. Update Audio Monitor label & button
  updateAudioMonitorDisplay();

  // 3. If changed, preserve history, commit in-flight turn, and rebind SSE preview to new target
  if (isChanged) {
    if (livePair) {
      commitLivePair(null);
    }
    const info = _catalogMap ? _catalogMap.get(newTarget) : null;
    const displayName = info ? (info.native_name === info.name ? info.name : `${info.native_name} (${info.name})`) : newTarget.toUpperCase();
    const isEn = getOperatorUiLanguage() === 'en';

    // Insert an unobtrusive switch marker if previous history exists
    if (preview && pairs.length > 0) {
      const divider = document.createElement('div');
      divider.className = 'preview-pair';
      divider.style.padding = '4px 8px';
      divider.style.background = 'transparent';
      divider.style.border = 'none';
      divider.style.textAlign = 'center';
      divider.innerHTML = `<span style="font-size: 11px; color: var(--color-text-muted); font-style: italic;">— ${isEn ? 'Monitor language switched to' : '모니터 언어 변경:'} <b>${displayName}</b> —</span>`;
      preview.appendChild(divider);
      if (previewWrap) previewWrap.scrollTop = previewWrap.scrollHeight;
    }

    connectSSE();
  }

  // 4. Mark currently monitored target in Today's Translation Languages
  if (window._lastStatusSnapshot) {
    updateLanguageTargets(window._lastStatusSnapshot);
  }
}

function updateMonitorTargetUI(targets, primaryTarget) {
  const wrap = document.getElementById('preview-monitor-target-wrap');
  const sel = document.getElementById('monitor-target-select');
  if (!wrap || !sel) return;

  const list = Array.isArray(targets) && targets.length > 0 ? targets : (primaryTarget ? [primaryTarget] : ['en']);

  if (list.length <= 1) {
    wrap.style.display = 'none';
    const soleTarget = list[0] || primaryTarget || 'en';
    if (_monitorTarget !== soleTarget) {
      setMonitorTarget(soleTarget);
    } else {
      updateAudioMonitorDisplay();
    }
    return;
  }

  wrap.style.display = 'inline-flex';

  const currentOptions = Array.from(sel.options).map(o => o.value);
  const isSame = currentOptions.length === list.length && currentOptions.every((val, i) => val === list[i]);

  if (!isSame) {
    sel.innerHTML = '';
    list.forEach(code => {
      const info = _catalogMap ? _catalogMap.get(code) : null;
      const displayName = info ? (info.native_name === info.name ? info.name : `${info.native_name} (${info.name})`) : code.toUpperCase();
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = displayName;
      sel.appendChild(opt);
    });
  }

  if (!list.includes(_monitorTarget)) {
    const defaultTarget = list.includes(primaryTarget) ? primaryTarget : list[0];
    setMonitorTarget(defaultTarget);
  } else {
    sel.value = _monitorTarget;
    updateAudioMonitorDisplay();
  }
}

function connectAudio() {
  disconnectAudio();
  if (!audioEnabled || !_serviceRunning || _paused) {
    return;
  }
  const currentActive = window._lastStatusSnapshot?.translation?.active_targets || _selectedTargets || [];
  if (!currentActive.includes(_monitorTarget)) {
    console.log('[Audio] Monitor target', _monitorTarget, 'is not currently active; suppressing WebSocket');
    return;
  }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const target = _monitorTarget || 'en';
  const url = proto + '//' + location.host + '/audio-stream?lang=' + encodeURIComponent(target);
  const ws = new WebSocket(url);
  audioWs = ws;
  ws.binaryType = 'arraybuffer';
  ws.onmessage = (e) => {
    if (audioWs !== ws) return;
    playPCM16(e.data);
  };
  ws.onerror = () => {};
  ws.onclose = (ev) => {
    if (audioWs === ws) {
      audioWs = null;
    }
    // Do NOT retry if connection was rejected (e.g. 1008 policy/inactive)
    if (ev && (ev.code === 1008 || ev.code === 4003 || ev.code === 4403)) {
      console.warn('[Audio] WebSocket rejected by server:', ev.code, ev.reason);
      disableAudio();
      return;
    }
    const currentActive = window._lastStatusSnapshot?.translation?.active_targets || _selectedTargets || [];
    if (audioEnabled && _serviceRunning && !_paused && currentActive.includes(_monitorTarget) && audioWs === null) {
      setTimeout(() => {
        if (audioEnabled && _serviceRunning && !_paused && !audioWs) {
          connectAudio();
        }
      }, 2000);
    }
  };
}

function disconnectAudio() {
  if (audioWs) {
    const ws = audioWs;
    audioWs = null;
    try {
      ws.onopen = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.close();
    } catch (_) {}
  }
}

function enableAudio() {
  ensureAudioCtx();
  audioEnabled = true;
  connectAudio();
  renderAudioButton();
  if (modal) modal.classList.add('hidden');
}

function disableAudio() {
  audioEnabled = false;
  disconnectAudio();
  renderAudioButton();
}

const modalOk = document.getElementById('modal-ok');
if (modalOk) {
  modalOk.addEventListener('click', () => {
    try { sessionStorage.setItem('skc_earphone_accepted', 'true'); } catch(_) {}
    enableAudio();
  });
}

const modalSkip = document.getElementById('modal-skip');
if (modalSkip) {
  modalSkip.addEventListener('click', () => {
    if (modal) modal.classList.add('hidden');
  });
}

if (btnAudio) {
  btnAudio.addEventListener('click', () => {
    if (audioEnabled) {
      disableAudio();
    } else {
      if (!_serviceRunning || _paused) {
        const isEn = getOperatorUiLanguage() === 'en';
        alert(isEn ? 'Translation must be running to monitor audio.' : '통역 서비스가 실행 중일 때만 음성을 모니터링할 수 있습니다.');
        return;
      }
      let accepted = false;
      try { accepted = (sessionStorage.getItem('skc_earphone_accepted') === 'true'); } catch(_) {}
      if (accepted) {
        enableAudio();
      } else {
        if (modal) modal.classList.remove('hidden');
      }
    }
  });
}

if (monitorTargetSelect) {
  monitorTargetSelect.addEventListener('change', () => {
    setMonitorTarget(monitorTargetSelect.value);
  });
}


// ============================================================
// PREVIEW & SSE STREAM
// ============================================================
let pairs = [];
let livePair = null;
const MAX_PAIRS = 50;

function getOrCreateLivePair() {
  if (livePair) return livePair;
  const wrap = document.createElement('div');
  wrap.className = 'preview-pair';
  const koEl = document.createElement('div');
  koEl.className = 'preview-ko';
  const enEl = document.createElement('div');
  enEl.className = 'preview-en live';
  wrap.appendChild(koEl);
  wrap.appendChild(enEl);
  if (preview) preview.appendChild(wrap);
  livePair = { wrap, koEl, enEl };
  return livePair;
}

function commitLivePair(timeStr) {
  if (!livePair) return;
  livePair.enEl.classList.remove('live');
  if (timeStr) {
    const ts = document.createElement('span');
    ts.className = 'preview-ts';
    ts.textContent = '[' + timeStr + '] ';
    livePair.enEl.prepend(ts);
  }
  pairs.push(livePair);
  livePair = null;
  while (pairs.length > MAX_PAIRS) {
    const old = pairs.shift();
    old.wrap.remove();
  }
}

function resetPreview() {
  if (preview) preview.innerHTML = '';
  pairs = [];
  livePair = null;
}

function connectSSE() {
  if (captionEs) {
    try { captionEs.close(); } catch(_) {}
    captionEs = null;
  }
  const streamUrl = _monitorTarget ? `/stream?lang=${encodeURIComponent(_monitorTarget)}` : '/stream';
  captionEs = new EventSource(streamUrl);
  captionEs.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.kind === 'ping') return;

    if (msg.kind === 'source') {
      const p = getOrCreateLivePair();
      p.koEl.textContent += (msg.source || msg.text || '');
    } else if (msg.kind === 'update') {
      const p = getOrCreateLivePair();
      p.enEl.textContent = (msg.target || msg.text || '');
    } else if (msg.kind === 'commit') {
      const p = getOrCreateLivePair();
      if (msg.target || msg.text) p.enEl.textContent = (msg.target || msg.text);
      if (msg.source || msg.ko) p.koEl.textContent = (msg.source || msg.ko);
      commitLivePair(msg.time_str || null);
    } else if (msg.kind === 'unavailable') {
      commitLivePair(null);
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-error)">[번역 불가 / Translation unavailable]</div>';
      if (preview) preview.appendChild(wrap);
    } else if (msg.kind === 'paused') {
      commitLivePair(null);
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-text-muted)">— Paused —</div>';
      if (preview) preview.appendChild(wrap);
    } else if (msg.kind === 'resumed') {
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-success)">— Resumed —</div>';
      if (preview) preview.appendChild(wrap);
    }

    if (previewWrap) previewWrap.scrollTop = previewWrap.scrollHeight;
  };
}


// ============================================================
// TRANSLATION LANGUAGES (PHASE 6)
// ============================================================
let _languagesCatalog = [];
let _catalogMap = new Map();
let _supportedTargets = ['en', 'uk', 'zh'];
let _selectedTargets = ['en'];
let _expectedSource = 'ko';

async function loadLanguageConfiguration() {
  try {
    const res = await fetch('/api/languages');
    if (!res.ok) return;
    const data = await res.json();
    _expectedSource = data.expected_source || 'ko';
    _languagesCatalog = data.available || [];
    _catalogMap = new Map(_languagesCatalog.map(l => [l.code, l]));
    _supportedTargets = data.supported_targets || ['en'];
    _selectedTargets = data.selected_targets || ['en'];

    // Populate Source Language dropdown (Korean and English prioritized on top)
    const srcSelect = document.getElementById('lang-source-select');
    if (srcSelect) {
      srcSelect.innerHTML = '';
      const priorityCodes = ['ko', 'en'];
      const topLangs = priorityCodes.map(code => _catalogMap.get(code)).filter(Boolean);
      const otherLangs = _languagesCatalog
        .filter(l => !priorityCodes.includes(l.code))
        .sort((a, b) => a.name.localeCompare(b.name));

      const sortedForSource = [...topLangs, ...otherLangs];

      sortedForSource.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.code;
        opt.textContent = l.native_name === l.name ? l.name : `${l.native_name} (${l.name})`;
        if (l.code === _expectedSource) opt.selected = true;
        srcSelect.appendChild(opt);
      });


      srcSelect.onchange = () => {
        _expectedSource = srcSelect.value;
        const newSrcInfo = _catalogMap.get(_expectedSource);
        const newSrcName = newSrcInfo ? newSrcInfo.name : _expectedSource.toUpperCase();
        const hadInSelected = _selectedTargets.includes(_expectedSource);

        // DO NOT mutate _supportedTargets!
        // Only remove new source language from today's active selected targets:
        _selectedTargets = _selectedTargets.filter(t => t !== _expectedSource);
        if (_selectedTargets.length === 0) {
          const remainingSupported = _supportedTargets.filter(t => t !== _expectedSource);
          if (remainingSupported.length > 0) {
            _selectedTargets = [remainingSupported[0]];
          }
        }
        saveSelectedTargets();
        renderSelectedTargets();

        if (hadInSelected) {
          showLanguageNotice(
            `원문 언어가 ${newSrcInfo ? newSrcInfo.native_name : newSrcName}(으)로 변경되었습니다. 오늘의 통역 대상에서 제외되었습니다.`,
            `Source changed to ${newSrcName}. It was removed from today's target languages.`
          );
        }
      };
    }

    renderSelectedTargets();
  } catch (e) {
    console.error('Failed to load language configuration:', e);
  }
}

let _langNoticeTimer = null;
function showLanguageNotice(koMsg, enMsg) {
  const hintEl = document.getElementById('lang-targets-hint');
  if (!hintEl) return;
  const isEn = getOperatorUiLanguage() === 'en';
  if (_langNoticeTimer) clearTimeout(_langNoticeTimer);

  hintEl.innerHTML = `<span style="color: var(--color-gold-500); font-weight: 600;">ℹ ${isEn ? enMsg : koMsg}</span>`;
  _langNoticeTimer = setTimeout(() => {
    updateLanguageCountAndHint();
  }, 4500);
}


function renderSelectedTargets() {
  const listEl = document.getElementById('lang-targets-config-list');
  if (!listEl) return;
  listEl.innerHTML = '';
  const isEn = getOperatorUiLanguage() === 'en';

  // Order: selected targets first in current order, followed by remaining supported targets
  const availableSupported = _supportedTargets.filter(c => c !== _expectedSource);
  const orderedCodes = [];
  _selectedTargets.forEach(c => {
    if (availableSupported.includes(c) && !orderedCodes.includes(c)) {
      orderedCodes.push(c);
    }
  });
  availableSupported.forEach(c => {
    if (!orderedCodes.includes(c)) {
      orderedCodes.push(c);
    }
  });

  const primaryTarget = _selectedTargets[0] || orderedCodes[0];

  orderedCodes.forEach(code => {
    const info = _catalogMap.get(code);
    const displayName = info ? (info.native_name === info.name ? info.name : `${info.native_name} (${info.name})`) : code.toUpperCase();
    const isChecked = _selectedTargets.includes(code);
    const isPrimary = isChecked && (code === primaryTarget);

    const item = document.createElement('label');
    item.className = `lang-target-item${isChecked ? ' checked' : ''}${isPrimary ? ' primary' : ''}`;

    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.value = code;
    chk.checked = isChecked;
    chk.dataset.code = code;

    chk.addEventListener('change', () => {
      if (chk.checked) {
        if (!_selectedTargets.includes(code)) {
          _selectedTargets.push(code);
        }
      } else {
        if (_selectedTargets.length <= 1) {
          chk.checked = true;
          return;
        }
        _selectedTargets = _selectedTargets.filter(c => c !== code);
      }
      saveSelectedTargets();
      renderSelectedTargets();
    });

    const labelSpan = document.createElement('span');
    labelSpan.className = 'lang-target-label';
    labelSpan.textContent = displayName;

    item.appendChild(chk);
    item.appendChild(labelSpan);

    if (isPrimary) {
      const primaryBadge = document.createElement('span');
      primaryBadge.className = 'primary-target-badge';
      primaryBadge.innerHTML = `<span data-lang="ko">★ 주 언어</span><span data-lang="en">★ Primary</span>`;
      item.appendChild(primaryBadge);
    } else if (isChecked) {
      const btnMakePrimary = document.createElement('button');
      btnMakePrimary.type = 'button';
      btnMakePrimary.className = 'btn-make-primary';
      btnMakePrimary.title = isEn ? 'Set as primary target language' : '기본 통역 언어로 설정';
      btnMakePrimary.innerHTML = `<span data-lang="ko">주 언어로 설정</span><span data-lang="en">Make Primary</span>`;
      btnMakePrimary.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        _selectedTargets = [code, ..._selectedTargets.filter(c => c !== code)];
        saveSelectedTargets();
        renderSelectedTargets();
      });
      item.appendChild(btnMakePrimary);
    }

    listEl.appendChild(item);
  });

  updateLanguageCountAndHint();
  updateMonitorTargetUI(_selectedTargets, primaryTarget || 'en');
}


function updateLanguageCountAndHint() {
  const count = _selectedTargets.length;
  const countBadge = document.getElementById('lang-target-count-badge');
  const hintEl = document.getElementById('lang-targets-hint');
  const isEn = getOperatorUiLanguage() === 'en';

  if (countBadge) {
    countBadge.textContent = `${count} ${count === 1 ? 'target' : 'targets'}`;
  }

  if (hintEl) {
    hintEl.innerHTML = isEn
      ? `<span>${count} translation ${count === 1 ? 'session' : 'sessions'} will start.</span>`
      : `<span>${count}개 통역 세션이 시작됩니다.</span>`;
  }
}

async function saveSelectedTargets() {
  try {
    const res = await fetch('/api/translation/targets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_source_language: _expectedSource,
        supported_targets: _supportedTargets,
        targets: _selectedTargets
      })
    });
    if (res.status === 409) {
      console.warn('Cannot update targets while translation is running/paused');
      return;
    }
    const data = await res.json();
    if (data.ok && data.translation) {
      _expectedSource = data.translation.expected_source_language || _expectedSource;
      _selectedTargets = data.translation.default_active_targets || _selectedTargets;
      _supportedTargets = data.translation.supported_targets || _supportedTargets;
      updateLanguageCountAndHint();
    }
  } catch (e) {
    console.error('Failed to save translation targets:', e);
  }
}

function updateLanguageTargets(st) {
  const isRunning = Boolean(st && st.service_running);
  const isLocked = isRunning;
  const isEn = getOperatorUiLanguage() === 'en';


  const badgeEl = document.getElementById('lang-panel-badge');
  const srcSelect = document.getElementById('lang-source-select');
  const configList = document.getElementById('lang-targets-config-list');
  const activeList = document.getElementById('lang-targets-active-list');
  const btnManage = document.getElementById('btn-open-manage-langs');
  const hintEl = document.getElementById('lang-targets-hint');
  const countBadge = document.getElementById('lang-target-count-badge');

  if (srcSelect) {
    srcSelect.disabled = isLocked;
  }


  if (isLocked) {
    if (badgeEl) {
      badgeEl.className = 'lang-panel-badge locked';
      badgeEl.innerHTML = isEn ? '<span>🔒 Session active</span>' : '<span>🔒 세션 진행 중</span>';
    }
    if (configList) configList.style.display = 'none';
    if (activeList) activeList.style.display = 'flex';
    if (btnManage) btnManage.disabled = true;

    if (hintEl) {
      hintEl.innerHTML = isEn
        ? '<span>Stop translation to change target languages.</span>'
        : '<span>통역 대상을 변경하려면 서비스를 종료하세요.</span>';
    }

    const translation = st && st.translation ? st.translation : null;
    const activeTargets = translation ? (translation.active_targets || []) : _selectedTargets;
    const primaryTarget = translation ? (translation.primary_target || 'en') : 'en';
    const sessionsMap = translation ? (translation.sessions || {}) : {};
    const telemetryStats = st && st.telemetry ? st.telemetry : {};
    const listenersByTarget = telemetryStats.listeners_by_target || {};

    updateMonitorTargetUI(activeTargets, primaryTarget);

    if (countBadge) {
      countBadge.textContent = `${activeTargets.length} active`;
    }

    if (activeList) {
      activeList.innerHTML = '';
      activeTargets.forEach(tgt => {
        const info = _catalogMap.get(tgt);
        const displayName = info ? (info.native_name === info.name ? info.name : `${info.native_name} (${info.name})`) : tgt.toUpperCase();
        const sess = sessionsMap[tgt] || {};
        const status = sess.status || (isRunning ? 'connected' : 'connecting');
        const latencyMs = (sess.latency_ms !== null && sess.latency_ms !== undefined)
          ? `${Math.round(sess.latency_ms)} ms`
          : ((tgt === translation?.primary_target && st.telemetry?.gemini_latency_ms) ? `${Math.round(st.telemetry.gemini_latency_ms)} ms` : '—');

        const listeners = listenersByTarget[tgt] !== undefined
          ? listenersByTarget[tgt]
          : (tgt === translation?.primary_target ? (st.attendees || 0) : 0);

        let statusClass = 'live';
        let statusLabel = isEn ? 'Live' : '송출 중';
        if (status === 'reconnecting') {
          statusClass = 'warn';
          statusLabel = isEn ? 'Reconnecting' : '재연결 중';
        } else if (status === 'connecting') {
          statusClass = 'warn';
          statusLabel = isEn ? 'Connecting' : '연결 중';
        } else if (status === 'failed') {
          statusClass = 'err';
          statusLabel = isEn ? 'Error' : '오류';
        }

        const isPrimary = (tgt === primaryTarget);
        const isMonitored = (tgt === _monitorTarget);

        const card = document.createElement('div');
        card.className = 'lang-session-card';
        card.innerHTML = `
          <div class="lang-session-header">
            <div class="lang-session-title">
              <span class="status-dot-mini ${statusClass}"></span>
              <span class="lang-session-name">${displayName}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 5px;">
              ${isMonitored ? `<span class="monitor-target-indicator"><span data-lang="ko">👁 모니터</span><span data-lang="en">👁 Monitor</span></span>` : ''}
              ${isPrimary ? `<span class="primary-target-badge" style="font-size: 10px; padding: 1px 5px;"><span data-lang="ko">주 언어</span><span data-lang="en">Primary</span></span>` : ''}
            </div>
          </div>
          <div class="lang-session-sub">
            ${statusLabel} · ${latencyMs} · ${listeners} ${isEn ? (listeners === 1 ? 'listener' : 'listeners') : '명'}
          </div>
        `;
        activeList.appendChild(card);
      });
    }

  } else {
    // Stopped state
    if (badgeEl) {
      badgeEl.className = 'lang-panel-badge ready';
      badgeEl.innerHTML = isEn ? '<span>Ready</span>' : '<span>대기 (Ready)</span>';
    }
    if (configList) configList.style.display = 'flex';
    if (activeList) activeList.style.display = 'none';
    if (btnManage) btnManage.disabled = false;

    updateLanguageCountAndHint();
    updateMonitorTargetUI(_selectedTargets, (st && st.translation && st.translation.primary_target) || 'en');
  }
}


// ── Manage Languages Modal Logic ─────────────────────────────
const manageModal = document.getElementById('manage-langs-modal');
const btnOpenManage = document.getElementById('btn-open-manage-langs');
const btnCloseManage = document.getElementById('btn-close-manage-langs');
const btnCancelManage = document.getElementById('btn-cancel-manage-langs');
const btnSaveManage = document.getElementById('btn-save-manage-langs');
const searchManageInput = document.getElementById('manage-langs-search');
const catalogListEl = document.getElementById('manage-langs-catalog-list');

let _modalSelectedSupported = new Set();

function openManageLanguagesModal() {
  if (!manageModal) return;
  _modalSelectedSupported = new Set(_supportedTargets);
  if (searchManageInput) searchManageInput.value = '';
  renderCatalogModalList('');
  manageModal.classList.remove('hidden');
}

function closeManageLanguagesModal() {
  if (!manageModal) return;
  manageModal.classList.add('hidden');
}

function renderCatalogModalList(searchQuery) {
  if (!catalogListEl) return;
  catalogListEl.innerHTML = '';
  const q = (searchQuery || '').toLowerCase().trim();

  const filtered = _languagesCatalog.filter(l => {
    if (!q) return true;
    return l.name.toLowerCase().includes(q) ||
           l.native_name.toLowerCase().includes(q) ||
           l.code.toLowerCase().includes(q);
  });

  filtered.forEach(l => {
    const item = document.createElement('label');
    item.className = 'catalog-item';

    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.value = l.code;
    chk.checked = _modalSelectedSupported.has(l.code);

    chk.addEventListener('change', () => {
      if (chk.checked) {
        _modalSelectedSupported.add(l.code);
      } else {
        if (_modalSelectedSupported.size <= 1) {
          chk.checked = true;
          return;
        }
        _modalSelectedSupported.delete(l.code);
      }
    });

    const nameSpan = document.createElement('span');
    nameSpan.className = 'catalog-item-name';
    nameSpan.textContent = l.native_name === l.name ? l.name : `${l.native_name} (${l.name})`;

    const codeSpan = document.createElement('span');
    codeSpan.className = 'catalog-item-code';
    codeSpan.textContent = `[${l.code}]`;

    item.appendChild(chk);
    item.appendChild(nameSpan);
    item.appendChild(codeSpan);
    catalogListEl.appendChild(item);
  });
}

if (btnOpenManage) btnOpenManage.addEventListener('click', openManageLanguagesModal);
if (btnCloseManage) btnCloseManage.addEventListener('click', closeManageLanguagesModal);
if (btnCancelManage) btnCancelManage.addEventListener('click', closeManageLanguagesModal);

if (searchManageInput) {
  searchManageInput.addEventListener('input', (e) => {
    renderCatalogModalList(e.target.value);
  });
}

if (btnSaveManage) {
  btnSaveManage.addEventListener('click', async () => {
    const newSupported = Array.from(_modalSelectedSupported);
    if (newSupported.length === 0) return;

    const newlyAdded = newSupported.filter(code => !_supportedTargets.includes(code));
    let newSelected = _selectedTargets.filter(t => newSupported.includes(t) && t !== _expectedSource);
    newlyAdded.forEach(code => {
      if (code !== _expectedSource && !newSelected.includes(code)) newSelected.push(code);
    });
    if (newSelected.length === 0) {
      const avail = newSupported.filter(t => t !== _expectedSource);
      if (avail.length > 0) newSelected = [avail[0]];
    }

    try {
      const res = await fetch('/api/translation/targets', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          supported_targets: newSupported,
          targets: newSelected
        })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && data.translation) {
          _supportedTargets = data.translation.supported_targets;
          _selectedTargets = data.translation.default_active_targets;
          renderSelectedTargets();
          closeManageLanguagesModal();
        }
      }
    } catch (e) {
      console.error('Failed to save supported languages:', e);
    }
  });
}

// ============================================================
// INITIALIZATION
// ============================================================
setOperatorUiLanguage(_currentUiLang);
checkAuth();
loadDevices();
loadLanguageConfiguration();
connectSSE();
startStatusPoll();
startEventPoll();



