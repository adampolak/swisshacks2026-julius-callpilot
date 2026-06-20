const state = {
  clmProfile: null,
  transcript: [],
  streamTimer: null,
  pollTimer: null,
  transcriptionRunning: false,
  steeringHistory: [],
  steeringQueue: Promise.resolve(),
  lastRenderedTurn: -1,
  lastAnalyzedTurn: -1,
  currentTranscriptText: null,
  guidanceCards: [],
  guidanceSeq: 0,
  activeGuidanceId: null,
  activeContextView: "clm",
  sourcePanels: { bank_api: null, news_api: null },
  contextHighlights: { bank_api: [], news_api: [], clm: [] },
};

const MAX_VISIBLE_CUES = 3;
const MAX_GUIDANCE_HISTORY = 12;

const els = {
  clmGrid: document.querySelector("#clmGrid"),
  clientTitle: document.querySelector("#client-title"),
  rmName: document.querySelector("#rmName"),
  contextView: document.querySelector("#contextView"),
  contextSourceHeader: document.querySelector("#contextSourceHeader"),
  contextContent: document.querySelector("#contextContent"),
  hardBoundary: document.querySelector("#hardBoundary"),
  transcript: document.querySelector("#transcript"),
  guidance: document.querySelector("#guidance"),
  steeringEmpty: document.querySelector("#steeringEmpty"),
  latencyBadge: document.querySelector("#latencyBadge"),
  runBtn: document.querySelector("#runBtn"),
  simulateBtn: document.querySelector("#simulateBtn"),
  modelLine: document.querySelector("#modelLine"),
  streamStatus: document.querySelector("#streamStatus"),
  turnCounter: document.querySelector("#turnCounter"),
};

function setStreamStatus(label, detail = "") {
  els.streamStatus.textContent = label;
  els.streamStatus.title = detail;
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok || result.error) {
    throw new Error(result.error || `Request failed with HTTP ${response.status}`);
  }
  return result;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightText(value, terms = []) {
  const text = String(value ?? "");
  const validTerms = terms.filter(Boolean).sort((a, b) => b.length - a.length);
  if (!validTerms.length) return escapeHtml(text);
  const pattern = new RegExp(`(${validTerms.map(escapeRegex).join("|")})`, "gi");
  return text
    .split(pattern)
    .map((part, index) => (index % 2 ? `<mark>${escapeHtml(part)}</mark>` : escapeHtml(part)))
    .join("");
}

function hasHighlight(value, terms = []) {
  const text = String(value ?? "").toLowerCase();
  return terms.some((term) => term && text.includes(String(term).toLowerCase()));
}

function cleanMarkdown(value) {
  return String(value ?? "").replaceAll("**", "").replaceAll("`", "").replaceAll("*", "").trim();
}

function renderClmDocument(markdown, highlights) {
  const lines = String(markdown || "").split(/\r?\n/);
  return lines
    .map((rawLine) => {
      const line = rawLine.trim();
      if (!line || /^-{3,}$/.test(line) || /^\|?\s*[-:]+(?:\s*\|\s*[-:]+)+\s*\|?$/.test(line)) return "";

      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) {
        const level = heading[1].length === 1 ? "h3" : "h4";
        const text = cleanMarkdown(heading[2]);
        return `<${level} class="${hasHighlight(text, highlights) ? "is-match" : ""}">${highlightText(text, highlights)}</${level}>`;
      }

      if (line.startsWith("|")) {
        const cells = line
          .slice(1, line.endsWith("|") ? -1 : undefined)
          .split("|")
          .map((cell) => {
            const text = cleanMarkdown(cell);
            return `<span class="${hasHighlight(text, highlights) ? "is-match" : ""}">${highlightText(text, highlights)}</span>`;
          })
          .join("");
        return `<div class="clm-table-row">${cells}</div>`;
      }

      const quote = line.match(/^>\s*(.+)$/);
      if (quote) {
        const text = cleanMarkdown(quote[1]);
        return `<p class="clm-note ${hasHighlight(text, highlights) ? "is-match" : ""}">${highlightText(text, highlights)}</p>`;
      }

      const listItem = line.match(/^(?:[-+]\s+|\d+\.\s+)(.+)$/);
      if (listItem) {
        const text = cleanMarkdown(listItem[1]);
        return `<p class="source-line ${hasHighlight(text, highlights) ? "is-match" : ""}">${highlightText(text, highlights)}</p>`;
      }

      const text = cleanMarkdown(line);
      return `<p class="${hasHighlight(text, highlights) ? "is-match" : ""}">${highlightText(text, highlights)}</p>`;
    })
    .join("");
}

