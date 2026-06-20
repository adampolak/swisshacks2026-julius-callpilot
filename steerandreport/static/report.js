const reportEls = {
  modeToggle: document.querySelector("#reportModeToggle"),
  downloadBtn: document.querySelector("#downloadReportBtn"),
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

async function generateReport() {
  const mode = reportEls.modeToggle.checked ? "deepseek" : "mock";
  reportEls.generateBtn.disabled = true;
  reportEls.status.classList.add("loading");
  setStatus(`Generating report in ${mode} mode...`);

  try {
    const response = await fetch("/api/run-pipeline", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const result = await response.json();
    const pipeline = result.fallback || result;
    const finalReportRound = pipeline.rounds?.[2];
    if (!finalReportRound?.output) throw new Error(result.error || "The report pipeline returned no report.");
    renderReport(finalReportRound.output);
    setStatus(result.error ? "DeepSeek failed; the mock report is shown." : `Generated ${pipeline.mode} report.`);
  } catch (error) {
    setStatus(`Report failed: ${error.message}`);
  } finally {
    reportEls.status.classList.remove("loading");
    reportEls.generateBtn.disabled = false;
  }
}

reportEls.downloadBtn.addEventListener("click", () => window.print());
reportEls.generateBtn.addEventListener("click", generateReport);
generateReport();
