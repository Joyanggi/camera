const STATUS_URL_REMOTE =
  "https://raw.githubusercontent.com/Joyanggi/camera/main/status.json";
const STATUS_URL_LOCAL = "../status.json";
const REFRESH_INTERVAL_MS = 60 * 1000;
const HARD_RELOAD_INTERVAL_MS = 5 * 60 * 1000;
const PREV_STATUS_KEY = "prev_status_v1";

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
  return counts;
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

function loadPrevStatus() {
  try {
    const raw = localStorage.getItem(PREV_STATUS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function savePrevStatus(map) {
  try {
    localStorage.setItem(PREV_STATUS_KEY, JSON.stringify(map));
  } catch {}
}

function buildStatusMap(products) {
  const map = {};
  for (const p of products) {
    if (p.url) map[p.url] = p.status;
  }
  return map;
}

function detectNewBuyables(products, prevMap) {
  if (!prevMap) return [];
  const fresh = [];
  for (const p of products) {
    if (p.status !== "BUYABLE") continue;
    if (prevMap[p.url] !== "BUYABLE") fresh.push(p);
  }
  return fresh;
}

function notifySupported() {
  return "Notification" in window;
}

function updateNotifBtn() {
  const btn = document.getElementById("notifBtn");
  if (!btn) return;
  if (!notifySupported()) {
    btn.textContent = "알림 미지원";
    btn.disabled = true;
    return;
  }
  const perm = Notification.permission;
  if (perm === "granted") {
    btn.textContent = "🔔 알림 켜짐";
    btn.classList.add("on");
    btn.disabled = true;
  } else if (perm === "denied") {
    btn.textContent = "🔕 알림 차단됨";
    btn.disabled = true;
  } else {
    btn.textContent = "🔔 알림 허용";
    btn.disabled = false;
  }
}

async function requestNotifPermission() {
  if (!notifySupported()) return;
  try {
    await Notification.requestPermission();
  } catch {}
  updateNotifBtn();
}

let audioCtx = null;
function ensureAudioCtx() {
  if (!audioCtx) {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch {
      return null;
    }
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }
  return audioCtx;
}

function playAlertChime() {
  const ctx = ensureAudioCtx();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;
    const PEAK = 0.32;

    // C6-E6-G6-C7 (도-미-솔-도) 상행 아르페지오를 0.3초 간격으로 두 번 반복
    // 총 길이 ~1.8초. 음 하나당 사인파 + 한 옥타브 위 사인파 살짝 섞어 더 또렷하게
    const notes = [
      1046.5, // C6
      1318.5, // E6
      1568.0, // G6
      2093.0, // C7
    ];
    const noteDur = 0.16;
    const noteGap = 0.04;
    const repeatGap = 0.3;

    [0, 1].forEach((cycle) => {
      const cycleStart = cycle * (notes.length * (noteDur + noteGap) + repeatGap);
      notes.forEach((freq, idx) => {
        const start = cycleStart + idx * (noteDur + noteGap);
        // 메인 사인파
        playTone(ctx, freq, "sine", start, noteDur, PEAK, now);
        // 한 옥타브 위 사인파 가볍게 (밝기 추가)
        playTone(ctx, freq * 2, "sine", start, noteDur, PEAK * 0.18, now);
      });
    });
  } catch (err) {
    console.error("audio error", err);
  }
}

function playTone(ctx, freq, type, startOffset, dur, peak, baseTime) {
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  const t0 = baseTime + startOffset;
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(peak, t0 + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(g).connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

function fireNotification(product) {
  playAlertChime();
  if (!notifySupported() || Notification.permission !== "granted") return;
  try {
    const n = new Notification(`📸 재고 풀림: ${product.site}`, {
      body: `${product.name}\n${product.detail || ""}`,
      tag: product.url,
      renotify: true,
    });
    n.onclick = () => {
      window.focus();
      window.open(product.url, "_blank", "noopener");
      n.close();
    };
  } catch (err) {
    console.error("notification error", err);
  }
}

function applyBuyableTheme(hasBuyable) {
  document.body.classList.toggle("has-buyable", hasBuyable);
}

let firstLoad = true;

async function refresh() {
  try {
    const data = await fetchStatus();
    const products = data.products || [];
    lastProducts = products;

    document.getElementById("checkedAt").textContent =
      `마지막 확인: ${formatKST(data.checked_at)}`;
    const counts = renderSummary(products);
    renderCards(products);
    applyBuyableTheme(counts.buyable > 0);

    const prevMap = loadPrevStatus();
    const currMap = buildStatusMap(products);

    if (!firstLoad) {
      const fresh = detectNewBuyables(products, prevMap);
      for (const p of fresh) fireNotification(p);
    }
    savePrevStatus(currMap);
    firstLoad = false;
  } catch (err) {
    document.getElementById("checkedAt").textContent = "상태를 불러오지 못했습니다";
    document.getElementById("cards").innerHTML =
      `<p class="loading">상태 파일을 가져오지 못했어요: ${escapeHtml(err.message)}</p>`;
    console.error(err);
  }
}

const TEST_DURATION_MS = 10 * 1000;
let testTimer = null;
let lastProducts = [];

const FAKE_PRODUCT = {
  site: "테스트",
  name: "🧪 테스트용 카메라 (실제 상품 아님)",
  url: "#test",
  status: "BUYABLE",
  detail: "테스트 모드: 카드 배경 + 알림 + 상단 테마 미리보기",
};

function runTest() {
  const btn = document.getElementById("testBtn");

  fireNotification(FAKE_PRODUCT);

  const augmented = [FAKE_PRODUCT, ...lastProducts];
  renderSummary(augmented);
  renderCards(augmented);
  applyBuyableTheme(true);

  if (btn) {
    btn.textContent = "🧪 테스트 중...";
    btn.disabled = true;
  }

  if (testTimer) clearTimeout(testTimer);
  testTimer = setTimeout(() => {
    refresh();
    if (btn) {
      btn.textContent = "🧪 테스트";
      btn.disabled = false;
    }
    testTimer = null;
  }, TEST_DURATION_MS);
}

// 첫 user gesture 때 AudioContext 활성화 (브라우저 정책상 필수)
function warmUpAudio() {
  ensureAudioCtx();
}

document.getElementById("refreshBtn").addEventListener("click", () => {
  warmUpAudio();
  refresh();
});
document.getElementById("notifBtn").addEventListener("click", () => {
  warmUpAudio();
  requestNotifPermission();
});
document.getElementById("testBtn").addEventListener("click", () => {
  warmUpAudio();
  runTest();
});
updateNotifBtn();
refresh();
setInterval(refresh, REFRESH_INTERVAL_MS);

// 5분마다 하드 새로고침. 테스트 진행 중이면 다음 주기로 미룸
setInterval(() => {
  if (testTimer) return;
  location.reload();
}, HARD_RELOAD_INTERVAL_MS);
