// frontend/app.js
const API = {
  key: null,

  async init() {
    const cfg = await this.get('/api/ui-config');
    this.key = cfg.api_key;
  },

  headers() {
    return { 'Content-Type': 'application/json', 'X-API-Key': this.key };
  },

  async get(path) {
    const r = await fetch(path, { headers: this.headers() });
    if (!r.ok) throw new Error(`GET ${path} failed: ${r.status}`);
    return r.json();
  },

  async post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: this.headers(), body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`POST ${path} failed: ${r.status}`);
    return r.json();
  },
};

// DOM helpers
const $ = id => document.getElementById(id);

// --- State ---
let currentJobId = null;
let pollInterval = null;
let clientMap = {};  // tenant_name → { tenant_id, tenant_shortcode }
let isLoggedIn = false;  // tracks current auth state for finishRun

// --- Initialization ---
document.addEventListener('DOMContentLoaded', async () => {
  await API.init();
  loadAuthStatus();
  loadClients();
  loadRecentRuns();
  populateYears();
  $('run-btn').addEventListener('click', handleRun);
  $('login-btn').addEventListener('click', handleLogin);
});

async function loadAuthStatus() {
  try {
    const status = await API.get('/api/auth/status');
    isLoggedIn = status.logged_in;
    const badge = $('auth-badge');
    if (status.logged_in) {
      badge.className = 'status-badge ok';
      badge.innerHTML = '<span class="dot"></span> Logged In';
      $('login-btn').style.display = 'none';
      $('run-btn').disabled = false;
    } else {
      badge.className = 'status-badge error';
      badge.innerHTML = '<span class="dot"></span> Not Logged In';
      $('login-btn').style.display = '';
      $('run-btn').disabled = true;
    }
  } catch {
    isLoggedIn = false;
    $('auth-badge').className = 'status-badge error';
    $('auth-badge').innerHTML = '<span class="dot"></span> Unknown';
    $('login-btn').style.display = '';
    $('run-btn').disabled = true;
  }
}

async function handleLogin() {
  const btn = $('login-btn');
  btn.disabled = true;
  btn.textContent = 'Logging in...';
  try {
    await API.post('/api/auth/automated-login', {});
  } catch (e) {
    console.error('Login failed:', e);
  }
  btn.textContent = 'Login';
  await loadAuthStatus();
}

async function loadClients() {
  try {
    const data = await API.get('/api/clients/');
    const datalist = $('clients-list');
    datalist.innerHTML = '';
    clientMap = {};
    data.clients.forEach(c => {
      clientMap[c.tenant_name] = { tenant_id: c.tenant_id, tenant_shortcode: c.tenant_shortcode };
      const opt = document.createElement('option');
      opt.value = c.tenant_name;
      datalist.appendChild(opt);
    });
  } catch (e) {
    console.error('Failed to load clients:', e);
  }
}

function populateYears() {
  const sel = $('year-select');
  const current = new Date().getFullYear();
  for (let y = current - 2; y <= current + 1; y++) {
    const opt = document.createElement('option');
    opt.value = y;
    opt.textContent = y;
    if (y === current) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function loadRecentRuns() {
  try {
    const data = await API.get('/api/reports/logs?limit=10');
    const tbody = $('runs-tbody');
    if (!data.logs || data.logs.length === 0) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No runs yet</td></tr>';
      return;
    }
    tbody.innerHTML = data.logs.map(log => {
      const when = log.started_at ? new Date(log.started_at).toLocaleString() : '—';
      const statusClass = log.status === 'success' ? 'success' : log.status === 'failed' ? 'failed' : 'running';
      const name = log.file_name || '—';
      return `<tr>
        <td>${name}</td>
        <td><span class="badge ${statusClass}">${log.status}</span></td>
        <td>${when}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load recent runs:', e);
  }
}

// --- Run handler ---
async function handleRun() {
  const orgInput = $('org-input').value.trim();
  const month = parseInt($('month-select').value);
  const year = parseInt($('year-select').value);

  if (!orgInput) { alert('Please select an organisation.'); return; }
  if (!clientMap[orgInput]) { alert('Organisation not found in the list. Please select from the dropdown.'); return; }

  const client = clientMap[orgInput];

  $('run-btn').disabled = true;
  $('run-btn').textContent = 'Running...';
  showProgress();
  clearSteps();
  addStep(`Starting report for ${orgInput}...`);

  try {
    const job = await API.post('/api/reports/run', {
      tenant_id: client.tenant_id,
      tenant_name: orgInput,
      tenant_shortcode: client.tenant_shortcode,
      month: month,
      year: year,
      find_unfiled: true,
    });
    currentJobId = job.job_id;
    startPolling();
  } catch (e) {
    addStep(`Error: ${e.message}`, 'error');
    finishRun(false, null);
  }
}

function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(async () => {
    try {
      const job = await API.get(`/api/reports/job/${currentJobId}`);
      // Sync steps shown in UI with job steps
      syncSteps(job.steps, job.status);

      if (job.status !== 'running') {
        clearInterval(pollInterval);
        pollInterval = null;
        const success = job.status === 'success';
        const fileName = job.result?.consolidated_file?.file_name || null;
        finishRun(success, fileName, job.result?.errors);
        loadRecentRuns();
      }
    } catch (e) {
      // Job may have expired or server restarted
      clearInterval(pollInterval);
      addStep('Could not contact server. Check if the app is still running.', 'error');
      finishRun(false, null);
    }
  }, 3000);
}

// --- UI helpers ---
let renderedSteps = 0;

function showProgress() {
  $('progress-section').style.display = 'block';
  $('result-box').style.display = 'none';
  renderedSteps = 0;
}

function clearSteps() {
  $('step-log').innerHTML = '';
  renderedSteps = 0;
}

function syncSteps(steps, status) {
  // Only append steps we haven't rendered yet
  for (let i = renderedSteps; i < steps.length; i++) {
    const isDone = status !== 'running' && i === steps.length - 1;
    addStep(steps[i], isDone ? (status === 'success' ? 'done' : 'error') : '');
  }
  renderedSteps = steps.length;
}

function addStep(msg, cls = '') {
  const li = document.createElement('li');
  if (cls) li.className = cls;
  li.textContent = msg;
  $('step-log').appendChild(li);
}

function finishRun(success, fileName, errors) {
  $('run-btn').disabled = !isLoggedIn;
  $('run-btn').textContent = 'Run Report';
  const box = $('result-box');
  box.style.display = 'block';
  if (success) {
    box.className = 'result-box success';
    box.textContent = fileName ? `Done! File saved: ${fileName}` : 'Done!';
  } else {
    box.className = 'result-box failure';
    const errMsg = errors && errors.length ? errors.join('; ') : 'The report job failed. Check the step log above.';
    box.textContent = `Failed: ${errMsg}`;
  }
}
