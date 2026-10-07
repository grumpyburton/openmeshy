// --- Image -> Unity mode ---------------------------------------------------------------
// One picture in, a rigged Unity-ready folder out. Drives POST /api/unity
// (viewer/unity_api.py -> scripts/image_to_unity.py): Pixal3D, Finish, auto-rig, export.
// Same SSE-with-polling-fallback shape as modes/finish.js.
import { JobProgressPanel } from '../components/job-progress.js';
import { STAGE_META, previewUrl, rigNote } from '../components/unity-format.js';

const f = (id) => document.getElementById(id);
const progress = new JobProgressPanel({
  stages: f('unity-stages'),
  bar: f('unity-overall-bar'),
  label: f('unity-overall-label'),
  eta: f('unity-overall-eta'),
});

const state = { image: null, running: false, source: null, poll: null };

function updateSubmit() {
  f('unity-submit').disabled = state.running || !state.image;
}

function setRunning(running) {
  state.running = running;
  f('unity-cancel').hidden = !running;
  updateSubmit();
}

function stopWatching() {
  if (state.source) state.source.close();
  if (state.poll) clearInterval(state.poll);
  state.source = null;
  state.poll = null;
}

function applyEvent(event) {
  progress.apply(event);
  if (event.message) f('unity-status').textContent = event.message;
  if (event.phase === 'done') {
    stopWatching();
    setRunning(false);
    f('unity-result').hidden = false;
    f('unity-download').href = event.zip_url;
    f('unity-run').href = event.run_url;
    f('unity-rig-note').textContent = rigNote(event.rig, f('unity-class').value);
    f('unity-where').textContent = `Also saved in ${event.folder}`;
    f('unity-preview-empty').hidden = true;
    f('unity-preview-frame').hidden = false;
    f('unity-preview-frame').src = previewUrl(`${event.preview_url}?t=${Date.now()}`);
  } else if (event.phase === 'error') {
    stopWatching();
    setRunning(false);
    f('unity-status').textContent = event.log_tail
      ? `${event.message}\n${event.log_tail.split('\n').slice(-6).join('\n')}` : event.message;
  }
}

function startPolling(jobId) {
  if (state.source) state.source.close();
  state.source = null;
  if (state.poll) return;
  state.poll = setInterval(async () => {
    try {
      const response = await fetch(`/api/unity/${jobId}/status`);
      if (!response.ok) return;
      const status = await response.json();
      if (status.last_event) applyEvent(status.last_event);
    } catch { /* the server may be restarting; keep trying */ }
  }, 3000);
}

async function submit() {
  if (!state.image) return;
  setRunning(true);
  progress.configure(STAGE_META);
  progress.reset();
  f('unity-progress-box').hidden = false;
  f('unity-result').hidden = true;
  f('unity-status').textContent = 'Uploading…';
  const settings = {
    class: f('unity-class').value,
    faces: Number(f('unity-faces').value),
    seed: Number(f('unity-seed').value || 42),
    name: f('unity-name').value.trim(),
    height: f('unity-height').value ? Number(f('unity-height').value) : null,
  };
  const body = new FormData();
  body.append('image', state.image, state.image.name);
  body.append('settings', JSON.stringify(settings));
  try {
    const response = await fetch('/api/unity', { method: 'POST', body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.source = new EventSource(payload.events_url);
    state.source.onmessage = (message) => applyEvent(JSON.parse(message.data));
    state.source.onerror = () => startPolling(payload.job_id);
    f('unity-cancel').onclick = async () => {
      await fetch(`/api/unity/${payload.job_id}/cancel`, { method: 'POST' });
    };
  } catch (error) {
    setRunning(false);
    f('unity-status').textContent = `Could not start: ${error.message}`;
  }
}

f('unity-image').onchange = (event) => {
  state.image = event.target.files[0] || null;
  f('unity-image-name').textContent = state.image ? state.image.name
    : 'a character or creature, whole body, plain background';
  const preview = f('unity-image-preview');
  if (state.image) {
    preview.src = URL.createObjectURL(state.image);
    preview.hidden = false;
  } else {
    preview.hidden = true;
  }
  updateSubmit();
};
f('unity-submit').onclick = submit;
