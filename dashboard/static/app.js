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

// The print stylesheet hides everything except the diagram, so "Save as PDF"
// in the print dialog yields a one-page diagram.
document.getElementById("download-diagram-btn").addEventListener("click", () => {
  // Wide diagrams (many access switches/APs) get a landscape page so they don't shrink.
  const svg = document.querySelector("#topology-svg-container svg");
  const wide = svg && Number(svg.getAttribute("width")) > Number(svg.getAttribute("height"));
  let pageStyle = document.getElementById("diagram-page-size");
  if (!pageStyle) {
    pageStyle = document.createElement("style");
    pageStyle.id = "diagram-page-size";
    document.head.appendChild(pageStyle);
  }
  pageStyle.textContent = `@page { size: ${wide ? "landscape" : "portrait"}; }`;
  window.print();
});

let originalResult = null; // the last successful /design or /refine result ({plan, validation, configs})
let shownPlan = null; // the plan currently drawn (the original, or a sabotaged copy)
let failedDevices = new Set(); // devices taken down in the simulator
let highlightedPath = []; // node ids of the flow traced on the topology

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let body;
  try {
    body = await res.json();
  } catch {
    body = { detail: res.statusText };
  }
  return { ok: res.ok, status: res.status, body };
}

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

    document.getElementById("changes-banner").hidden = true;
    showDesign(body);
    resultsSection.hidden = false;
    document.getElementById("requirements-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showError(`Request failed: ${err.message}`);
  } finally {
    setLoading(false, "");
  }
});

// A new real design (from /design or /refine): render it and load everything
// computed from it.
function showDesign(result) {
  originalResult = result;
  failedDevices = new Set();
  highlightedPath = [];
  whatChangedBanner.hidden = true;
  renderResult(result);
  loadSabotages(result.plan);
  loadSimulation(result.plan);
  loadBom(result.plan);
  resetAsk(result.plan);
}

function renderResult(result, highlightCheck) {
  shownPlan = result.plan;
  renderRequirements(result.plan.spec);
  renderValidation(result.validation, highlightCheck);
  drawTopology();
  renderVlans(result.plan.vlans);
  renderConfigs(result.configs || []);
}

function drawTopology() {
  // Simulator state only applies to the original design, not a sabotaged copy.
  const live = shownPlan === originalResult?.plan;
  renderTopology(shownPlan.nodes, shownPlan.links, live ? { failed: failedDevices, path: highlightedPath } : {});
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
    const groups = [
      ["design", "Break the design"],
      ["config", "Break the configs (the design stays valid)"],
    ];
    for (const [kind, title] of groups) {
      const items = sabotages.filter((s) => (s.kind || "design") === kind);
      if (items.length === 0) continue;
      const group = document.createElement("div");
      group.className = "demo-group";
      group.appendChild(document.createElement("h3")).textContent = title;
      const row = group.appendChild(document.createElement("div"));
      row.className = "demo-row";
      for (const s of items) {
        const btn = document.createElement("button");
        btn.textContent = s.label;
        btn.title = `Should be caught by: ${s.target_check}`;
        btn.addEventListener("click", () => breakPlan(s.key));
        row.appendChild(btn);
      }
      demoButtons.appendChild(group);
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
  document.getElementById("download-all-btn").hidden = configs.length === 0;
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

function renderTopology(nodes, links, { failed = new Set(), path = [] } = {}) {
  const container = document.getElementById("topology-svg-container");
  const pairKey = (a, b) => [a, b].sort().join("|");
  const tracedLinks = new Set(path.slice(1).map((id, i) => pairKey(path[i], id)));
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
    const down = failed.has(link.source_id) || failed.has(link.target_id);
    const traced = tracedLinks.has(pairKey(link.source_id, link.target_id));
    line.setAttribute("class", `link-line ${link.link_type}${down ? " down" : ""}${traced ? " on-path" : ""}`);
    svg.appendChild(line);
  }

  for (const { cx, top, node, lines } of positions.values()) {
    const g = document.createElementNS(SVG_NS, "g");
    const isDown = failed.has(node.node_id);
    g.setAttribute("class", `node${isDown ? " failed" : ""}${path.includes(node.node_id) ? " on-path" : ""}`);

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

    if (isDown) {
      const r = ICON * 0.32;
      const cy = top + ICON / 2;
      const x = document.createElementNS(SVG_NS, "path");
      x.setAttribute("d", `M${cx - r} ${cy - r} L${cx + r} ${cy + r} M${cx + r} ${cy - r} L${cx - r} ${cy + r}`);
      x.setAttribute("class", "failed-x");
      g.appendChild(x);
    }

    svg.appendChild(g);
  }

  container.appendChild(svg);
}

// --- What we understood + refine --------------------------------------------

const REDUNDANCY_LABELS = {
  none: "None",
  dual_wan: "Dual WAN",
  dual_wan_plus_switch_redundancy: "Dual WAN + redundant core switches",
};

function renderRequirements(spec) {
  const guest = spec.needs_guest_wifi ? (spec.guest_wifi_isolated ? "Yes, isolated" : "Yes, not isolated") : "No";
  const rows = [
    ["Organization", spec.org_name],
    ["Users", String(spec.user_count)],
    ["Departments", spec.department_segments.length ? spec.department_segments.join(", ") : "None beyond staff"],
    ["Guest wifi", guest],
    ["Redundancy", REDUNDANCY_LABELS[spec.redundancy] || spec.redundancy],
    ["Address space", spec.preferred_base_cidr || "Default (10.0.0.0/16)"],
  ];
  const dl = document.getElementById("spec-summary");
  dl.innerHTML = "";
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.appendChild(document.createElement("dt")).textContent = label;
    row.appendChild(document.createElement("dd")).textContent = value;
    dl.appendChild(row);
  }

  const assumptions = spec.assumptions || [];
  document.getElementById("assumptions-block").hidden = assumptions.length === 0;
  const list = document.getElementById("assumptions-list");
  list.innerHTML = "";
  for (const a of assumptions) list.appendChild(document.createElement("li")).textContent = a;

  document.getElementById("notes-block").hidden = !spec.raw_notes;
  document.getElementById("notes-text").textContent = spec.raw_notes || "";
}

