const LAYER_ORDER = ["wan_uplink", "router", "firewall", "core_switch", "access_switch", "ap"];

const NODE_COLORS = {
  wan_uplink: "#5a6478",
  router: "#5b8cff",
  firewall: "#ff5d6c",
  core_switch: "#b57bff",
  access_switch: "#3ecf8e",
  ap: "#37c6d0",
  other: "#8892a8",
};

const form = document.getElementById("design-form");
const descriptionInput = document.getElementById("description");
const submitBtn = document.getElementById("submit-btn");
const statusEl = document.getElementById("status");
const errorBanner = document.getElementById("error-banner");
const resultsSection = document.getElementById("results");
const demoPanel = document.getElementById("demo-panel");
const demoButtons = document.getElementById("demo-buttons");
const whatChangedBanner = document.getElementById("what-changed-banner");

let originalResult = null; // the last successful /design response ({plan, validation, configs})

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function setLoading(isLoading, message) {
  submitBtn.disabled = isLoading;
  statusEl.hidden = !message;
  statusEl.textContent = message || "";
}

function showError(message) {
  errorBanner.hidden = false;
  errorBanner.textContent = message;
  resultsSection.hidden = true;
}

function hideError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic request-validation error shape.
    return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  return JSON.stringify(detail);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const description = descriptionInput.value.trim();
  if (!description) {
    showError("Please describe your network requirements first.");
    return;
  }

  hideError();
  setLoading(true, "Designing network…");

  try {
    const res = await fetch("/design", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description }),
    });
    const body = await res.json();

    if (!res.ok) {
      showError(`(${res.status}) ${formatDetail(body.detail)}`);
      return;
    }

    originalResult = body;
    renderResult(body);
    resultsSection.hidden = false;
    loadSabotages(body.plan);
  } catch (err) {
    showError(`Request failed: ${err.message}`);
  } finally {
    setLoading(false, "");
  }
});

function renderResult(result, highlightCheck) {
  renderValidation(result.validation, highlightCheck);
  renderTopology(result.plan.nodes, result.plan.links);
  renderVlans(result.plan.vlans);
  renderConfigs(result.configs || []);
}

function renderValidation(validation, highlightCheck) {
  const badge = document.getElementById("overall-pass-badge");
  badge.textContent = validation.overall_pass ? "PASS" : "FAIL";
  badge.className = `badge ${validation.overall_pass ? "pass" : "fail"}`;

  const list = document.getElementById("validation-list");
  list.innerHTML = "";
  for (const check of validation.checks) {
    const li = document.createElement("li");
    if (check.check_name === highlightCheck) li.className = "just-broken";
    const icon = document.createElement("span");
    icon.className = `check-icon ${check.passed ? "pass" : "fail"}`;
    icon.textContent = check.passed ? "✓" : "✗";
    const text = document.createElement("span");
    text.innerHTML = `<span class="check-name">${escapeHtml(check.check_name)}</span>${escapeHtml(check.detail)}`;
    li.appendChild(icon);
    li.appendChild(text);
    list.appendChild(li);
  }
}

// --- Break it (demo) -------------------------------------------------------

