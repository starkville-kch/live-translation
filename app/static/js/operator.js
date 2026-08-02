function switchQrTab(mode) {
  const btnLocal = document.getElementById('qr-tab-local');
  const btnPublic = document.getElementById('qr-tab-public');
  const pnlLocal = document.getElementById('qr-panel-local');
  const pnlPublic = document.getElementById('qr-panel-public');
  const imgLocal = document.getElementById('qr-img-local');
  const imgPublic = document.getElementById('qr-img-public');
  const t = Date.now();

  if (mode === 'local') {
    btnLocal.className = 'primary';
    btnPublic.className = 'secondary';
    pnlLocal.style.display = 'block';
    pnlPublic.style.display = 'none';
    if (imgLocal) imgLocal.src = '/api/qr.png?type=local&t=' + t;
  } else {
    btnLocal.className = 'secondary';
    btnPublic.className = 'primary';
    pnlLocal.style.display = 'none';
    pnlPublic.style.display = 'block';
    if (imgPublic) imgPublic.src = '/api/qr.png?type=public&t=' + t;
  }
}


let polling = null;

let captionEs = null;
let hasInitializedAutoStop = false;
let lastEventId = -1;
let userScrolledUp = false;
let eventPollTimer = null;
let lastAutoRestartAttempt = 0;

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

const btnStart = document.getElementById('btn-start');
const btnPause = document.getElementById('btn-pause');
const btnStop  = document.getElementById('btn-stop');
const btnShutdown = document.getElementById('btn-shutdown');
const preview  = document.getElementById('preview');
const previewWrap = document.getElementById('preview-wrap');
const logEl    = document.getElementById('log');

// ── Preview: Korean + English paired DOM ──────────────────────────────────────
// Each "pair" = one committed turn: { koEl, enEl, wrapEl, koText, enText }
let pairs = [];      // committed pairs
let livePair = null; // the currently-accumulating in-progress pair

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
  preview.appendChild(wrap);
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
  // Trim oldest pairs
  while (pairs.length > MAX_PAIRS) {
    const old = pairs.shift();
    old.wrap.remove();
  }
}

function resetPreview() {
  preview.innerHTML = '';
  pairs = [];
  livePair = null;
}

// ── Foldable cards ──────────────────────────────────────────────────────────
['qr-toggle', 'log-toggle'].forEach(id => {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener('click', function() {
      this.classList.toggle('open');
      const bodyId = id === 'qr-toggle' ? 'qr-body' : 'log-body';
      document.getElementById(bodyId).classList.toggle('hidden');
    });
  }
});

// ── Audio engine (24kHz PCM16 mono, via WS /audio-stream) ───────────────────
let audioCtx = null, gainNode = null, audioEnabled = false, audioWs = null, nextPlayAt = 0;
const SAMPLE_RATE = 24000;

function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    gainNode = audioCtx.createGain();
    gainNode.gain.value = parseFloat(document.getElementById('vol-slider').value);
    gainNode.connect(audioCtx.destination);
    nextPlayAt = audioCtx.currentTime;
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
}

function playPCM16(arrayBuffer) {
  if (!audioEnabled || !audioCtx) return;
  const raw = new Int16Array(arrayBuffer);
  const buf = audioCtx.createBuffer(1, raw.length, SAMPLE_RATE);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < raw.length; i++) ch[i] = raw[i] / 32768;
  const src = audioCtx.createBufferSource();
  src.buffer = buf; src.connect(gainNode);
  const now = audioCtx.currentTime;
  if (nextPlayAt < now) nextPlayAt = now + 0.05;
  src.start(nextPlayAt);
  nextPlayAt += buf.duration;
}

function connectAudio() {
  if (audioWs) { audioWs.close(); audioWs = null; }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  audioWs = new WebSocket(proto + '//' + location.host + '/audio-stream');
  audioWs.binaryType = 'arraybuffer';
  audioWs.onmessage = (e) => playPCM16(e.data);
  audioWs.onerror = () => {};
  audioWs.onclose = () => {
    audioWs = null;
    if (audioEnabled) setTimeout(connectAudio, 3000);
  };
}

