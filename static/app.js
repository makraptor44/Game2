const socket = io({ transports: ["polling", "websocket"] });

let myName  = null;
let gameId  = null;
let isHost  = false;
let timerInterval = null;

// ── Connection ──────────────────────────────────────────────────────────────

socket.on("connect", () => console.log("Connected:", socket.id));
socket.on("connect_error", (err) => {
  console.error("Socket error:", err);
  showToast("Connection error — refresh the page", "error");
});

// ── Tabs ────────────────────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${tab}`).classList.add("active");
  });
});

// ── Lobby actions ────────────────────────────────────────────────────────────

document.getElementById("btn-create").addEventListener("click", () => {
  const name   = document.getElementById("create-name").value.trim();
  const rounds = parseInt(document.getElementById("cfg-rounds").value) || 5;
  if (!name) { setLobbyError("Enter your name"); return; }
  myName = name;
  isHost = true;
  window._pendingHost = { name, rounds };
  socket.emit("CreateGame", { rounds });
});

document.getElementById("btn-join").addEventListener("click", () => {
  const name = document.getElementById("join-name").value.trim();
  const code = document.getElementById("join-code").value.trim().toLowerCase();
  if (!name) { setLobbyError("Enter your name"); return; }
  if (!code) { setLobbyError("Enter a game code"); return; }
  myName = name;
  isHost = false;
  gameId = code;
  socket.emit("JoinGame", { game_id: code, name });
});

function setLobbyError(msg) {
  const el = document.getElementById("lobby-error");
  el.textContent = msg;
  setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 3000);
}

socket.on("game_created", (data) => {
  gameId = data.game_id;
  const name = window._pendingHost?.name || document.getElementById("create-name").value.trim();
  socket.emit("JoinGame", { game_id: gameId, name });
});

socket.on("error", (data) => {
  console.warn("Server error:", data.message);
  showToast(data.message, "error");
  setLobbyError(data.message);
});

// ── State handler ────────────────────────────────────────────────────────────

socket.on("state_update", (state) => {
  if (!gameId && state.game_id) gameId = state.game_id;
  handleStateUpdate(state);
});

function handleStateUpdate(state) {
  const mode = getMyUiMode(state);

  if (mode !== "lobby") updateHUD(state);

  if (state.phase_ends_at) {
    startTimer(state.phase_ends_at, state.server_time);
  } else {
    clearTimer();
  }

  switch (mode) {
    case "lobby":          showWaitingRoom(state); break;

    case "phase1_reveal":
      showScreen("screen-game"); showPanel("panel-phase1-reveal");
      renderRevealPanel(state, 1); break;

    case "phase1_trade":
      showScreen("screen-game"); showPanel("panel-phase1-trade");
      renderTradePanel(state, 1); break;

    case "phase1_acted":
      showScreen("screen-game"); showPanel("panel-phase1-acted");
      renderActedPanel(state, 1); break;

    case "phase2_reveal":
      showScreen("screen-game"); showPanel("panel-phase2-reveal");
      renderRevealPanel(state, 2); break;

    case "phase2_trade":
      showScreen("screen-game"); showPanel("panel-phase2-trade");
      renderTradePanel(state, 2); break;

    case "phase2_acted":
      showScreen("screen-game"); showPanel("panel-phase2-acted");
      renderActedPanel(state, 2); break;

    case "round_results":
      showScreen("screen-game"); showPanel("panel-results");
      renderResults(state, false); break;

    case "final_results":
      showScreen("screen-game"); showPanel("panel-results");
      renderResults(state, true); break;

    case "eliminated":
      showScreen("screen-game"); showPanel("panel-eliminated");
      renderEliminated(state); break;

    default:
      console.warn("Unknown ui_mode:", mode);
  }
}

function getMyUiMode(state) {
  if (state.player_data?.ui_mode) return state.player_data.ui_mode;
  const map = {
    lobby: "lobby",
    phase1_reveal:  "phase1_reveal",
    phase1_trading: "phase1_trade",
    phase2_reveal:  "phase2_reveal",
    phase2_trading: "phase2_trade",
    round_results:  "round_results",
    finished:       "final_results",
  };
  return map[state.phase] || "lobby";
}