async function loadSabotages(plan) {
  demoPanel.hidden = true;
  whatChangedBanner.hidden = true;
  try {
    const res = await fetch("/demo/sabotages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    if (!res.ok) return;
    const sabotages = await res.json();
    if (sabotages.length === 0) return;

    demoButtons.innerHTML = "";
    for (const s of sabotages) {
      const btn = document.createElement("button");
      btn.textContent = s.label;
      btn.addEventListener("click", () => breakPlan(s.key));
      demoButtons.appendChild(btn);
    }
    demoPanel.hidden = false;
  } catch {
    // Demo panel is optional; silently skip it if this call fails.
  }
}

async function breakPlan(key) {
  const buttons = demoButtons.querySelectorAll("button");
  buttons.forEach((b) => (b.disabled = true));
  try {
    const res = await fetch("/demo/break", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: originalResult.plan, sabotage: key }),
    });
    const body = await res.json();
    if (!res.ok) {
      showError(`(${res.status}) ${formatDetail(body.detail)}`);
      return;
    }

    renderResult({ plan: body.plan, validation: body.validation, configs: [] }, findTargetCheck(body.validation));
    showWhatChanged(body.what_changed);
  } catch (err) {
    showError(`Request failed: ${err.message}`);
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

function findTargetCheck(validation) {
  const failed = validation.checks.find((c) => !c.passed);
  return failed ? failed.check_name : null;
}

function showWhatChanged(whatChanged) {
  whatChangedBanner.hidden = false;
  whatChangedBanner.innerHTML = "";
  const text = document.createElement("span");
  text.textContent = `\u{1F528} ${whatChanged}`;
  const restoreBtn = document.createElement("button");
  restoreBtn.textContent = "Restore original design";
  restoreBtn.addEventListener("click", restoreOriginal);
  whatChangedBanner.appendChild(text);
  whatChangedBanner.appendChild(restoreBtn);
}

function restoreOriginal() {
  whatChangedBanner.hidden = true;
  renderResult(originalResult);
}

function renderVlans(vlans) {
  const tbody = document.querySelector("#vlan-table tbody");
  tbody.innerHTML = "";
  for (const v of [...vlans].sort((a, b) => a.vlan_id - b.vlan_id)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${v.vlan_id}</td>
      <td>${escapeHtml(v.name)}</td>
      <td class="mono">${escapeHtml(v.subnet_cidr)}</td>
      <td>${escapeHtml(v.purpose)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderConfigs(configs) {
  const list = document.getElementById("configs-list");
  list.innerHTML = "";
  if (configs.length === 0) {
    list.innerHTML = '<p style="color: var(--text-dim); margin: 0;">No configs generated (validation failed, or this device type doesn\'t need one).</p>';
    return;
  }
  for (const cfg of configs) {
    const row = document.createElement("div");
    row.className = "config-row";
    const label = document.createElement("span");
    label.className = "node-id";
    label.textContent = cfg.node_id;
    const btn = document.createElement("button");
    btn.textContent = "Download";
    btn.addEventListener("click", () => downloadConfig(cfg));
    row.appendChild(label);
    row.appendChild(btn);
    list.appendChild(row);
  }
}

function downloadConfig(cfg) {
  const blob = new Blob([cfg.config_text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${cfg.node_id}.cfg`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- Topology ------------------------------------------------------------

function layerOf(nodeType) {
  const idx = LAYER_ORDER.indexOf(nodeType);
  return idx === -1 ? LAYER_ORDER.length : idx;
}

function renderTopology(nodes, links) {
  const container = document.getElementById("topology-svg-container");
  container.innerHTML = "";
  if (nodes.length === 0) return;

  const layerHeight = 110;
  const colSpacing = 160;
  const nodeW = 130;
  const nodeH = 42;
  const marginX = 40;
  const marginY = 30;

  const layers = new Map();
  for (const node of nodes) {
    const l = layerOf(node.node_type);
    if (!layers.has(l)) layers.set(l, []);
    layers.get(l).push(node);
  }
  const sortedLayerKeys = [...layers.keys()].sort((a, b) => a - b);

  const positions = new Map(); // node_id -> {x, y}
  let maxCols = 1;
  sortedLayerKeys.forEach((layerKey, rowIdx) => {
    const rowNodes = layers.get(layerKey);
    maxCols = Math.max(maxCols, rowNodes.length);
    rowNodes.forEach((node, colIdx) => {
      positions.set(node.node_id, {
        x: marginX + colIdx * colSpacing,
        y: marginY + rowIdx * layerHeight,
        node,
      });
    });
  });

  // Center each row within the widest row.
  const totalWidth = marginX * 2 + (maxCols - 1) * colSpacing + nodeW;
  sortedLayerKeys.forEach((layerKey) => {
    const rowNodes = layers.get(layerKey);
    const rowWidth = marginX * 2 + (rowNodes.length - 1) * colSpacing + nodeW;
    const offset = (totalWidth - rowWidth) / 2;
    if (offset > 0) {
      for (const node of rowNodes) {
        positions.get(node.node_id).x += offset;
      }
    }
  });

  const totalHeight = marginY * 2 + (sortedLayerKeys.length - 1) * layerHeight + nodeH;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", totalWidth);
  svg.setAttribute("height", totalHeight);
  svg.setAttribute("viewBox", `0 0 ${totalWidth} ${totalHeight}`);

  // Links first, so nodes draw on top.
  for (const link of links) {
    const from = positions.get(link.source_id);
    const to = positions.get(link.target_id);
    if (!from || !to) continue;
    const x1 = from.x + nodeW / 2;
    const y1 = from.y + nodeH / 2;
    const x2 = to.x + nodeW / 2;
    const y2 = to.y + nodeH / 2;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "line");
    path.setAttribute("x1", x1);
    path.setAttribute("y1", y1);
    path.setAttribute("x2", x2);
    path.setAttribute("y2", y2);
    path.setAttribute("class", `link-line ${link.link_type}`);
    svg.appendChild(path);
  }

  // Nodes.
  for (const { x, y, node } of positions.values()) {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "node");

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x);
    rect.setAttribute("y", y);
    rect.setAttribute("width", nodeW);
    rect.setAttribute("height", nodeH);
    rect.setAttribute("rx", 8);
    rect.setAttribute("fill", NODE_COLORS[node.node_type] || NODE_COLORS.other);
    g.appendChild(rect);

    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", x + nodeW / 2);
    text.setAttribute("y", y + nodeH / 2 + 4);
    text.setAttribute("text-anchor", "middle");
    text.textContent = node.label;
    g.appendChild(text);

    svg.appendChild(g);
  }

  container.appendChild(svg);
}