function setInlineStatus(el, message, isError = false) {
  el.hidden = !message;
  el.className = `status${isError ? " error" : ""}`;
  el.textContent = message || "";
}

document.getElementById("refine-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("refine-input");
  const change = input.value.trim();
  const status = document.getElementById("refine-status");
  const btn = document.getElementById("refine-btn");
  if (!change || !originalResult) return;

  btn.disabled = true;
  setInlineStatus(status, "Updating the design…");
  try {
    const { ok, status: code, body } = await postJSON("/refine", { plan: originalResult.plan, change });
    if (!ok) {
      setInlineStatus(status, `(${code}) ${formatDetail(body.detail)}`, true);
      return;
    }
    showDesign(body.result);
    renderChanges(body.changes, change);
    input.value = "";
    setInlineStatus(status, "");
  } catch (err) {
    setInlineStatus(status, `Request failed: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
});

function renderChanges(changes, request) {
  const banner = document.getElementById("changes-banner");
  banner.querySelector("h3").textContent = `What changed: “${request}”`;
  const list = document.getElementById("changes-list");
  list.innerHTML = "";
  for (const c of changes) list.appendChild(document.createElement("li")).textContent = c;
  banner.hidden = false;
}

// --- Simulate ------------------------------------------------------------------

const SIM_DEVICE_ORDER = ["wan_uplink", "router", "firewall", "core_switch", "access_switch"];

async function loadSimulation(plan) {
  renderFailureButtons(plan.nodes);
  const badge = document.getElementById("resilience-badge");
  const summary = document.getElementById("resilience-summary");
  badge.textContent = "";
  badge.className = "badge";
  summary.textContent = "Trying every single-device failure…";

  runSimulation();
  const res = await postJSON("/resilience", { plan }).catch(() => null);
  if (plan !== originalResult.plan) return; // a newer design replaced this one
  if (!res || !res.ok) {
    summary.textContent = `Couldn't run the failure analysis${res ? `: ${formatDetail(res.body.detail)}` : "."}`;
    return;
  }
  const spofs = res.body.single_points_of_failure;
  badge.textContent = spofs.length
    ? `${spofs.length} SINGLE POINT${spofs.length > 1 ? "S" : ""} OF FAILURE`
    : "NO SINGLE POINT OF FAILURE";
  badge.className = `badge ${spofs.length ? "warn" : "pass"}`;
  summary.textContent = res.body.summary;
  for (const btn of document.querySelectorAll("#failure-buttons [data-node]")) {
    const spof = spofs.includes(btn.dataset.node);
    btn.classList.toggle("spof", spof);
    btn.title = spof ? "Single point of failure: losing it cuts VLANs off the internet" : "";
  }
}

function renderFailureButtons(nodes) {
  const row = document.getElementById("failure-buttons");
  row.innerHTML = "";
  const devices = nodes
    .filter((n) => SIM_DEVICE_ORDER.includes(n.node_type))
    .sort((a, b) => SIM_DEVICE_ORDER.indexOf(a.node_type) - SIM_DEVICE_ORDER.indexOf(b.node_type));
  for (const n of devices) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.dataset.node = n.node_id;
    btn.textContent = n.label;
    btn.setAttribute("aria-pressed", "false");
    btn.addEventListener("click", () => {
      if (failedDevices.has(n.node_id)) failedDevices.delete(n.node_id);
      else failedDevices.add(n.node_id);
      const down = failedDevices.has(n.node_id);
      btn.classList.toggle("down", down);
      btn.setAttribute("aria-pressed", String(down));
      runSimulation();
    });
    row.appendChild(btn);
  }
  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "chip reset";
  reset.textContent = "Restore all";
  reset.addEventListener("click", () => {
    failedDevices = new Set();
    for (const b of row.querySelectorAll(".chip.down")) {
      b.classList.remove("down");
      b.setAttribute("aria-pressed", "false");
    }
    runSimulation();
  });
  row.appendChild(reset);
}