// ── Waiting room ─────────────────────────────────────────────────────────────

function showWaitingRoom(state) {
  showScreen("screen-waiting");
  document.getElementById("waiting-code").textContent = state.game_id || gameId || "——";

  const grid = document.getElementById("waiting-players");
  grid.innerHTML = (state.players || []).map(p => `
    <div class="player-chip ${p.eliminated ? "is-eliminated" : ""}">
      <span class="role-dot"></span>
      ${escHtml(p.name)}${p.role === "host" ? " <small>(host)</small>" : ""}
    </div>
  `).join("");

  const startBtn = document.getElementById("btn-start");
  const hint     = document.getElementById("waiting-hint");
  if (isHost) {
    startBtn.classList.remove("hidden");
    hint.style.display = "none";
    startBtn.disabled  = (state.players || []).length < (state.config?.min_players || 2);
  } else {
    startBtn.classList.add("hidden");
    hint.style.display = "block";
  }
}

document.getElementById("btn-start").addEventListener("click", () => {
  socket.emit("StartGame", { game_id: gameId });
});

document.getElementById("waiting-code").addEventListener("click", async () => {
  if (!gameId || !navigator.clipboard) return;
  await navigator.clipboard.writeText(gameId);
  showToast("Game code copied", "success");
});

// ── HUD ──────────────────────────────────────────────────────────────────────

function updateHUD(state) {
  document.getElementById("hud-round").textContent       = state.round_index || "—";
  document.getElementById("hud-rounds-total").textContent = state.config?.rounds || "—";

  const phaseLabels = {
    phase1_reveal:  "Phase 1 Reveal",
    phase1_trading: "Phase 1 Trade",
    phase2_reveal:  "Phase 2 Reveal",
    phase2_trading: "Phase 2 Trade",
    round_results:  "Results",
    finished:       "Game Over",
  };
  document.getElementById("hud-phase").textContent = phaseLabels[state.phase] || state.phase || "—";

  const pd = state.player_data || {};
  document.getElementById("hud-cash").textContent = `£${Number(pd.my_cash ?? 0).toFixed(2)}`;
  document.getElementById("hud-pos").textContent  = pd.my_round_position ?? 0;
}

// ── Timer ────────────────────────────────────────────────────────────────────

function startTimer(endsAt, serverNow) {
  clearTimer();
  const fill     = document.getElementById("timer-fill");
  const totalSec = Math.max(0.001, endsAt - serverNow);

  function tick() {
    const remaining = endsAt - Date.now() / 1000;
    const pct = Math.max(0, Math.min(100, (remaining / totalSec) * 100));
    fill.style.width = `${pct}%`;
    fill.classList.toggle("crit", pct < 20);
    fill.classList.toggle("warn", pct >= 20 && pct < 50);
    if (pct >= 50) fill.classList.remove("warn", "crit");
  }
  tick();
  timerInterval = setInterval(tick, 500);
}

function clearTimer() {
  if (timerInterval) clearInterval(timerInterval);
  const fill = document.getElementById("timer-fill");
  fill.style.width = "100%";
  fill.classList.remove("warn", "crit");
}

// ── Reveal panels ─────────────────────────────────────────────────────────────

function renderRevealPanel(state, phaseNum) {
  const r = state.round || {};

  // Cards
  renderCards(state, phaseNum === 1 ? "phase1-cards" : "phase2-cards");

  // Market prices
  if (phaseNum === 1) {
    setText("phase1-market-pre",    formatMoney(r.market_price_phase1_pre_news));
    setText("phase1-market-frozen", formatMoney(r.market_price_phase1_frozen));
  } else {
    setText("phase2-market-pre",    formatMoney(r.market_price_phase2_pre_news));
    setText("phase2-market-frozen", formatMoney(r.market_price_phase2_frozen));
  }

  // News — FIX: pass arrays directly, not nested objects
  if (phaseNum === 1) {
    renderNewsList("phase1-deck-news", state.persistent_deck_news || []);
    renderNewsList("phase1-news",      r.phase1_news || []);
  } else {
    renderNewsList("phase2-deck-news", state.persistent_deck_news || []);
    // Phase 2 shows ALL news: phase1 + phase2
    const allNews = [...(r.phase1_news || []), ...(r.phase2_news || [])];
    renderNewsList("phase2-news", allNews);
  }
}