function disconnectAudio() {
  if (audioWs) { audioWs.close(); audioWs = null; }
}

const modal   = document.getElementById('earphone-modal');
const btnAudio = document.getElementById('btn-audio');
const volSlider = document.getElementById('vol-slider');
const volLabel  = document.getElementById('vol-label');
const volWrapper = document.getElementById('volume-wrapper');

function enableAudio() {
  ensureAudioCtx();
  audioEnabled = true;
  connectAudio();
  btnAudio.textContent = '🔊 Playback Enabled';
  btnAudio.classList.add('on');
  volWrapper.style.display = 'flex';
  modal.classList.add('hidden');
  updateVolLabel();
}

function updateVolLabel() {
  volLabel.textContent = Math.round(parseFloat(volSlider.value) * 100) + '%';
}

const mOk = document.getElementById('modal-ok');
if (mOk) mOk.addEventListener('click', enableAudio);
const mSkip = document.getElementById('modal-skip');
if (mSkip) mSkip.addEventListener('click', () => modal.classList.add('hidden'));

if (btnAudio) {
  btnAudio.addEventListener('click', () => {
    if (audioEnabled) {
      audioEnabled = false;
      disconnectAudio();
      btnAudio.textContent = '🔇 Playback Muted';
      btnAudio.classList.remove('on');
      volWrapper.style.display = 'none';
    } else {
      modal.classList.remove('hidden');
    }
  });
}

if (volSlider) {
  volSlider.addEventListener('input', () => {
    if (gainNode) gainNode.gain.value = parseFloat(volSlider.value);
    updateVolLabel();
  });
}

// ── Device list ──────────────────────────────────────────────────────────────
async function loadDevices() {
  const devices = await fetch('/api/devices').then(r => r.json());
  const status = await fetch('/api/status').then(r => r.json());
  const sel = document.getElementById('device-select');
  if (!sel) return;
  sel.innerHTML = devices.map(d => `<option value="${d.index}">[${d.index}] ${d.name}</option>`).join('');
  if (status && status.device_index !== undefined) {
    sel.value = status.device_index;
  }
  // Hook up change event to persist select index immediately
  sel.addEventListener('change', async () => {
    const idx = parseInt(sel.value);
    await fetch('/api/devices/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: idx })
    });
  });
}

// ── Start ─────────────────────────────────────────────────────────────────────
if (btnStart) {
  btnStart.addEventListener('click', async () => {
    btnStart.disabled = true; btnStart.textContent = '⏳ Starting…';
    const idx = parseInt(document.getElementById('device-select').value);
    try {
      await fetch('/api/start', { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({device_index: idx}) });
      btnPause.disabled = false; btnStop.disabled = false;
      resetPreview();
      const rcEl = document.getElementById('stat-runtime-cost');
      if (rcEl) rcEl.textContent = '—';
      startStatusPoll(); connectSSE();
    } catch {
      btnStart.disabled = false; btnStart.textContent = '▶ Start';
    }
  });
}

// ── Pause / Resume ────────────────────────────────────────────────────────────
let _paused = false;
if (btnPause) {
  btnPause.addEventListener('click', async () => {
    if (_paused) {
      await fetch('/api/resume', {method:'POST'});
      _paused = false; btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning';
    } else {
      await fetch('/api/pause', {method:'POST'});
      _paused = true; btnPause.textContent = '▶ Resume'; btnPause.className = 'primary';
    }
  });
}