let simulationRun = 0; // ignore responses from clicks that have been superseded

async function runSimulation() {
  const run = ++simulationRun;
  const plan = originalResult.plan;
  const summary = document.getElementById("simulation-summary");
  highlightedPath = [];
  document.getElementById("flow-detail").hidden = true;
  drawTopology();
  summary.className = "sim-summary";
  summary.textContent = "Simulating…";
  try {
    const { ok, body } = await postJSON("/simulate", { plan, failed: [...failedDevices] });
    if (run !== simulationRun) return;
    if (!ok) {
      summary.textContent = `Simulation failed: ${formatDetail(body.detail)}`;
      return;
    }
    const cut = body.flows.some((f) => f.destination === "internet" && !f.allowed);
    summary.className = `sim-summary ${cut ? "bad" : "good"}`;
    summary.textContent = body.summary;
    renderMatrix(body.flows);
  } catch (err) {
    if (run === simulationRun) summary.textContent = `Simulation failed: ${err.message}`;
  }
}

function renderMatrix(flows) {
  const table = document.getElementById("reachability-matrix");
  table.innerHTML = "";
  const sources = [...new Set(flows.map((f) => f.source))];
  const dests = [...sources, "internet"];
  const byKey = new Map(flows.map((f) => [`${f.source}>${f.destination}`, f]));

  const headRow = table.createTHead().insertRow();
  headRow.appendChild(document.createElement("th")).textContent = "from ↓ / to →";
  for (const d of dests) headRow.appendChild(document.createElement("th")).textContent = d;

  const tbody = table.createTBody();
  for (const src of sources) {
    const tr = tbody.insertRow();
    tr.appendChild(document.createElement("th")).textContent = src;
    for (const dst of dests) {
      const td = tr.insertCell();
      const flow = byKey.get(`${src}>${dst}`);
      if (!flow) {
        td.className = "self";
        td.textContent = "—";
        continue;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `cell ${flow.allowed ? "ok" : "blocked"}`;
      btn.textContent = flow.allowed ? "✓" : "✗";
      btn.title = `${src} → ${dst}: ${flow.allowed ? "allowed" : "blocked"} (${flow.reason})`;
      btn.setAttribute("aria-label", btn.title);
      btn.addEventListener("click", () => showFlow(flow));
      td.appendChild(btn);
    }
  }
}

function showFlow(flow) {
  const detail = document.getElementById("flow-detail");
  detail.hidden = false;
  detail.className = `flow-detail ${flow.allowed ? "ok" : "blocked"}`;
  detail.innerHTML = "";
  detail.appendChild(document.createElement("strong")).textContent =
    `${flow.source} → ${flow.destination}: ${flow.allowed ? "allowed" : "blocked"}`;
  const path = detail.appendChild(document.createElement("span"));
  path.className = "mono";
  path.textContent = flow.path.length ? flow.path.join(" → ") : "(never leaves the VLAN)";
  detail.appendChild(document.createElement("span")).textContent = flow.reason;
  const hint = detail.appendChild(document.createElement("a"));
  hint.href = "#topology-section";
  hint.textContent = "See the path on the topology ↑";

  if (shownPlan !== originalResult.plan) restoreOriginal();
  highlightedPath = flow.path;
  drawTopology();
}

// --- Bill of materials -------------------------------------------------------

const money = (n) => `$${n.toLocaleString("en-US")}`;

async function loadBom(plan) {
  const section = document.getElementById("bom-section");
  const tbody = document.querySelector("#bom-table tbody");
  const tfoot = document.querySelector("#bom-table tfoot");
  const res = await postJSON("/bom", { plan }).catch(() => null);
  if (plan !== originalResult.plan) return;
  section.hidden = !res || !res.ok;
  if (section.hidden) return;

  const bom = res.body;
  tbody.innerHTML = "";
  for (const line of bom.lines) {
    const per = line.recurring ? "/mo" : "";
    const tr = tbody.insertRow();
    tr.innerHTML = `
      <td><div class="item">${escapeHtml(line.item)}</div><div class="item-sub">${escapeHtml(line.description)}</div></td>
      <td class="mono">${line.quantity}</td>
      <td class="mono num">${money(line.unit_cost)}${per}</td>
      <td class="mono num">${money(line.subtotal)}${per}</td>`;
  }
  tfoot.innerHTML = `
    <tr><th colspan="3">One-time total</th><td class="mono num">${money(bom.one_time_total)}</td></tr>
    <tr><th colspan="3">Monthly recurring</th><td class="mono num">${money(bom.monthly_total)}/mo</td></tr>`;
  const notes = document.getElementById("bom-notes");
  notes.innerHTML = "";
  for (const n of bom.notes) notes.appendChild(document.createElement("li")).textContent = n;
}

// --- Download all configs (.zip) ---------------------------------------------

document.getElementById("download-all-btn").addEventListener("click", () => {
  if (!originalResult) return;
  const slug = originalResult.plan.spec.org_name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "network";
  const files = originalResult.configs.map((c) => ({ name: `${slug}/configs/${c.node_id}.cfg`, text: c.config_text }));
  files.push({ name: `${slug}/design.json`, text: JSON.stringify(originalResult, null, 2) });
  downloadBlob(makeZip(files), `${slug}-configs.zip`);
});

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) crc = CRC_TABLE[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

// Minimal uncompressed ("stored") ZIP writer -- plenty for a few text files,
// and no third-party library to load.
function makeZip(files) {
  const enc = new TextEncoder();
  const DOS_DATE = (1 << 5) | 1; // 1980-01-01
  const UTF8_NAMES = 0x0800;
  const parts = [];
  const central = [];
  let offset = 0;

  for (const f of files) {
    const name = enc.encode(f.name);
    const data = enc.encode(f.text);
    const crc = crc32(data);

    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, UTF8_NAMES, true);
    local.setUint16(12, DOS_DATE, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, name.length, true);
    parts.push(local, name, data);

    const entry = new DataView(new ArrayBuffer(46));
    entry.setUint32(0, 0x02014b50, true);
    entry.setUint16(4, 20, true);
    entry.setUint16(6, 20, true);
    entry.setUint16(8, UTF8_NAMES, true);
    entry.setUint16(14, DOS_DATE, true);
    entry.setUint32(16, crc, true);
    entry.setUint32(20, data.length, true);
    entry.setUint32(24, data.length, true);
    entry.setUint16(28, name.length, true);
    entry.setUint32(42, offset, true);
    central.push(entry, name);

    offset += 30 + name.length + data.length;
  }

  const centralSize = central.reduce((n, p) => n + p.byteLength, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, files.length, true);
  end.setUint16(10, files.length, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end], { type: "application/zip" });
}