// ── Trade panels ──────────────────────────────────────────────────────────────

function renderTradePanel(state, phaseNum) {
  const r = state.round || {};

  renderCards(state, phaseNum === 1 ? "trade1-cards" : "trade2-cards");

  const price = phaseNum === 1 ? r.market_price_phase1_frozen : r.market_price_phase2_frozen;
  setText(phaseNum === 1 ? "trade1-price" : "trade2-price", formatMoney(price));
  setText(phaseNum === 1 ? "trade1-error" : "trade2-error", "");

  // Reset qty input only once
  const qtyEl = document.getElementById(phaseNum === 1 ? "trade1-qty" : "trade2-qty");
  if (!qtyEl.dataset.initialised) {
    qtyEl.value = "1";
    qtyEl.dataset.initialised = "1";
  }

  // News
  if (phaseNum === 1) {
    renderNewsList("trade1-deck-news", state.persistent_deck_news || []);
    renderNewsList("trade1-news",      r.phase1_news || []);
  } else {
    renderNewsList("trade2-deck-news", state.persistent_deck_news || []);
    const allNews = [...(r.phase1_news || []), ...(r.phase2_news || [])];
    renderNewsList("trade2-news", allNews);
  }
}

// ── Acted panel ───────────────────────────────────────────────────────────────

function renderActedPanel(state, phaseNum) {
  const trades    = phaseNum === 1 ? (state.round?.phase1_trades || []) : (state.round?.phase2_trades || []);
  const actedSet  = new Set(trades.map(t => t.player_name));
  const players   = (state.players || []).filter(p => !p.eliminated);
  const targetId  = phaseNum === 1 ? "phase1-acted-status" : "phase2-acted-status";
  const el        = document.getElementById(targetId);

  el.innerHTML = players.map(p => `
    <div class="acted-row ${actedSet.has(p.name) ? "done" : ""}">
      <span>${escHtml(p.name)}</span>
      <span class="acted-dot"></span>
    </div>
  `).join("");
}

// ── Results ───────────────────────────────────────────────────────────────────

function renderResults(state, isFinal) {
  setText("results-title",      isFinal ? "Final Standings" : `Round ${state.round_index} Results`);
  setText("results-true-value", state.round?.true_value ?? "—");

  const announced = state.round?.announced_deck_news_end_of_round;
  setText("results-context",
    announced ? `New deck news: ${announced.headline}` : "No new persistent deck news this round"
  );

  // Leaderboard table
  const tbody = document.getElementById("leaderboard-body");
  const lb    = state.leaderboard || [];
  tbody.innerHTML = lb.map(entry => {
    const isMe     = entry.name === myName;
    const rankCls  = entry.rank <= 3 ? `rank-${entry.rank}` : "";
    const pnlCls   = entry.round_pnl >= 0 ? "pnl-pos" : "pnl-neg";
    return `
      <tr class="${rankCls} ${isMe ? "is-me" : ""} ${entry.eliminated ? "is-eliminated" : ""}">
        <td>${entry.rank}</td>
        <td>${escHtml(entry.name)}${isMe ? " <small>(you)</small>" : ""}${entry.eliminated ? " <small>(out)</small>" : ""}</td>
        <td class="${pnlCls}">${signed(entry.phase1_pnl)}</td>
        <td class="${pnlCls}">${signed(entry.phase2_pnl)}</td>
        <td class="${pnlCls}">${signed(entry.round_pnl)}</td>
        <td>£${Number(entry.cash).toFixed(2)}</td>
      </tr>`;
  }).join("");

  renderNewsList("results-deck-news", state.persistent_deck_news || []);

  const msg = document.getElementById("next-round-msg");
  msg.textContent       = isFinal ? "Game over" : "Next round starting soon…";
  msg.style.animationName = isFinal ? "none" : "";
}

