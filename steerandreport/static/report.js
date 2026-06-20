const reportState = {
  transcript: null,
  clmProfile: null,
  fileName: "",
};

const reportEls = {
  modeToggle: document.querySelector("#reportModeToggle"),
  uploadBtn: document.querySelector("#uploadReportBtn"),
  uploadInput: document.querySelector("#reportUploadInput"),
  generateBtn: document.querySelector("#generateReportBtn"),
  report: document.querySelector("#report"),
  status: document.querySelector("#reportStatus"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(message) {
  reportEls.status.querySelector("span:last-child").textContent = message;
}

function renderList(items) {
  return `<ul class="report-list">${(items || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderReport(output) {
  reportEls.report.innerHTML = `
    <section class="report-block summary-block">
      <h3>Executive Summary</h3>
      <p>${escapeHtml(output.executive_summary)}</p>
    </section>
    <section class="report-block">
      <h3>Client Objectives</h3>
      ${renderList(output.client_objectives)}
    </section>
    <section class="report-block">
      <h3>Relationship Signals</h3>
      ${renderList(output.relationship_signals)}
    </section>
    <section class="report-block">
      <h3>Unresolved Questions</h3>
      ${renderList(output.unresolved_questions)}
    </section>
    <section class="report-block">
      <h3>Improvement Opportunities</h3>
      ${renderList(output.improvement_opportunities)}
    </section>
    <section class="report-block">
      <h3>Suitability & Compliance</h3>
      ${renderList(output.suitability_and_compliance)}
    </section>
    <section class="report-block">
      <h3>Recommended Follow-Ups</h3>
      ${renderList(output.recommended_follow_ups)}
    </section>
    <section class="report-block note-block">
      <h3>Draft Client Note</h3>
      <p>${escapeHtml(output.draft_client_note)}</p>
    </section>
  `;
}

function normalizeTranscript(turns) {
  if (!Array.isArray(turns)) throw new Error("Transcript JSON must contain an array of turns.");
  const normalized = turns
    .map((turn, index) => {
      if (typeof turn === "string") return { role: "unknown", speaker: "Speaker", text: turn.trim(), turn_index: index };
      const text = String(turn?.text ?? turn?.content ?? "").trim();
      if (!text) return null;
      const rawRole = String(turn.role ?? turn.speaker ?? "unknown").toLowerCase();
      const role = rawRole.includes("client") ? "client" : rawRole === "rm" || rawRole.includes("relationship") ? "rm" : "unknown";
      return {
        role,
        speaker: role === "rm" ? "Relationship Manager" : role === "client" ? "Client" : "Speaker",
        text,
        turn_index: index,
      };
    })
    .filter(Boolean);
  if (!normalized.length) throw new Error("The uploaded transcript contains no readable turns.");
  return normalized;
}

function parseTextTranscript(raw) {
  const turns = [];
  let current = null;
  const speakerPattern = /^\*{0,2}(RM|Relationship Manager|Client)\s*:\*{0,2}\s*(.*)$/i;

  for (const rawLine of raw.split(/\r?\n/)) {
    const line = rawLine.trim();
    const match = line.match(speakerPattern);
    if (match) {
      if (current?.parts.length) turns.push(current);
      const role = /client/i.test(match[1]) ? "client" : "rm";
      current = { role, speaker: role === "rm" ? "Relationship Manager" : "Client", parts: [match[2]] };
      continue;
    }
    if (current && line && !line.startsWith("#") && !line.startsWith(">") && line !== "---") current.parts.push(line);
  }

  if (current?.parts.length) turns.push(current);
  if (!turns.length && raw.trim()) turns.push({ role: "unknown", speaker: "Uploaded transcript", parts: [raw.trim()] });
  return normalizeTranscript(turns.map((turn) => ({ ...turn, text: turn.parts.join(" ") })));
}

function parseUpload(raw, fileName) {
  const looksLikeJson = fileName.toLowerCase().endsWith(".json") || /^[\s\r\n]*[\[{]/.test(raw);
  if (!looksLikeJson) return { transcript: parseTextTranscript(raw), clmProfile: null };

  const parsed = JSON.parse(raw);
  if (Array.isArray(parsed)) return { transcript: normalizeTranscript(parsed), clmProfile: null };
  const transcript = parsed.transcript ?? parsed.turns;
  if (!transcript) throw new Error("JSON must contain a transcript or turns array.");
  return { transcript: normalizeTranscript(transcript), clmProfile: parsed.clmProfile ?? parsed.clm_profile ?? null };
}

async function handleUpload() {
  const file = reportEls.uploadInput.files?.[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    setStatus("Upload failed: files must be smaller than 2 MB.");
    return;
  }

  reportEls.uploadBtn.disabled = true;
  try {
    const parsed = parseUpload(await file.text(), file.name);
    reportState.transcript = parsed.transcript;
    reportState.clmProfile = parsed.clmProfile;
    reportState.fileName = file.name;
    setStatus(`Loaded ${file.name} · ${parsed.transcript.length} turns. Enable DeepSeek for a file-specific report, then generate.`);
  } catch (error) {
    reportState.transcript = null;
    reportState.clmProfile = null;
    reportState.fileName = "";
    setStatus(`Upload failed: ${error.message}`);
  } finally {
    reportEls.uploadBtn.disabled = false;
    reportEls.uploadInput.value = "";
  }
}

async function generateReport() {
  const mode = reportEls.modeToggle.checked ? "deepseek" : "mock";
  reportEls.generateBtn.disabled = true;
  reportEls.status.classList.add("loading");
  setStatus(`Generating ${reportState.fileName || "demo transcript"} in ${mode} mode...`);

  try {
    const payload = { mode };
    if (reportState.transcript) payload.transcript = reportState.transcript;
    if (reportState.clmProfile) payload.clmProfile = reportState.clmProfile;
    const response = await fetch("/api/run-pipeline", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    const pipeline = result.fallback || result;
    const finalReportRound = pipeline.rounds?.[2];
    if (!finalReportRound?.output) throw new Error(result.error || "The report pipeline returned no report.");
    renderReport(finalReportRound.output);
    setStatus(
      result.error
        ? "DeepSeek failed; the mock report is shown."
        : mode === "mock" && reportState.fileName
          ? "Mock preview generated. Uploaded content is used when DeepSeek mode is enabled."
          : `Generated ${pipeline.mode} report from ${reportState.fileName || "the demo transcript"}.`,
    );
  } catch (error) {
    setStatus(`Report failed: ${error.message}`);
  } finally {
    reportEls.status.classList.remove("loading");
    reportEls.generateBtn.disabled = false;
  }
}

reportEls.uploadBtn.addEventListener("click", () => reportEls.uploadInput.click());
reportEls.uploadInput.addEventListener("change", handleUpload);
reportEls.generateBtn.addEventListener("click", generateReport);
generateReport();
