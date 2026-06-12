// All requests go to this app's own backend — the browser never contacts a
// third-party API (the TTB network firewall would block it).

"use strict";

const MAX_FILE_MB = 10;
const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024;
const IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".gif"];

const el = (id) => document.getElementById(id);
let selectedLabels = []; // File objects chosen in step 1
let datasetFile = null;  // optional modified product catalog (CSV/XLSX)
let lastResults = null;  // last extraction results, echoed back for export
let lastCatalog = [];    // catalog the results were matched against
let lastTotalSeconds = null;  // server-side processing time
let lastClientSeconds = null; // end-to-end time incl. upload, measured here

// ---------------------------------------------------------------- helpers

function showError(message) {
  const banner = el("error-banner");
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function clearError() {
  el("error-banner").classList.add("hidden");
}

let loadingTimer = null;

function setLoading(active, text) {
  clearInterval(loadingTimer);
  const base = text || "Checking labels…";
  el("loading-text").textContent = base;
  el("loading").classList.toggle("hidden", !active);
  el("extract-button").disabled = active;
  if (active) {
    const started = Date.now();
    loadingTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      el("loading-text").textContent = `${base} ${seconds}s`;
    }, 1000);
  }
}

function hasImageExtension(name) {
  const lower = name.toLowerCase();
  return IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

// --------------------------------------------------- step 1: label images

el("label-input").addEventListener("change", (event) => {
  clearError();
  for (const file of event.target.files) {
    if (!hasImageExtension(file.name)) {
      showError(`"${file.name}" is not a supported image (JPEG, PNG, WebP, or GIF). It was skipped.`);
      continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      showError(`"${file.name}" is larger than ${MAX_FILE_MB}MB and was skipped.`);
      continue;
    }
    selectedLabels.push(file);
  }
  event.target.value = "";
  renderLabelList();
});

function renderLabelList() {
  const list = el("label-list");
  list.innerHTML = "";
  selectedLabels.forEach((file, index) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      selectedLabels.splice(index, 1);
      renderLabelList();
    });
    item.append(name, remove);
    list.appendChild(item);
  });
}

// ------------------------------------------------------ product catalog

el("dataset-button").addEventListener("click", async () => {
  clearError();
  const button = el("dataset-button");
  button.disabled = true;
  try {
    const response = await fetch("/api/dataset");
    if (!response.ok) {
      showError("The dataset could not be downloaded. Please try again.");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "base-product-dataset.xlsx";
    link.click();
    URL.revokeObjectURL(url);
  } catch {
    showError("Could not reach the server. Please try again.");
  } finally {
    button.disabled = false;
  }
});

el("dataset-input").addEventListener("change", (event) => {
  clearError();
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const lower = file.name.toLowerCase();
  if (!lower.endsWith(".csv") && !lower.endsWith(".xlsx")) {
    showError(`"${file.name}" is not a CSV or Excel (.xlsx) file.`);
    return;
  }
  if (file.size > MAX_FILE_BYTES) {
    showError(`"${file.name}" is larger than ${MAX_FILE_MB}MB.`);
    return;
  }
  datasetFile = file;
  el("dataset-status").textContent = `Will match against ${file.name}.`;
  el("dataset-remove").classList.remove("hidden");
});

el("dataset-remove").addEventListener("click", () => {
  datasetFile = null;
  el("dataset-status").textContent = "";
  el("dataset-remove").classList.add("hidden");
});

el("match-toggle").addEventListener("change", (event) => {
  el("catalog-controls").classList.toggle("disabled-zone", !event.target.checked);
});

// --------------------------------------------------------------- extract

el("extract-button").addEventListener("click", async () => {
  clearError();
  el("results-section").classList.add("hidden");

  if (selectedLabels.length === 0) {
    showError("Please add at least one label image first.");
    return;
  }

  const matchCatalog = el("match-toggle").checked;
  const modelMode = document.querySelector('input[name="model-mode"]:checked').value;
  const body = new FormData();
  for (const file of selectedLabels) body.append("labels", file);
  body.append("match_catalog", matchCatalog ? "true" : "false");
  body.append("model_mode", modelMode);
  if (matchCatalog && datasetFile) body.append("dataset", datasetFile);

  setLoading(true, selectedLabels.length > 1
    ? `Checking ${selectedLabels.length} labels…`
    : "Checking label…");

  const startedAt = Date.now();
  try {
    const response = await fetch("/api/extract", { method: "POST", body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      showError(payload.detail || "Extraction failed. Please try again.");
      return;
    }
    lastResults = payload.results;
    lastCatalog = payload.catalog || [];
    lastTotalSeconds = payload.total_seconds;
    lastClientSeconds = (Date.now() - startedAt) / 1000;
    renderResults(payload.results);
  } catch {
    showError("Could not reach the server. Please check your connection and try again.");
  } finally {
    setLoading(false);
  }
});

// ---------------------------------------------------------- Excel export

el("download-button").addEventListener("click", async () => {
  if (!lastResults) return;
  const button = el("download-button");
  button.disabled = true;
  try {
    // Send the still-selected images along so the (stateless) server can
    // embed linked thumbnails in the workbook.
    const body = new FormData();
    body.append("payload", JSON.stringify({
      results: lastResults,
      catalog: lastCatalog,
      total_seconds: lastTotalSeconds,
      client_seconds: lastClientSeconds,
    }));
    for (const file of selectedLabels) body.append("labels", file);
    const response = await fetch("/api/export", { method: "POST", body });
    if (!response.ok) {
      showError("The Excel file could not be created. Please try again.");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "label-extraction-results.xlsx";
    link.click();
    URL.revokeObjectURL(url);
  } catch {
    showError("Could not reach the server. Please try again.");
  } finally {
    button.disabled = false;
  }
});

// -------------------------------------------------------------- results

function renderResults(results) {
  const counts = { pass: 0, fail: 0, warning: 0, error: 0 };
  results.forEach((r) => counts[r.verdict]++);
  let total = "";
  if (lastClientSeconds != null && lastTotalSeconds != null) {
    total = ` in ${lastClientSeconds.toFixed(1)}s (${lastTotalSeconds.toFixed(1)}s processing, the rest upload/transfer)`;
  } else if (lastTotalSeconds != null) {
    total = ` in ${lastTotalSeconds.toFixed(1)}s`;
  }
  el("results-counts").innerHTML = `
    ${results.length} label${results.length === 1 ? "" : "s"} checked${total} —
    <strong class="pass">${counts.pass} passed</strong>,
    <strong class="fail">${counts.fail} failed</strong>,
    <strong class="warn">${counts.warning + counts.error} need review</strong>`;

  const section = el("results-section");
  section.classList.remove("hidden");
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}
