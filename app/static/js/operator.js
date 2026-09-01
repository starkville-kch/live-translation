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
const volSlider         = document.getElementById('vol-slider');
const volLabel          = document.getElementById('vol-label');
const volWrapper        = document.getElementById('volume-wrapper');
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
// UI LANGUAGE SELECTION
// ============================================================
let _currentUiLang = localStorage.getItem('skc_ui_lang') || 'ko';

function getOperatorUiLanguage() {
  return _currentUiLang;
}

function setOperatorUiLanguage(lang) {
  _currentUiLang = (lang === 'en') ? 'en' : 'ko';
  localStorage.setItem('skc_ui_lang', _currentUiLang);
  document.documentElement.setAttribute('lang', _currentUiLang);

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
      updateControlBar({ state: 'starting', service_running: true });
      const idx = parseInt(document.getElementById('device-select').value);
      try {
        await fetch('/api/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ device_index: idx })
        });
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
        updateControlBar({ state: 'failed', service_running: false });
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

async function setDriftCorrection(enabled) {
  try {
    await fetch('/api/config/auto-drift-correction', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled })
    });
    autoDriftCorrectionEnabled = enabled;
    updateDriftUI(autoDriftCorrectionEnabled);
  } catch (err) {
    console.error('Failed to update drift correction:', err);
    updateDriftUI(autoDriftCorrectionEnabled);
  }
}

if (radioDriftManual && radioDriftAuto) {
  radioDriftManual.addEventListener('change', () => setDriftCorrection(false));
  radioDriftAuto.addEventListener('change', () => setDriftCorrection(true));
}

function updateDriftUI(enabled) {
  const descEl = document.getElementById('stat-drift-status');
  const isEn = getOperatorUiLanguage() === 'en';
  if (enabled) {
    if (radioDriftAuto) radioDriftAuto.checked = true;
    if (descEl) {
      descEl.textContent = isEn ? 'Auto-recovers session on language drift' : '잘못된 언어 감지 시 세션 자동 교정';
      descEl.style.color = 'var(--color-success-700)';
      descEl.style.fontWeight = '600';
    }
  } else {
    if (radioDriftManual) radioDriftManual.checked = true;
    if (descEl) {
      descEl.textContent = isEn ? 'Manual correction (Pause → Resume)' : '잘못된 언어 감지 시 수동 교정 (Pause → Resume)';
      descEl.style.color = 'var(--color-text-muted)';
      descEl.style.fontWeight = '400';
    }
  }
}

