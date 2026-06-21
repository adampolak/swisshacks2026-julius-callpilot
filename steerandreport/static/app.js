const state = {
  clmProfile: null,
  transcript: [],
  streamTimer: null,
  steeringHistory: [],
  steeringQueue: Promise.resolve(),
  lastRenderedTurn: -1,
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
  state.streamTimer = null;
  state.steeringHistory = [];
  state.steeringQueue = Promise.resolve();
  state.lastRenderedTurn = -1;
  state.currentTranscriptText = null;
  state.sourcePanels = { bank_api: null, news_api: null };
  state.contextHighlights = { bank_api: [], news_api: [], clm: [] };
  els.transcript.textContent = "";
  els.turnCounter.textContent = `0 / ${state.transcript.length}`;
  els.streamStatus.textContent = "Ready";
  renderIdle();
  if (state.clmProfile) renderContext("clm");
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

function randomBetween(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function nextSpeechChunk(text, offset) {
  const wordLimit = [1, 2, 2, 3, 3, 3, 4][randomBetween(0, 6)];
  const tokenPattern = /\s*\S+/g;
  tokenPattern.lastIndex = offset;

  let nextOffset = offset;
  let wordCount = 0;
  while (wordCount < wordLimit) {
    const match = tokenPattern.exec(text);
    if (!match) break;

    wordCount += 1;
    nextOffset = tokenPattern.lastIndex;

    const token = match[0].trimEnd();
    if (/[.!?]$/.test(token)) break;
    if (/[,;:]$/.test(token) && wordCount >= 2) break;
  }

  if (nextOffset === offset) {
    return { chunk: text.slice(offset), nextOffset: text.length };
  }

  return { chunk: text.slice(offset, nextOffset), nextOffset };
}

function getChunkDelay(chunk) {
  const text = chunk.trim();
  if (!text) return randomBetween(180, 280);
  if (/[.!?]$/.test(text)) return randomBetween(520, 840);
  if (/[,;:]$/.test(text)) return randomBetween(320, 480);

  const wordCount = text.split(/\s+/).filter(Boolean).length;
  const min = wordCount > 2 ? 220 : 180;
  const max = Math.min(360, 280 + wordCount * 20);
  return randomBetween(min, max);
}

function typeTurn(turnIndex, offset = 0) {
  if (turnIndex >= state.transcript.length) {
    state.streamTimer = null;
    els.simulateBtn.disabled = false;
    els.simulateBtn.textContent = "Replay call";
    els.streamStatus.textContent = "Complete";
    return;
  }

  const text = state.transcript[turnIndex].text;
  if (offset === 0) startTranscriptTurn(turnIndex);
  if (offset >= text.length) {
    queueTurnAnalysis(turnIndex);
    els.turnCounter.textContent = `${turnIndex + 1} / ${state.transcript.length}`;
    state.streamTimer = setTimeout(() => typeTurn(turnIndex + 1, 0), randomBetween(560, 840));
    return;
  }

  const { chunk, nextOffset } = nextSpeechChunk(text, offset);
  state.currentTranscriptText.textContent += chunk;
  els.transcript.scrollTop = els.transcript.scrollHeight;

  state.streamTimer = setTimeout(() => typeTurn(turnIndex, nextOffset), getChunkDelay(chunk));
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
  if (window.location.hash === "#play") simulateLiveFeed();
});
