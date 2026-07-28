function showTab(tab) {
  document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  
  document.getElementById(tab).style.display = 'block';
  event.currentTarget.classList.add('active');
  
  if (tab === 'today') loadToday();
  if (tab === 'submitted') loadSubmitted();
}

async function loadToday() {
  const res = await fetch('/api/today');
  const data = await res.json();
  const container = document.getElementById('today-list');
  if (!data.length) {
    container.innerHTML = '<p class="detail">Aucun média lu aujourd’hui.</p>';
    return;
  }
  container.innerHTML = data.map(e => `
    <div class="entry">
      <div class="date">${e.date}</div>
      <div class="label">${e.label}</div>
      <div class="detail">${e.detail}</div>
    </div>
  `).join('');
}

async function loadSubmitted() {
  const res = await fetch('/api/status');
  const data = await res.json();
  const container = document.getElementById('submitted-list');
  const all = [...(data.jobs || []), ...(data.history || [])];
  if (!all.length) {
    container.innerHTML = '<p class="detail">Aucune soumission.</p>';
    return;
  }
  container.innerHTML = all.map(e => `
    <div class="entry">
      <div class="date">${e.date}</div>
      <div class="label">${e.label}</div>
      <div class="detail">${e.status} — ${e.detail}</div>
    </div>
  `).join('');
}

document.addEventListener('DOMContentLoaded', () => {
  loadToday();
});