// ── Stop ──────────────────────────────────────────────────────────────────────
if (btnStop) {
  btnStop.addEventListener('click', async () => {
    btnStop.disabled = true; btnStop.textContent = '⏳ Stopping…';
    try { await fetch('/api/stop', {method:'POST'}); }
    finally {
      btnStart.disabled = false; btnStart.textContent = '▶ Start';
      btnPause.disabled = true;  btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning';
      btnStop.disabled = true;   btnStop.textContent = '■ Stop';
      _paused = false;
      if (captionEs) { captionEs.close(); captionEs = null; }
    }
  });
}

// ── Shutdown ──────────────────────────────────────────────────────────────────
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

// ── Status poll ───────────────────────────────────────────────────────────────
const SESSION_COLOR = { connected:'ok', reconnecting:'warn', failed:'err', connecting:'warn', stopped:'' };
const AUDIO_COLOR   = { connected:'ok', no_signal:'warn', disconnected:'err', stopped:'' };

function fmtRuntime(s) {
  return Math.floor(s/60) + ':' + String(Math.floor(s%60)).padStart(2,'0');
}

// ── Event log (polls /api/events) ────────────────────────────────────────────
if (logEl) {
  logEl.addEventListener('scroll', () => {
    const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 10;
    userScrolledUp = !atBottom;
  });
}

function fmtTs(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

function appendLogEvent(ev) {
  if (!logEl) return;
  const placeholder = logEl.querySelector('.log-placeholder');
  if (placeholder) placeholder.remove();

  const hasDetails = ev.details && Object.keys(ev.details).length > 0;
  const d = document.createElement('div');
  d.className = 'log-entry' + (hasDetails ? ' has-details' : '');

  const mainLine = document.createElement('div');
  mainLine.textContent = fmtTs(ev.ts) + ' ' + ev.icon + ' ' + ev.message +
                         (hasDetails ? ' ▸' : '');
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
  } catch { /* network error — skip */ }
}

function startEventPoll() {
  if (eventPollTimer) clearInterval(eventPollTimer);
  pollEvents();
  eventPollTimer = setInterval(pollEvents, 1500);
}