// ============================================================
// ATTENDEE ACCESS
// ============================================================
function copyPublicLink() {
  const el = document.getElementById('qr-public-url');
  const url = el ? el.textContent.trim() : 'https://live.starkvillekoreanchurch.org/live';
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
  polling = setInterval(async () => {
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
    if (st.telemetry) {
      const t = st.telemetry;
      const gLatEl = document.getElementById('stat-gemini-lat');
      if (gLatEl) gLatEl.textContent = t.gemini_latency_ms ? t.gemini_latency_ms + ' ms' : '—';

      const lRttEl = document.getElementById('stat-local-rtt');
      if (lRttEl) lRttEl.textContent = (t.local_rtt_ms !== null && t.local_rtt_ms !== undefined) ? t.local_rtt_ms + ' ms' : '—';

      const lEstEl = document.getElementById('stat-local-est-e2e');
      if (lEstEl) lEstEl.textContent = t.est_local_delay_s ? '~' + t.est_local_delay_s + ' s' : '—';

      const lListEl = document.getElementById('stat-local-listeners-badge');
      if (lListEl) lListEl.textContent = (t.local_listeners || 0) + '명';

      const pRttEl = document.getElementById('stat-public-rtt');
      if (pRttEl) pRttEl.textContent = (t.public_rtt_ms !== null && t.public_rtt_ms !== undefined) ? t.public_rtt_ms + ' ms' : '—';

      const pEstEl = document.getElementById('stat-public-est-e2e');
      if (pEstEl) pEstEl.textContent = t.est_public_delay_s ? '~' + t.est_public_delay_s + ' s' : '—';

      const pListEl = document.getElementById('stat-public-listeners-badge');
      if (pListEl) pListEl.textContent = (t.public_listeners || 0) + '명';
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
        if (JSON.stringify(currentOpts) !== JSON.stringify(newModels)) {
          sel.innerHTML = '';
          newModels.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            let label = name;
            if (name === m.fallback_model && name === m.preferred_model) {
              label += ' — 기본 · 권장';
            } else if (name === m.fallback_model) {
              label += ' — 기본';
            } else if (name === m.preferred_model) {
              label += ' — 권장';
            } else if (name === m.last_known_good_model) {
              label += ' — 최근 검증됨';
            } else {
              label += ' — 새 모델 · 확인 중';
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
    }

    // Update Attendee Access Card
    const publicLiveLink = st.live_url_public || st.public_attendee_url || st.live_url_primary || 'https://live.starkvillekoreanchurch.org/live';
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
      const volEl = document.getElementById('vol-slider');
      gainNode.gain.value = volEl ? parseFloat(volEl.value) : 0.8;
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

function connectAudio() {
  if (audioWs) {
    try { audioWs.close(); } catch(_) {}
    audioWs = null;
  }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  audioWs = new WebSocket(proto + '//' + location.host + '/audio-stream');
  audioWs.binaryType = 'arraybuffer';
  audioWs.onmessage = (e) => playPCM16(e.data);
  audioWs.onerror = () => {};
  audioWs.onclose = () => {
    audioWs = null;
    if (audioEnabled) setTimeout(connectAudio, 2000);
  };
}

function disconnectAudio() {
  if (audioWs) {
    try { audioWs.close(); } catch(_) {}
    audioWs = null;
  }
}

function enableAudio() {
  ensureAudioCtx();
  audioEnabled = true;
  connectAudio();
  if (btnAudio) {
    btnAudio.classList.remove('off');
    btnAudio.classList.add('on');
    btnAudio.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>
      </svg>
      <span class="text">Playback Enabled</span>
    `;
  }
  if (volWrapper) volWrapper.style.display = 'flex';
  if (modal) modal.classList.add('hidden');
  updateVolLabel();
}

function updateVolLabel() {
  if (volLabel && volSlider) {
    volLabel.textContent = Math.round(parseFloat(volSlider.value) * 100) + '%';
  }
}

const modalOk = document.getElementById('modal-ok');
if (modalOk) modalOk.addEventListener('click', enableAudio);

const modalSkip = document.getElementById('modal-skip');
if (modalSkip) modalSkip.addEventListener('click', () => {
  if (modal) modal.classList.add('hidden');
});

if (btnAudio) {
  btnAudio.addEventListener('click', () => {
    if (audioEnabled) {
      audioEnabled = false;
      disconnectAudio();
      btnAudio.classList.remove('on');
      btnAudio.classList.add('off');
      btnAudio.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
          <line x1="23" y1="9" x2="17" y2="15"/>
          <line x1="17" y1="9" x2="23" y2="15"/>
        </svg>
        <span class="text">Playback Muted</span>
      `;
      if (volWrapper) volWrapper.style.display = 'none';
    } else {
      if (modal) modal.classList.remove('hidden');
    }
  });
}

if (volSlider) {
  volSlider.addEventListener('input', () => {
    if (gainNode) gainNode.gain.value = parseFloat(volSlider.value);
    updateVolLabel();
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
  if (captionEs) captionEs.close();
  captionEs = new EventSource('/stream');
  captionEs.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.kind === 'ping') return;

    if (msg.kind === 'source') {
      const p = getOrCreateLivePair();
      p.koEl.textContent += msg.text;
    } else if (msg.kind === 'update') {
      const p = getOrCreateLivePair();
      p.enEl.textContent = msg.text;
    } else if (msg.kind === 'commit') {
      if (livePair && msg.text) livePair.enEl.textContent = msg.text;
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
// INITIALIZATION
// ============================================================
setOperatorUiLanguage(_currentUiLang);
checkAuth();
loadDevices();
startStatusPoll();
startEventPoll();