// --- Ask about this design ----------------------------------------------------

function resetAsk(plan) {
  setInlineStatus(document.getElementById("ask-status"), "");
  document.getElementById("ask-answer").hidden = true;
  const suggestions = [
    "Why is each subnet the size it is?",
    plan.spec.guest_wifi_isolated && "How is guest traffic kept away from internal systems?",
    plan.spec.redundancy !== "none" && "What happens if the primary internet connection fails?",
    "Which devices should I configure first?",
  ].filter(Boolean);
  const row = document.getElementById("ask-suggestions");
  row.innerHTML = "";
  for (const q of suggestions) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = q;
    btn.addEventListener("click", () => {
      document.getElementById("ask-input").value = q;
      askQuestion(q);
    });
    row.appendChild(btn);
  }
}

document.getElementById("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = document.getElementById("ask-input").value.trim();
  if (q && originalResult) askQuestion(q);
});

async function askQuestion(question) {
  const status = document.getElementById("ask-status");
  const answer = document.getElementById("ask-answer");
  const btn = document.getElementById("ask-btn");
  btn.disabled = true;
  answer.hidden = true;
  setInlineStatus(status, "Reading your design…");
  try {
    const { ok, status: code, body } = await postJSON("/explain", { result: originalResult, question });
    if (!ok) {
      setInlineStatus(status, `(${code}) ${formatDetail(body.detail)}`, true);
      return;
    }
    setInlineStatus(status, "");
    answer.innerHTML = "";
    const q = answer.appendChild(document.createElement("p"));
    q.className = "answer-q";
    q.textContent = question;
    for (const para of body.answer.split(/\n{2,}/)) {
      answer.appendChild(document.createElement("p")).textContent = para;
    }
    answer.hidden = false;
  } catch (err) {
    setInlineStatus(status, `Request failed: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}
