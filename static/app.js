const state = {
  clmProfile: null,
  transcript: [],
  streamTimer: null,
  steeringHistory: [],
  steeringQueue: Promise.resolve(),
  lastRenderedTurn: -1,
  currentTranscriptText: null,
  activeGuidanceShownAt: 0,
  pendingGuidance: null,
  guidanceSwapTimer: null,
  guidanceTransitionTimer: null,
  guidanceTransitioning: false,
};

const MIN_GUIDANCE_MS = 10000;
const GUIDANCE_EXIT_MS = 420;

const els = {
  clmGrid: document.querySelector("#clmGrid"),
  clientTitle: document.querySelector("#client-title"),
  rmName: document.querySelector("#rmName"),
  contextLabel: document.querySelector("#contextLabel"),
  contextList: document.querySelector("#contextList"),
  hardBoundary: document.querySelector("#hardBoundary"),
  transcript: document.querySelector("#transcript"),
  guidance: document.querySelector("#guidance"),
  steeringEmpty: document.querySelector("#steeringEmpty"),
  latencyBadge: document.querySelector("#latencyBadge"),
  runBtn: document.querySelector("#runBtn"),
  simulateBtn: document.querySelector("#simulateBtn"),
  modeToggle: document.querySelector("#modeToggle"),
  modelLine: document.querySelector("#modelLine"),
  streamStatus: document.querySelector("#streamStatus"),
  turnCounter: document.querySelector("#turnCounter"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderClm(profile) {
  els.clientTitle.textContent = profile.primary_client;
  els.rmName.textContent = profile.relationship_manager;
  els.hardBoundary.textContent = profile.boundaries[0];
  const metrics = [
    ["Total wealth", profile.total_wealth],
    ["Managed", profile.managed_assets],
    ["Risk", profile.risk_profile],
    ["Cash", profile.cash_weight],
  ];
  els.clmGrid.innerHTML = metrics
    .map(
      ([label, value]) => `
        <div class="wealth-metric">
          <dt>${escapeHtml(label)}</dt>
          <dd>${escapeHtml(value)}</dd>
        </div>
      `,
    )
    .join("");
  renderContext("portfolio");
}

function renderContext(view) {
  const views = {
    portfolio: ["Portfolio signals", state.clmProfile.portfolio_snapshot],
    family: ["Family context", state.clmProfile.family_context],
    commitments: ["Outstanding commitments", state.clmProfile.open_commitments],
  };
  const [label, items] = views[view];
  els.contextLabel.textContent = label;
  els.contextList.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  document.querySelectorAll(".dossier-tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function updateSteeringStatus(result) {
  els.modelLine.textContent = result.model_source || "Local";
  els.latencyBadge.textContent = `${result.latency_ms || 0} ms`;
}

function renderCueCards(result) {
  const cards = (result.cards || []).slice(0, 2);
  els.steeringEmpty.hidden = cards.length > 0;
  els.guidance.classList.toggle("has-cues", cards.length > 0);
  els.guidance.classList.remove("is-exiting");
  els.guidance.innerHTML = cards
    .map(
      (card, index) => `
        <article class="cue-card cue-${card.category.toLowerCase()}">
          <div class="cue-meta">
            <span>${escapeHtml(card.category)}</span>
            <span>0${index + 1}</span>
          </div>
          <p>${escapeHtml(card.text)}</p>
        </article>
      `,
    )
    .join("");
  state.activeGuidanceShownAt = cards.length ? performance.now() : 0;
}

function finishGuidanceSwap() {
  const next = state.pendingGuidance;
  state.pendingGuidance = null;
  state.guidanceTransitioning = false;
  state.guidanceTransitionTimer = null;
  if (next) renderCueCards(next);
}

function beginGuidanceSwap() {
  if (state.guidanceTransitioning || !state.pendingGuidance) return;
  state.guidanceTransitioning = true;
  els.guidance.classList.add("is-exiting");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  state.guidanceTransitionTimer = setTimeout(finishGuidanceSwap, reducedMotion ? 0 : GUIDANCE_EXIT_MS);
}

function scheduleGuidanceSwap() {
  if (state.guidanceSwapTimer) clearTimeout(state.guidanceSwapTimer);
  const elapsed = performance.now() - state.activeGuidanceShownAt;
  const wait = Math.max(0, MIN_GUIDANCE_MS - elapsed);
  state.guidanceSwapTimer = setTimeout(() => {
    state.guidanceSwapTimer = null;
    beginGuidanceSwap();
  }, wait);
}

function renderSteering(result) {
  updateSteeringStatus(result);
  const cards = (result.cards || []).slice(0, 2);
  if (!cards.length) return;

  const next = { ...result, cards };
  if (!state.activeGuidanceShownAt && !state.guidanceTransitioning) {
    renderCueCards(next);
    return;
  }

  state.pendingGuidance = next;
  scheduleGuidanceSwap();
}

function clearGuidance() {
  if (state.guidanceSwapTimer) clearTimeout(state.guidanceSwapTimer);
  if (state.guidanceTransitionTimer) clearTimeout(state.guidanceTransitionTimer);
  state.guidanceSwapTimer = null;
  state.guidanceTransitionTimer = null;
  state.guidanceTransitioning = false;
  state.pendingGuidance = null;
  state.activeGuidanceShownAt = 0;
  els.guidance.classList.remove("has-cues", "is-exiting");
  els.guidance.innerHTML = "";
  els.steeringEmpty.hidden = false;
}

function renderIdle() {
  clearGuidance();
  updateSteeringStatus({ model_source: "Idle", latency_ms: 0 });
}

async function analyzeTurn(turnIndex) {
  const response = await fetch("/api/steering", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      clmProfile: state.clmProfile,
      turns: state.transcript.slice(0, turnIndex + 1),
      latestTurnIndex: turnIndex,
      history: state.steeringHistory,
      allowBigModel: els.modeToggle.checked,
    }),
  });
  const result = await response.json();
  if (result.error) return;

  state.steeringHistory.push({
    turn_index: turnIndex,
    output: result.output,
    cards: result.cards,
  });
  if (turnIndex >= state.lastRenderedTurn) {
    state.lastRenderedTurn = turnIndex;
    renderSteering(result);
  }
}

