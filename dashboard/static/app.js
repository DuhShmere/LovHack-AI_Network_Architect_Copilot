const LAYER_ORDER = ["wan_uplink", "router", "firewall", "core_switch", "access_switch", "ap"];

const NODE_COLORS = {
  wan_uplink: "#6b7280",
  router: "#2d6a00",
  firewall: "#c23b3b",
  core_switch: "#1f4d00",
  access_switch: "#3f7d24",
  ap: "#b45309",
  other: "#5b6168",
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
    scrollToValidation();
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
    scrollToValidation();
  } catch (err) {
    showError(`Request failed: ${err.message}`);
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

function scrollToValidation() {
  document.getElementById("validation-section").scrollIntoView({ behavior: "smooth", block: "start" });
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

const SVG_NS = "http://www.w3.org/2000/svg";
const ICON = 68; // icons are drawn in a 64x64 box and scaled to this size

function tint(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const mix = (c) => Math.round(c + (255 - c) * amount);
  const r = mix(n >> 16), g = mix((n >> 8) & 255), b = mix(n & 255);
  return `rgb(${r}, ${g}, ${b})`;
}

function shade(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const dim = (c) => Math.round(c * (1 - amount));
  return `rgb(${dim(n >> 16)}, ${dim((n >> 8) & 255)}, ${dim(n & 255)})`;
}

// Our own drawings of the standard network-diagram symbols, in a 64x64 box.
const ICONS = {
  wan_uplink: (c) => `
    <path d="M17 48 a11 11 0 0 1 1.5-21.9 a15 15 0 0 1 28.6 2.6 a9.7 9.7 0 0 1 -0.6 19.3 Z"
          fill="${tint(c, 0.55)}" stroke="${c}" stroke-width="2.5" stroke-linejoin="round" />`,

  router: (c) => `
    <path d="M6 26 v14 a26 9 0 0 0 52 0 v-14" fill="${c}" />
    <ellipse cx="32" cy="26" rx="26" ry="9" fill="${tint(c, 0.35)}" />
    <g stroke="#fff" stroke-width="2.4" stroke-linecap="round" fill="none">
      <path d="M23 21 l7 4 M27 20.5 l-4 0.5 l1.5 3.5" />
      <path d="M41 31 l-7 -4 M37 31.5 l4 -0.5 l-1.5 -3.5" />
      <path d="M41 21 l-7 4 M37 20.5 l4 0.5 l-1.5 3.5" />
      <path d="M23 31 l7 -4 M27 31.5 l-4 -0.5 l1.5 -3.5" />
    </g>`,

  firewall: (c) => `
    <rect x="8" y="14" width="48" height="38" rx="2" fill="${c}" />
    <g stroke="#fff" stroke-width="2">
      <path d="M8 23.5 h48 M8 33 h48 M8 42.5 h48" />
      <path d="M24 14 v9.5 M40 14 v9.5 M16 23.5 v9.5 M32 23.5 v9.5 M48 23.5 v9.5
               M24 33 v9.5 M40 33 v9.5 M16 42.5 v9.5 M32 42.5 v9.5 M48 42.5 v9.5" />
    </g>`,

  core_switch: (c) => switchIcon(c),
  access_switch: (c) => switchIcon(c),

  ap: (c) => `
    <g fill="none" stroke="${c}" stroke-width="2.6" stroke-linecap="round">
      <path d="M24 22 a11 11 0 0 1 16 0" />
      <path d="M19 16.5 a18 18 0 0 1 26 0" />
      <path d="M22 38 l-4 -14 M42 38 l4 -14" />
    </g>
    <circle cx="32" cy="27" r="2.6" fill="${c}" />
    <rect x="12" y="37" width="40" height="13" rx="4" fill="${c}" />
    <circle cx="20" cy="43.5" r="1.8" fill="#fff" />
    <circle cx="26" cy="43.5" r="1.8" fill="#fff" />`,

  other: (c) => `<rect x="10" y="16" width="44" height="32" rx="6" fill="${c}" />`,
};

function switchIcon(c) {
  return `
    <polygon points="6,30 18,20 58,20 46,30" fill="${tint(c, 0.35)}" />
    <rect x="6" y="30" width="40" height="16" fill="${c}" />
    <polygon points="46,30 58,20 58,36 46,46" fill="${shade(c, 0.3)}" />
    <g stroke="#fff" stroke-width="2" stroke-linecap="round" fill="none">
      <path d="M21 23 h20 M37 21 l4 2 l-4 2" />
      <path d="M43 27 h-20 M27 25 l-4 2 l4 2" />
    </g>`;
}

function wrapLabel(label, maxChars = 16) {
  if (label.length <= maxChars) return [label];
  const cut = label.lastIndexOf(" ", maxChars);
  const at = cut > 0 ? cut : label.indexOf(" ");
  return at > 0 ? [label.slice(0, at), label.slice(at + 1)] : [label];
}

function renderTopology(nodes, links) {
  const container = document.getElementById("topology-svg-container");
  container.innerHTML = "";
  if (nodes.length === 0) return;

  const slotW = 150; // width reserved per node, so labels have room
  const colSpacing = 170;
  const layerHeight = 155;
  const marginX = 20;
  const marginY = 16;
  const lineH = 15;

  const layers = new Map();
  for (const node of nodes) {
    const l = layerOf(node.node_type);
    if (!layers.has(l)) layers.set(l, []);
    layers.get(l).push(node);
  }
  const sortedLayerKeys = [...layers.keys()].sort((a, b) => a - b);
  const maxCols = Math.max(...[...layers.values()].map((row) => row.length));
  const totalWidth = marginX * 2 + (maxCols - 1) * colSpacing + slotW;

  const neighbors = new Map(nodes.map((n) => [n.node_id, []]));
  for (const l of links) {
    neighbors.get(l.source_id)?.push(l.target_id);
    neighbors.get(l.target_id)?.push(l.source_id);
  }

  // positions: node_id -> {cx, top, iconBottom, labelBottom, node, lines}
  const positions = new Map();
  sortedLayerKeys.forEach((layerKey, rowIdx) => {
    // Order each row by where its already-placed neighbors sit, so a device
    // lands under the thing it plugs into instead of links crisscrossing.
    const rowNodes = layers
      .get(layerKey)
      .map((node, i) => {
        const placed = neighbors.get(node.node_id).map((id) => positions.get(id)).filter(Boolean);
        const key = placed.length ? placed.reduce((s, p) => s + p.cx, 0) / placed.length : i;
        return { node, key, i };
      })
      .sort((a, b) => a.key - b.key || a.i - b.i)
      .map((entry) => entry.node);
    const rowWidth = (rowNodes.length - 1) * colSpacing + slotW;
    const rowStart = (totalWidth - rowWidth) / 2;
    rowNodes.forEach((node, colIdx) => {
      const top = marginY + rowIdx * layerHeight;
      const lines = wrapLabel(node.label);
      positions.set(node.node_id, {
        cx: rowStart + colIdx * colSpacing + slotW / 2,
        top,
        iconBottom: top + ICON,
        labelBottom: top + ICON + 8 + lines.length * lineH,
        node,
        lines,
      });
    });
  });

  const lastRowBottom = Math.max(...[...positions.values()].map((p) => p.labelBottom));
  const totalHeight = lastRowBottom + marginY;

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", totalWidth);
  svg.setAttribute("height", totalHeight);
  svg.setAttribute("viewBox", `0 0 ${totalWidth} ${totalHeight}`);

  // Links first, so icons draw on top. Between layers, run from the bottom
  // of the upper node's label to the top of the lower icon, so lines never
  // cross the text; within a layer, join the icons side to side.
  for (const link of links) {
    const a = positions.get(link.source_id);
    const b = positions.get(link.target_id);
    if (!a || !b) continue;
    let x1, y1, x2, y2;
    if (a.top === b.top) {
      const [left, right] = a.cx < b.cx ? [a, b] : [b, a];
      x1 = left.cx + ICON / 2; x2 = right.cx - ICON / 2;
      y1 = y2 = left.top + ICON / 2;
    } else {
      const [upper, lower] = a.top < b.top ? [a, b] : [b, a];
      x1 = upper.cx; y1 = upper.labelBottom + 4;
      x2 = lower.cx; y2 = lower.top;
    }
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", x1);
    line.setAttribute("y1", y1);
    line.setAttribute("x2", x2);
    line.setAttribute("y2", y2);
    line.setAttribute("class", `link-line ${link.link_type}`);
    svg.appendChild(line);
  }

  for (const { cx, top, node, lines } of positions.values()) {
    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("class", "node");

    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${node.node_id} (${node.node_type})`;
    g.appendChild(title);

    const color = NODE_COLORS[node.node_type] || NODE_COLORS.other;
    const draw = ICONS[node.node_type] || ICONS.other;
    const icon = document.createElementNS(SVG_NS, "g");
    icon.setAttribute("transform", `translate(${cx - ICON / 2} ${top}) scale(${ICON / 64})`);
    icon.innerHTML = draw(color);
    g.appendChild(icon);

    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("class", "node-label");
    text.setAttribute("text-anchor", "middle");
    lines.forEach((line, i) => {
      const tspan = document.createElementNS(SVG_NS, "tspan");
      tspan.setAttribute("x", cx);
      tspan.setAttribute("y", top + ICON + 8 + lineH * (i + 1) - 3);
      tspan.textContent = line;
      text.appendChild(tspan);
    });
    g.appendChild(text);

    svg.appendChild(g);
  }

  container.appendChild(svg);
}