// ── Eliminated ────────────────────────────────────────────────────────────────

function renderEliminated(state) {
  setText("elim-cash", `£${Number(state.player_data?.my_cash ?? 0).toFixed(2)}`);
}

// ── Cards ─────────────────────────────────────────────────────────────────────

function renderCards(state, containerId) {
  const container = document.getElementById(containerId);
  if (!container || !state.round) return;

  const revealed   = state.round.revealed_cards || [];
  const totalCards = state.config?.card_no || 5;
  const hiddenCount = Math.max(0, totalCards - revealed.length);

  let html = revealed.map((v, i) => `
    <div class="card-chip" style="animation-delay:${i * 0.06}s">${v}</div>
  `).join("");

  for (let i = 0; i < hiddenCount; i++) {
    html += `<div class="card-chip hidden-card" style="animation-delay:${(revealed.length + i) * 0.06}s">?</div>`;
  }
  container.innerHTML = html;
}

// ── News lists ────────────────────────────────────────────────────────────────

/**
 * FIX: Previous version had a bug where news objects from the server
 * weren't being unpacked correctly. We now handle both array-of-objects
 * and flat array formats.
 */
function renderNewsList(containerId, newsItems) {
  const el = document.getElementById(containerId);
  if (!el) return;

  // Normalise: filter out nulls/empties
  const items = (newsItems || []).filter(item => item && (item.headline || item.text));

  if (!items.length) {
    el.innerHTML = `<div class="news-empty">No news</div>`;
    return;
  }

  el.innerHTML = items.map(item => {
    const tier     = item.tier || "—";
    const headline = item.headline || item.text || "";
    const affectsEv = item.affects_ev;
    const borderColor = affectsEv ? "var(--green)" : "var(--yellow)";
    return `
      <div class="news-item" style="border-left-color: ${borderColor}">
        <div class="news-tier">TIER ${escHtml(String(tier))} ${affectsEv ? "· EV IMPACT" : ""}</div>
        <div class="news-headline">${escHtml(headline)}</div>
      </div>`;
  }).join("");
}

// ── Trading actions ───────────────────────────────────────────────────────────

function submitAction(action, phaseNum) {
  const qtyEl  = document.getElementById(phaseNum === 1 ? "trade1-qty" : "trade2-qty");
  const errorEl = document.getElementById(phaseNum === 1 ? "trade1-error" : "trade2-error");
  const qty    = parseInt(qtyEl?.value || "0", 10);

  if (action !== "pass" && (!Number.isInteger(qty) || qty <= 0)) {
    errorEl.textContent = "Enter a valid quantity";
    return;
  }

  socket.emit("PlayerAction", {
    game_id: gameId,
    action,
    qty: action === "pass" ? 0 : qty,
  });
}

document.getElementById("btn-buy-1").addEventListener("click",  () => submitAction("buy",  1));
document.getElementById("btn-sell-1").addEventListener("click", () => submitAction("sell", 1));
document.getElementById("btn-pass-1").addEventListener("click", () => submitAction("pass", 1));
document.getElementById("btn-buy-2").addEventListener("click",  () => submitAction("buy",  2));
document.getElementById("btn-sell-2").addEventListener("click", () => submitAction("sell", 2));
document.getElementById("btn-pass-2").addEventListener("click", () => submitAction("pass", 2));

// ── Screen / panel helpers ────────────────────────────────────────────────────

function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => {
    s.classList.remove("active");
    s.style.display = "none";
  });
  const target = document.getElementById(id);
  target.style.display = "flex";
  requestAnimationFrame(() => target.classList.add("active"));
}

function showPanel(id) {
  document.querySelectorAll(".game-panel").forEach(p => p.classList.add("hidden"));
  document.getElementById(id)?.classList.remove("hidden");
}

function showToast(msg, type = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast show ${type}`;
  setTimeout(() => el.classList.remove("show"), 2500);
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatMoney(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `£${Number(v).toFixed(2)}`;
}

function signed(v) {
  const n = Number(v || 0);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.getElementById("screen-lobby").style.display = "flex";