function setActiveTab(view) {
  document.querySelectorAll(".dossier-tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function renderEmptySource(view) {
  const label = view === "bank_api" ? "Bank API" : "News API";
  els.contextSourceHeader.innerHTML = `
    <div>
      <p class="meta-label">${label}</p>
      <h3>Awaiting a relevant question</h3>
    </div>
    <span class="source-state">Standby</span>
  `;
  els.contextContent.innerHTML = `<p class="source-empty">Relevant internal knowledge will appear here when the client asks a detailed question.</p>`;
}

function renderContext(view, highlights = state.contextHighlights[view] || []) {
  state.activeContextView = view;
  state.contextHighlights[view] = highlights;
  setActiveTab(view);
  els.contextView.scrollTop = 0;

  if (view === "clm") {
    els.contextSourceHeader.innerHTML = `
      <div>
        <p class="meta-label">Client lifecycle management</p>
        <h3>Complete client record</h3>
      </div>
      <span class="source-state">Verified</span>
    `;
    els.contextContent.innerHTML = `<div class="clm-document">${renderClmDocument(state.clmProfile?.profile_markdown, highlights)}</div>`;
    requestAnimationFrame(() => els.contextView.querySelector("mark")?.scrollIntoView({ block: "center" }));
    return;
  }

  const source = state.sourcePanels[view];
  if (!source) {
    renderEmptySource(view);
    return;
  }

  els.contextSourceHeader.innerHTML = `
    <div>
      <p class="meta-label">${escapeHtml(source.label)}</p>
      <h3>${escapeHtml(source.title)}</h3>
    </div>
    <span class="source-state">${escapeHtml(source.status)}</span>
  `;
  els.contextContent.innerHTML = `
    <div class="source-query">
      <span class="meta-label">Retrieval</span>
      <p>${highlightText(source.query, highlights)}</p>
    </div>
    <div class="source-items">
      ${(source.items || [])
        .map(
          (item) => `
            <article class="source-item ${hasHighlight(`${item.title} ${item.text}`, highlights) ? "is-match" : ""}">
              <h4>${highlightText(item.title, highlights)}</h4>
              <p>${highlightText(item.text, highlights)}</p>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
  requestAnimationFrame(() => els.contextView.querySelector("mark")?.scrollIntoView({ block: "center" }));
}

function contextSnapshotFromResult(result) {
  const panels = {};
  const highlights = {};

  (result.knowledge_sources || []).forEach((source) => {
    highlights[source.type] = source.highlights || [];
    if (source.type !== "clm") panels[source.type] = source;
  });

  return {
    activeSource: result.active_source || null,
    panels,
    highlights,
  };
}

function applyContextSnapshot(snapshot) {
  if (!snapshot?.activeSource) return;

  Object.entries(snapshot.panels || {}).forEach(([type, source]) => {
    state.sourcePanels[type] = source;
  });
  Object.entries(snapshot.highlights || {}).forEach(([type, highlights]) => {
    state.contextHighlights[type] = highlights;
  });

  renderContext(snapshot.activeSource, snapshot.highlights?.[snapshot.activeSource] || []);
}

function updateKnowledgePanels(result) {
  (result.knowledge_sources || []).forEach((source) => {
    state.contextHighlights[source.type] = source.highlights || [];
    if (source.type !== "clm") state.sourcePanels[source.type] = source;
  });
  if (result.active_source) renderContext(result.active_source);
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
  renderContext("clm");
}

function updateSteeringStatus(result) {
  els.modelLine.textContent = result.model_source || "Local";
  els.latencyBadge.textContent = `${result.latency_ms || 0} ms`;
}

function guidanceSignature(card) {
  const source = (card.sources || ["Conversation"]).join(" / ");
  return [card.category, source, card.text].join("::").toLowerCase();
}

function cueCardMarkup(item, index) {
  const card = item.card;
  const source = (card.sources || ["Conversation"]).join(" / ");
  const selected = item.id === state.activeGuidanceId;
  return `
    <article class="cue-card cue-${card.category.toLowerCase()} ${item.isNew ? "is-new" : ""} ${selected ? "is-selected" : ""}" data-cue-id="${item.id}" role="button" tabindex="0" aria-pressed="${selected}">
      <div class="cue-meta">
        <span>${escapeHtml(card.category)}</span>
        <span class="cue-source">${escapeHtml(source)} </span>
      </div>
      <p>${escapeHtml(card.text)}</p>
    </article>
  `;
}

function animateGuidanceShift(previousRects) {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reducedMotion) return;

  els.guidance.querySelectorAll(".cue-card").forEach((node) => {
    const previous = previousRects.get(node.dataset.cueId);
    if (!previous) return;
    const current = node.getBoundingClientRect();
    const deltaY = previous.top - current.top;
    if (Math.abs(deltaY) < 1) return;
    node.animate(
      [
        { transform: `translateY(${deltaY}px)` },
        { transform: "translateY(0)" },
      ],
      { duration: 260, easing: "cubic-bezier(.2, .72, .18, 1)" }
    );
  });
}

