const charts = {};

function renderChart(canvasId, config) {
  const el = document.getElementById(canvasId);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(el, config);
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString("it-IT", { day: "2-digit", month: "short" });
}

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) throw new Error(`${path}: ${resp.status}`);
  return resp.json();
}

// ---------- tabs ----------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---------- connection status ----------
async function refreshStatus() {
  const status = await api("/api/auth/status");
  const strava = document.getElementById("strava-status");
  const garmin = document.getElementById("garmin-status");
  strava.textContent = `Strava: ${status.strava_connected ? "connesso" : "disconnesso"}`;
  strava.className = `pill ${status.strava_connected ? "pill-on" : "pill-off"}`;
  garmin.textContent = `Garmin: ${status.garmin_connected ? "connesso" : "disconnesso"}`;
  garmin.className = `pill ${status.garmin_connected ? "pill-on" : "pill-off"}`;
}

document.getElementById("sync-btn").addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true;
  btn.textContent = "Sincronizzazione…";
  try {
    const result = await api("/api/sync", { method: "POST" });
    btn.textContent = `Fatto: ${result.strava_activities} attività, ${result.garmin_days} giorni`;
    if (result.errors && result.errors.length) console.warn(result.errors);
    await loadAll();
  } catch (err) {
    btn.textContent = "Errore — riprova";
    console.error(err);
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = "Sincronizza ora"; }, 3000);
    refreshStatus();
  }
});

// ---------- Garmin login ----------
document.getElementById("garmin-login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("garmin-email").value;
  const password = document.getElementById("garmin-password").value;
  const msg = document.getElementById("garmin-connect-msg");
  msg.textContent = "Accesso in corso…";
  try {
    const res = await api("/api/auth/garmin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (res.mfa_required) {
      window._garminMfaSession = res.session_id;
      document.getElementById("garmin-mfa-form").classList.remove("hidden");
      msg.textContent = "Inserisci il codice di verifica (MFA) inviato da Garmin.";
    } else {
      msg.textContent = "Garmin connesso.";
      refreshStatus();
    }
  } catch (err) {
    msg.textContent = "Login fallito: controlla le credenziali.";
  }
});

document.getElementById("garmin-mfa-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = document.getElementById("garmin-mfa-code").value;
  const msg = document.getElementById("garmin-connect-msg");
  try {
    await api("/api/auth/garmin/mfa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: window._garminMfaSession, code }),
    });
    msg.textContent = "Garmin connesso.";
    document.getElementById("garmin-mfa-form").classList.add("hidden");
    refreshStatus();
  } catch (err) {
    msg.textContent = "Codice non valido, riprova.";
  }
});

// ---------- data rendering ----------
function activitiesTable(activities) {
  if (!activities.length) return '<p class="muted">Nessuna attività ancora. Collega Strava e sincronizza.</p>';
  const rows = activities.map((a) => `
    <tr>
      <td>${fmtDate(a.start_date)}</td>
      <td>${a.name}</td>
      <td>${a.distance_km} km</td>
      <td>${Math.round(a.moving_time_s / 60)} min</td>
      <td>${a.avg_power_w ? Math.round(a.weighted_avg_power_w || a.avg_power_w) + " W" : "—"}</td>
      <td>${a.avg_hr ? Math.round(a.avg_hr) + " bpm" : "—"}</td>
      <td>${a.training_load ?? "—"}</td>
    </tr>`).join("");
  return `<table>
    <thead><tr><th>Data</th><th>Nome</th><th>Distanza</th><th>Durata</th><th>Potenza</th><th>FC media</th><th>Carico</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderPmc(canvasId, pmc) {
  renderChart(canvasId, {
    type: "line",
    data: {
      labels: pmc.map((p) => fmtDate(p.date)),
      datasets: [
        { label: "CTL (fitness)", data: pmc.map((p) => p.ctl), borderColor: "#00b0d8", tension: 0.3, pointRadius: 0 },
        { label: "ATL (fatica)", data: pmc.map((p) => p.atl), borderColor: "#f0555f", tension: 0.3, pointRadius: 0 },
        { label: "TSB (forma)", data: pmc.map((p) => p.tsb), borderColor: "#3ecf8e", tension: 0.3, pointRadius: 0 },
      ],
    },
    options: { responsive: true, plugins: { legend: { position: "bottom" } }, scales: { y: { beginAtZero: false } } },
  });
}

function renderPowerCurve(curve, ftp) {
  const labels = { "5": "5s", "60": "1min", "300": "5min", "1200": "20min", "3600": "60min" };
  const order = ["5", "60", "300", "1200", "3600"];
  const data = order.map((k) => curve[k] || null);
  document.getElementById("ftp-line").textContent = ftp
    ? `FTP stimata: ${ftp} W (95% della miglior potenza a 20 minuti)`
    : "FTP non stimabile: servono attività con dati di potenza.";
  renderChart("power-curve-chart", {
    type: "bar",
    data: { labels: order.map((k) => labels[k]), datasets: [{ label: "Watt", data, backgroundColor: "#fc5200" }] },
    options: { responsive: true, plugins: { legend: { display: false } } },
  });
}

function renderWellnessCharts(wellness) {
  const labels = wellness.map((w) => fmtDate(w.date));
  renderChart("sleep-chart", {
    type: "line",
    data: { labels, datasets: [{ label: "Sleep score", data: wellness.map((w) => w.sleep_score), borderColor: "#00b0d8", tension: 0.3 }] },
    options: { plugins: { legend: { display: false } } },
  });
  renderChart("battery-chart", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Max", data: wellness.map((w) => w.body_battery_max), borderColor: "#3ecf8e", tension: 0.3 },
        { label: "Min", data: wellness.map((w) => w.body_battery_min), borderColor: "#f0555f", tension: 0.3 },
      ],
    },
    options: { plugins: { legend: { position: "bottom" } } },
  });
  renderChart("stress-chart", {
    type: "line",
    data: { labels, datasets: [{ label: "Stress medio", data: wellness.map((w) => w.stress_avg), borderColor: "#f0555f", tension: 0.3 }] },
    options: { plugins: { legend: { display: false } } },
  });
  renderChart("readiness-chart", {
    type: "line",
    data: { labels, datasets: [{ label: "Training Readiness", data: wellness.map((w) => w.training_readiness_score), borderColor: "#fc5200", tension: 0.3 }] },
    options: { plugins: { legend: { display: false } } },
  });
}

async function loadAll() {
  try {
    const summary = await api("/api/dashboard/summary");
    document.getElementById("readiness-score").textContent = summary.readiness.score ?? "—";
    document.getElementById("readiness-label").textContent = summary.readiness.label;
    renderPmc("pmc-mini-chart", summary.pmc_tail);
    document.getElementById("recent-activities-table").innerHTML = activitiesTable(summary.recent_activities);
  } catch (err) {
    console.error("dashboard summary failed", err);
  }

  try {
    const pmc = await api("/api/cycling/pmc?days=180");
    renderPmc("pmc-chart", pmc);
  } catch (err) { console.error(err); }

  try {
    const pc = await api("/api/cycling/power-curve?days=90");
    renderPowerCurve(pc.best_power_curve, pc.estimated_ftp);
  } catch (err) { console.error(err); }

  try {
    const activities = await api("/api/cycling/activities?limit=200");
    document.getElementById("activities-table").innerHTML = activitiesTable(activities);
  } catch (err) { console.error(err); }

  try {
    const wellness = await api("/api/health/wellness?days=30");
    renderWellnessCharts(wellness);
  } catch (err) { console.error(err); }
}

refreshStatus();
loadAll();