function startStatusPoll() {
  if (polling) clearInterval(polling);
  window.addEventListener('beforeunload', () => { if (polling) clearInterval(polling); });
  polling = setInterval(async () => {

    let st;
    try { st = await fetch('/api/status').then(r => r.json()); } catch { return; }

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
      const autoStopEl = document.getElementById('auto-stop-select');
      if (autoStopEl) autoStopEl.value = st.auto_stop_timeout_min;
      hasInitializedAutoStop = true;
    }

    const lvlBar = document.getElementById('level-bar');
    if (lvlBar) lvlBar.style.width = st.audio.level + '%';
    const lvlLbl = document.getElementById('level-label');
    if (lvlLbl) {
      lvlLbl.textContent = st.audio.level > 0 ? '레벨: ' + Math.round(st.audio.level) + '%' : '입력 레벨 — 신호 없음';
    }

    const auEl = document.getElementById('stat-audio');
    const audioDev = st.audio.device || '';
    if (auEl) {
      if (st.audio.status === 'connected' || st.audio.level > 0) {
        auEl.textContent = `🟢 ${audioDev ? audioDev : '마이크'} — 신호 수신 중`;
        auEl.className = 'sg-val sg-wide ok';
      } else if (st.audio.status === 'no_signal') {
        auEl.textContent = `🟡 ${audioDev ? audioDev : '마이크'} — 신호 없음`;
        auEl.className = 'sg-val sg-wide warn';
      } else {
        auEl.textContent = `🔴 마이크 연결 안 됨`;
        auEl.className = 'sg-val sg-wide err';
      }
    }

    const seEl = document.getElementById('stat-session');
    if (seEl) {
      if (st.session.status === 'connected') {
        seEl.textContent = `🟢 연결됨 (Session active)`;
        seEl.className = 'sg-val sg-wide ok';
      } else if (st.session.status === 'reconnecting') {
        seEl.textContent = `🟡 재연결 중 (${st.session.last_event || ''})`;
        seEl.className = 'sg-val sg-wide warn';
      } else if (st.session.status === 'failed') {
        seEl.textContent = `🔴 연결 실패 (${st.session.last_event || ''})`;
        seEl.className = 'sg-val sg-wide err';
      } else {
        seEl.textContent = `⚪ 대기 중`;
        seEl.className = 'sg-val sg-wide';
      }
    }

    if (st.telemetry) {
      const t = st.telemetry;
      const gLatEl = document.getElementById('stat-gemini-lat');
      if (gLatEl) {
        const ms = t.gemini_latency_ms;
        const statusBadge = ms ? (ms < 1000 ? ' 🟢' : ms < 2000 ? ' 🟡' : ' 🔴') : '';
        gLatEl.textContent = ms ? ms + ' ms' + statusBadge : '—';
      }

      const lRttEl = document.getElementById('stat-local-rtt');
      if (lRttEl) {
        const rttStr = t.local_rtt_ms !== null && t.local_rtt_ms !== undefined ? t.local_rtt_ms + ' ms' : '—';
        const estStr = t.est_local_delay_s ? ` (실지연 약 ${t.est_local_delay_s}s)` : '';
        lRttEl.textContent = rttStr + estStr;
      }

      const pRttEl = document.getElementById('stat-public-rtt');
      if (pRttEl) {
        const rttStr = t.public_rtt_ms !== null && t.public_rtt_ms !== undefined ? t.public_rtt_ms + ' ms' : '—';
        const estStr = t.est_public_delay_s ? ` (실지연 약 ${t.est_public_delay_s}s)` : '';
        pRttEl.textContent = rttStr + estStr;
      }
    }

    const attEl = document.getElementById('stat-attendees');
    if (attEl) {
      const locCnt = (st.telemetry && st.telemetry.local_listeners !== undefined) ? st.telemetry.local_listeners : 0;
      const pubCnt = (st.telemetry && st.telemetry.public_listeners !== undefined) ? st.telemetry.public_listeners : 0;
      attEl.textContent = `현장 ${locCnt}명 | 공용 ${pubCnt}명`;
    }

    const rcEl = document.getElementById('stat-runtime-cost');
    if (rcEl) {
      if (st.service_running) {
        rcEl.textContent = `${fmtRuntime(st.runtime_s)} | $${(st.cost_usd || 0).toFixed(4)}`;
      } else {
        rcEl.textContent = '—';
      }
    }

    // Update Dual QR URLs under the QR code card
    const urlsLocal = document.getElementById('qr-urls-local');
    const urlsPublic = document.getElementById('qr-urls-public');

    if (urlsLocal && st.live_url_local) {
      urlsLocal.innerHTML = `
        <div>
          <div style="font-weight:600; color:var(--color-navy-900); margin-bottom: 2px;">기본 로컬 주소 (mDNS):</div>
          <a href="${st.live_url_local}" target="_blank" style="color:var(--color-gold-500); text-decoration:underline;">${st.live_url_local}</a>
        </div>
        <div>
          <div style="font-weight:600; color:var(--color-navy-900); margin-bottom: 2px;">비상 IP 주소 (Fallback IP):</div>
          <a href="${st.live_url_fallback}" target="_blank" style="color:var(--color-text-muted); text-decoration:underline;">${st.live_url_fallback}</a>
        </div>
      `;
    }

    if (urlsPublic) {
      const pubUrl = st.public_attendee_url || st.live_url_public || "https://live.starkvillekoreanchurch.org/live";
      urlsPublic.innerHTML = `
        <div>
          <div style="font-weight:600; color:var(--color-success); margin-bottom: 2px;">🔒 공용 HTTPS 주소 (Cloudflare Domain):</div>
          <a href="${pubUrl}" target="_blank" style="color:var(--color-gold-500); text-decoration:underline;">${pubUrl}</a>
        </div>
      `;
    }

    // ── Status strip ──────────────────────────────────────────────────────────
    function ssSet(id, cls, label) {
      const el = document.getElementById(id);
      if (!el) return;
      el.className = 'ss-item ' + cls;
      for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) { node.textContent = label + ' '; return; }
      }
      el.insertBefore(document.createTextNode(label + ' '), el.firstChild);
    }
    const audioMap = {connected:['ss-ok','🟢 Audio'], no_signal:['ss-warn','🟡 Audio'], disconnected:['ss-err','🔴 Audio'], stopped:['','🔵 Audio']};
    ssSet('ss-audio', ...(audioMap[st.audio.status] || ['','🔵 Audio']));
    const geminiMap = {connected:['ss-ok','🟢 Gemini'], reconnecting:['ss-warn','🟡 Gemini'], connecting:['ss-warn','🟡 Gemini'], failed:['ss-err','🔴 Gemini'], stopped:['','🔵 Gemini']};
    ssSet('ss-gemini', ...(geminiMap[st.session.status] || ['','🔵 Gemini']));
    if (st.session.status === 'connected') ssSet('ss-internet', 'ss-ok', '🟢 Internet');
    else if (st.session.status === 'reconnecting') ssSet('ss-internet', 'ss-warn', '🟡 Internet');
    else if (st.session.status === 'failed') ssSet('ss-internet', 'ss-err', '🔴 Internet');
    else ssSet('ss-internet', '', '🔵 Internet');
    if (st.state === 'running' && st.session.status === 'connected') ssSet('ss-translation', 'ss-ok', '🟢 Translation');
    else if (st.state === 'starting' || st.paused) ssSet('ss-translation', 'ss-warn', '🟡 Translation');
    else if (st.state === 'failed' || st.session.status === 'failed') ssSet('ss-translation', 'ss-err', '🔴 Translation');
    else ssSet('ss-translation', '', '🔵 Translation');

    if (!st.service_running) {
      if (btnStart) { btnStart.disabled = false; btnStart.textContent = '▶ Start'; }
      if (btnPause) { btnPause.disabled = true; btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning'; }
      if (btnStop)  { btnStop.disabled = true; btnStop.textContent = '■ Stop'; }
      _paused = false;
    } else {
      if (btnStart) { btnStart.disabled = true; btnStart.textContent = '▶ Running'; }
      if (btnStop)  { btnStop.disabled = false; }
      if (btnPause) {
        btnPause.disabled = false;
        _paused = st.paused;
        if (_paused) {
          btnPause.textContent = '▶ Resume'; btnPause.className = 'primary';
        } else {
          btnPause.textContent = '⏸ Pause'; btnPause.className = 'warning';
        }
      }
    }
  }, 1000);
}

// ── SSE: Korean + English paired preview ─────────────────────────────────────
function connectSSE() {
  if (captionEs) captionEs.close();
  captionEs = new EventSource('/stream');
  captionEs.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.kind === 'ping') { return; }

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
      preview.appendChild(wrap);

    } else if (msg.kind === 'paused') {
      commitLivePair(null);
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-text-muted)">— Paused —</div>';
      preview.appendChild(wrap);

    } else if (msg.kind === 'resumed') {
      const wrap = document.createElement('div');
      wrap.className = 'preview-pair';
      wrap.innerHTML = '<div class="preview-en" style="color:var(--color-success)">— Resumed —</div>';
      preview.appendChild(wrap);
    }

    if (previewWrap) previewWrap.scrollTop = previewWrap.scrollHeight;
  };
}

const autoStopEl = document.getElementById('auto-stop-select');
if (autoStopEl) {
  autoStopEl.addEventListener('change', async (e) => {
    const mins = parseInt(e.target.value);
    try {
      await fetch('/api/config/auto-stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minutes: mins })
      });
    } catch (err) { /* event will still appear via backend operator_events */ }
  });
}

loadDevices();
startStatusPoll();
startEventPoll();
connectSSE();