function queueTurnAnalysis(turnIndex) {
  state.steeringQueue = state.steeringQueue
    .then(() => analyzeTurn(turnIndex))
    .catch(() => undefined);
}

function resetStream() {
  if (state.streamTimer) clearTimeout(state.streamTimer);
  state.streamTimer = null;
  state.steeringHistory = [];
  state.steeringQueue = Promise.resolve();
  state.lastRenderedTurn = -1;
  state.currentTranscriptText = null;
  els.transcript.textContent = "";
  els.turnCounter.textContent = `0 / ${state.transcript.length}`;
  els.streamStatus.textContent = "Ready";
  renderIdle();
}

function startTranscriptTurn(turnIndex) {
  const turn = state.transcript[turnIndex];
  const row = document.createElement("article");
  row.className = `transcript-turn role-${turn.role}`;
  row.dataset.turnIndex = String(turnIndex);

  const role = document.createElement("span");
  role.className = "turn-role";
  role.textContent = turn.role === "rm" ? "RM" : "Client";

  const text = document.createElement("p");
  text.className = "turn-text";

  row.append(role, text);
  els.transcript.append(row);
  state.currentTranscriptText = text;
}

function typeTurn(turnIndex, charIndex = 0) {
  if (turnIndex >= state.transcript.length) {
    state.streamTimer = null;
    els.simulateBtn.disabled = false;
    els.simulateBtn.textContent = "Replay call";
    els.streamStatus.textContent = "Complete";
    return;
  }

  const text = state.transcript[turnIndex].text;
  if (charIndex === 0) startTranscriptTurn(turnIndex);
  if (charIndex >= text.length) {
    queueTurnAnalysis(turnIndex);
    els.turnCounter.textContent = `${turnIndex + 1} / ${state.transcript.length}`;
    state.streamTimer = setTimeout(() => typeTurn(turnIndex + 1, 0), 260);
    return;
  }

  const char = text[charIndex];
  state.currentTranscriptText.textContent += char;
  els.transcript.scrollTop = els.transcript.scrollHeight;

  const delay = /[.?]/.test(char) ? 165 : char === "," ? 85 : char === " " ? 34 : 20;
  state.streamTimer = setTimeout(() => typeTurn(turnIndex, charIndex + 1), delay);
}

function simulateLiveFeed() {
  resetStream();
  els.simulateBtn.disabled = true;
  els.simulateBtn.textContent = "Call running";
  els.streamStatus.textContent = "Live";
  typeTurn(0, 0);
}

async function loadDemo() {
  const response = await fetch("/api/demo");
  const demo = await response.json();
  state.clmProfile = demo.clmProfile;
  state.transcript = demo.transcript;
  renderClm(state.clmProfile);
  resetStream();
}

document.querySelectorAll(".dossier-tab").forEach((tab) => {
  tab.addEventListener("click", () => renderContext(tab.dataset.view));
});
els.runBtn.addEventListener("click", () => window.open("/report.html", "_blank", "noopener,noreferrer"));
els.simulateBtn.addEventListener("click", simulateLiveFeed);

loadDemo().then(() => {
  if (window.location.hash === "#play") simulateLiveFeed();
});
