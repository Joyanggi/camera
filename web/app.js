const STATUS_URL_REMOTE =
  "https://raw.githubusercontent.com/Joyanggi/camera/main/status.json";
const STATUS_URL_LOCAL = "../status.json";
const REFRESH_INTERVAL_MS = 60 * 1000;

const STATUS_META = {
  BUYABLE:       { label: "구매 가능", klass: "buyable" },
  SOLD_OUT:      { label: "품절",      klass: "soldout" },
  UNKNOWN:       { label: "확인 필요", klass: "warning" },
  NETWORK_ERROR: { label: "네트워크",  klass: "error"   },
  RATE_LIMITED:  { label: "차단 대기", klass: "warning" },
  ERROR:         { label: "오류",      klass: "error"   },
  SKIPPED:       { label: "건너뜀",    klass: "warning" },
};

function statusMeta(status) {
  return STATUS_META[status] || { label: status || "-", klass: "warning" };
}

function pickStatusUrl() {
  if (
    location.hostname === "localhost" ||
    location.hostname === "127.0.0.1" ||
    location.protocol === "file:"
  ) {
    return STATUS_URL_LOCAL;
  }
  return STATUS_URL_REMOTE;
}

async function fetchStatus() {
  const url = `${pickStatusUrl()}?t=${Date.now()}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function formatKST(iso) {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleString("ko-KR", {
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function renderSummary(products) {
  const counts = { buyable: 0, soldout: 0, warning: 0, error: 0 };
  for (const p of products) {
    const k = statusMeta(p.status).klass;
    counts[k] = (counts[k] || 0) + 1;
  }
  const summary = document.getElementById("summary");
  summary.innerHTML = `
    <div class="stat buyable">
      <div class="label">구매 가능</div>
      <div class="value">${counts.buyable}</div>
    </div>
    <div class="stat soldout">
      <div class="label">품절</div>
      <div class="value">${counts.soldout}</div>
    </div>
    <div class="stat warning">
      <div class="label">확인 필요</div>
      <div class="value">${counts.warning}</div>
    </div>
    <div class="stat error">
      <div class="label">오류</div>
      <div class="value">${counts.error}</div>
    </div>
  `;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

function renderCards(products) {
  const wrap = document.getElementById("cards");
  if (!products.length) {
    wrap.innerHTML = `<p class="loading">표시할 상품이 없습니다.</p>`;
    return;
  }
  const sorted = [...products].sort((a, b) => {
    const rank = (s) => (s === "BUYABLE" ? 0 : s === "UNKNOWN" ? 1 : s === "SOLD_OUT" ? 2 : 3);
    return rank(a.status) - rank(b.status);
  });
  wrap.innerHTML = sorted.map((p) => {
    const meta = statusMeta(p.status);
    return `
      <a class="card ${meta.klass}" href="${escapeHtml(p.url)}" target="_blank" rel="noopener">
        <div class="card-head">
          <span class="badge">${escapeHtml(p.site)}</span>
          <span class="status-pill ${meta.klass}">${escapeHtml(meta.label)}</span>
        </div>
        <h2 class="card-name">${escapeHtml(p.name)}</h2>
        <div class="card-detail">${escapeHtml(p.detail || "")}</div>
      </a>
    `;
  }).join("");
}

async function refresh() {
  try {
    const data = await fetchStatus();
    document.getElementById("checkedAt").textContent =
      `마지막 확인: ${formatKST(data.checked_at)}`;
    renderSummary(data.products || []);
    renderCards(data.products || []);
  } catch (err) {
    document.getElementById("checkedAt").textContent = "상태를 불러오지 못했습니다";
    document.getElementById("cards").innerHTML =
      `<p class="loading">상태 파일을 가져오지 못했어요: ${escapeHtml(err.message)}</p>`;
    console.error(err);
  }
}

document.getElementById("refreshBtn").addEventListener("click", refresh);
refresh();
setInterval(refresh, REFRESH_INTERVAL_MS);