function renderGuidanceStack() {
  els.steeringEmpty.hidden = state.guidanceCards.length > 0;
  els.guidance.classList.toggle("has-cues", state.guidanceCards.length > 0);
  els.guidance.innerHTML = state.guidanceCards.map(cueCardMarkup).join("");
  els.guidance.style.setProperty("--visible-cues", String(Math.min(MAX_VISIBLE_CUES, state.guidanceCards.length || 1)));
}

function activateCueCard(cueId) {
  const item = state.guidanceCards.find((candidate) => candidate.id === cueId);
  if (!item) return;

  state.activeGuidanceId = cueId;
  applyContextSnapshot(item.context);
  renderGuidanceStack();
}

function prependCueCards(result) {
  const cards = (result.cards || []).slice(0, 2);
  updateKnowledgePanels(result);
  if (!cards.length) return;

  const previousRects = new Map(
    Array.from(els.guidance.querySelectorAll(".cue-card"), (node) => [node.dataset.cueId, node.getBoundingClientRect()])
  );
  const existingSignatures = new Set(state.guidanceCards.map((item) => item.signature));
  const nextSignatures = new Set();
  const newItems = cards
    .map((card) => ({ card, signature: guidanceSignature(card) }))
    .filter((item) => {
      if (existingSignatures.has(item.signature) || nextSignatures.has(item.signature)) return false;
      nextSignatures.add(item.signature);
      return true;
    })
    .map((item) => ({
      ...item,
      id: `cue-${Date.now()}-${state.guidanceSeq++}`,
      context: contextSnapshotFromResult(result),
      isNew: true,
    }));

  if (!newItems.length) return;

  state.guidanceCards.forEach((item) => {
    item.isNew = false;
  });
  state.activeGuidanceId = newItems[0].id;
  state.guidanceCards = [...newItems, ...state.guidanceCards].slice(0, MAX_GUIDANCE_HISTORY);
  renderGuidanceStack();
  animateGuidanceShift(previousRects);
  els.guidance.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

function renderSteering(result) {
  updateSteeringStatus(result);
  const cards = (result.cards || []).slice(0, 2);
  if (!cards.length) {
    if (result.active_source) updateKnowledgePanels(result);
    return;
  }

  prependCueCards({ ...result, cards });
}

function clearGuidance() {
  state.guidanceCards = [];
  state.guidanceSeq = 0;
  state.activeGuidanceId = null;
  els.guidance.classList.remove("has-cues");
  els.guidance.innerHTML = "";
  els.guidance.scrollTop = 0;
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
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.streamTimer = null;
  state.pollTimer = null;
  state.steeringHistory = [];
  state.steeringQueue = Promise.resolve();
  state.lastRenderedTurn = -1;
  state.lastAnalyzedTurn = -1;
  state.currentTranscriptText = null;
  state.sourcePanels = { bank_api: null, news_api: null };
  state.contextHighlights = { bank_api: [], news_api: [], clm: [] };
  els.transcript.textContent = "";
  els.turnCounter.textContent = "0";
  setStreamStatus("Ready");
  renderIdle();
  if (state.clmProfile) renderContext("clm");
}

function roleLabel(role) {
  return role === "rm" ? "RM" : "Client";
}

function transcriptTurnMarkup(turn, isInterim = false) {
  return `
    <article class="transcript-turn role-${escapeHtml(turn.role)} ${isInterim ? "is-interim" : ""}" data-turn-index="${escapeHtml(turn.turn_index ?? "")}">
      <span class="turn-role">${escapeHtml(roleLabel(turn.role))}</span>
      <p class="turn-text">${escapeHtml(turn.text)}</p>
    </article>
  `;
}

function renderTranscript(interim = {}) {
  const finalRows = state.transcript.map((turn) => transcriptTurnMarkup(turn)).join("");
  const interimRows = ["rm", "client"]
    .map((role) => ({ role, text: String(interim[role] || "").trim() }))
    .filter((turn) => turn.text)
    .map((turn) => transcriptTurnMarkup(turn, true))
    .join("");

  els.transcript.innerHTML = finalRows + interimRows;
  els.turnCounter.textContent = String(state.transcript.length);
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function queueNewTurnAnalysis() {
  for (let index = state.lastAnalyzedTurn + 1; index < state.transcript.length; index += 1) {
    queueTurnAnalysis(index);
    state.lastAnalyzedTurn = index;
  }
}

function applyTranscriptionSnapshot(snapshot) {
  state.transcript = snapshot.turns || snapshot.transcript || [];
  renderTranscript(snapshot.interim || {});
  queueNewTurnAnalysis();

  if (snapshot.last_error) {
    setStreamStatus("Error", snapshot.last_error);
    return;
  }
  if (snapshot.running) {
    setStreamStatus("Live");
    return;
  }
  if (snapshot.status === "stopped") {
    setStreamStatus("Stopped");
    return;
  }
  setStreamStatus(snapshot.status === "idle" ? "Ready" : snapshot.status || "Ready");
}

async function refreshLiveFeed() {
  try {
    const response = await fetch("/api/transcription");
    const snapshot = await response.json();
    if (snapshot.error) throw new Error(snapshot.error);
    applyTranscriptionSnapshot(snapshot);

    if (state.transcriptionRunning && !snapshot.running && snapshot.status !== "starting") {
      state.transcriptionRunning = false;
      els.simulateBtn.disabled = false;
      els.simulateBtn.textContent = "Start call";
    }
  } catch (error) {
    setStreamStatus("Error", error.message);
  }

  if (state.transcriptionRunning) {
    state.pollTimer = setTimeout(refreshLiveFeed, 650);
  }
}

async function startLiveFeed() {
  state.transcript = [];
  resetStream();
  state.transcriptionRunning = true;
  els.simulateBtn.disabled = true;
  els.simulateBtn.textContent = "Starting";
  setStreamStatus("Starting");

  try {
    const snapshot = await postJson("/api/transcription/start", { reset: true });
    applyTranscriptionSnapshot(snapshot);
    els.simulateBtn.disabled = false;
    els.simulateBtn.textContent = "Stop call";
    refreshLiveFeed();
  } catch (error) {
    state.transcriptionRunning = false;
    els.simulateBtn.disabled = false;
    els.simulateBtn.textContent = "Start call";
    setStreamStatus("Error", error.message);
  }
}

async function stopLiveFeed() {
  state.transcriptionRunning = false;
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  els.simulateBtn.disabled = true;
  els.simulateBtn.textContent = "Stopping";
  setStreamStatus("Stopping");

  try {
    const snapshot = await postJson("/api/transcription/stop");
    applyTranscriptionSnapshot(snapshot);
  } catch (error) {
    setStreamStatus("Error", error.message);
  } finally {
    els.simulateBtn.disabled = false;
    els.simulateBtn.textContent = "Start call";
  }
}

function simulateLiveFeed() {
  if (state.transcriptionRunning) {
    stopLiveFeed();
  } else {
    startLiveFeed();
  }
}

async function loadDemo() {
  const response = await fetch("/api/demo");
  const demo = await response.json();
  state.clmProfile = demo.clmProfile;
  state.transcript = demo.transcript;
  renderClm(state.clmProfile);
  resetStream();
  applyTranscriptionSnapshot(demo.transcription || { turns: state.transcript, status: "idle" });
  if (demo.transcription?.running) {
    state.transcriptionRunning = true;
    els.simulateBtn.textContent = "Stop call";
    refreshLiveFeed();
  }
}

document.querySelectorAll(".dossier-tab").forEach((tab) => {
  tab.addEventListener("click", () => renderContext(tab.dataset.view));
});
els.guidance.addEventListener("click", (event) => {
  const card = event.target.closest(".cue-card");
  if (!card) return;
  activateCueCard(card.dataset.cueId);
});
els.guidance.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest(".cue-card");
  if (!card) return;
  event.preventDefault();
  activateCueCard(card.dataset.cueId);
});
els.runBtn.addEventListener("click", () => window.open("/report.html", "_blank", "noopener,noreferrer"));
els.simulateBtn.addEventListener("click", simulateLiveFeed);

loadDemo().then(() => {
  if (window.location.hash === "#play" && !state.transcriptionRunning) simulateLiveFeed();
});
