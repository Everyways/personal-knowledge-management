const ICONS = {
  queued: "⏳", processing: '<span class="dot">◔</span>', attente: "⏸️",
  doublon: "🔁", ok: "✅", erreur: "❌", info: "📋",
};

let pollTimer = null;

function showTab(tab) {
  document.querySelectorAll(".tab-content").forEach(el => el.style.display = "none");
  document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));
  document.getElementById(tab).style.display = "block";
  document.querySelector(`.tab-btn[data-tab="${tab}"]`).classList.add("active");
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function tagsHtml(tags) {
  if (!tags || !tags.length) return "";
  return `<div class="tags">${tags.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join("")}</div>`;
}

function retryHtml(e) {
  if (e.status !== "erreur" || !e.url) return "";
  return `<button class="small" onclick="retry('${encodeURIComponent(e.url)}', this)">Réessayer</button>`;
}

async function retry(encodedUrl, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    await fetch("/api/retry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: decodeURIComponent(encodedUrl) }),
    });
    poll();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Réessayer";
  }
}

function renderToday(data) {
  const container = document.getElementById("today-list");
  if (!data.length) {
    container.innerHTML = '<p class="empty">Aucune lecture aujourd’hui.</p>';
    return;
  }
  container.innerHTML = data.map(e => `
    <div class="entry status-${e.status}">
      <div class="entry-head">
        <span class="entry-icon">${ICONS[e.status] || ""}</span>
        <span class="entry-label">${escapeHtml(e.label)}</span>
        <span class="entry-date">${escapeHtml(e.date)}</span>
      </div>
      ${e.detail ? `<div class="entry-detail">${escapeHtml(e.detail)}</div>` : ""}
      ${tagsHtml(e.tags)}
    </div>
  `).join("");
}

function renderSubmitted(data) {
  const container = document.getElementById("submitted-list");
  const rows = data.jobs || [];
  const hist = (data.history || []).filter(h => !rows.find(r => r.label === h.label && r.status !== "queued"));
  const all = [...rows, ...hist].slice(0, 30);
  if (!all.length) {
    container.innerHTML = '<p class="empty">Aucune soumission.</p>';
  } else {
    container.innerHTML = all.map(e => `
      <div class="entry status-${e.status}">
        <div class="entry-head">
          <span class="entry-icon" title="${escapeHtml(e.status)}">${ICONS[e.status] || e.status}</span>
          <span class="entry-label">${escapeHtml(e.label)}</span>
          <span class="entry-date">${escapeHtml(e.date)}</span>
        </div>
        ${e.detail ? `<div class="entry-detail">${escapeHtml(e.detail)}</div>` : ""}
        ${tagsHtml(e.tags)}
        ${retryHtml(e)}
      </div>
    `).join("");
  }

  const active = rows.filter(r => ["queued", "processing", "attente"].includes(r.status));
  const ind = document.getElementById("indicator");
  if (active.length) {
    ind.textContent = `🔄 ${active.length} job(s) en cours…`;
    schedule(2000);
  } else {
    ind.textContent = "";
    schedule(15000);
  }
}

function schedule(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, ms);
}

async function poll() {
  try {
    const [statusRes, todayRes] = await Promise.all([
      fetch("/api/status"), fetch("/api/today"),
    ]);
    renderSubmitted(await statusRes.json());
    renderToday(await todayRes.json());
  } catch (e) {
    schedule(5000);
  }
}

let searchTimer = null;

function onSearchInput(e) {
  clearTimeout(searchTimer);
  const q = e.target.value;
  searchTimer = setTimeout(() => doSearch(q), 300);
}

async function doSearch(q) {
  const results = document.getElementById("search-results");
  if (!q.trim()) {
    results.innerHTML = "";
    return;
  }
  try {
    const res = await fetch("/api/search?q=" + encodeURIComponent(q));
    const data = await res.json();
    if (!data.length) {
      results.innerHTML = '<p class="empty">Aucun résultat.</p>';
      return;
    }
    results.innerHTML = data.map(r => `
      <div class="entry" onclick="openPreview('${encodeURIComponent(r.path)}')">
        <div class="entry-head">
          <span class="entry-label">${escapeHtml(r.title)}</span>
          <span class="entry-date">${escapeHtml(r.date)}</span>
        </div>
        <div class="entry-detail">${escapeHtml(r.excerpt)}…</div>
      </div>
    `).join("");
  } catch (e) {
    results.innerHTML = '<p class="empty">Recherche indisponible.</p>';
  }
}

async function openPreview(encodedPath) {
  const preview = document.getElementById("note-preview");
  const content = document.getElementById("note-preview-content");
  try {
    const res = await fetch("/api/note?path=" + encodedPath);
    const data = await res.json();
    content.textContent = data.content || "Introuvable.";
  } catch (e) {
    content.textContent = "Erreur de chargement.";
  }
  document.getElementById("search-results").style.display = "none";
  preview.style.display = "block";
}

function closePreview() {
  document.getElementById("note-preview").style.display = "none";
  document.getElementById("search-results").style.display = "block";
}

poll();
