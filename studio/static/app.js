const state = {
  id: null,
  project: null,
  picks: { offer_id: null, hook_id: null, title_id: null },
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function showWarn(message) {
  $("warn").hidden = !message;
  $("warn").textContent = message || "";
}

$("sample").onclick = () => {
  const form = $("brief-form");
  const values = {
    founder_name: "Alex",
    company: "Northwind",
    icp: "owners of B2B service companies over $5k a month",
    pain: "referrals dried up and cold email replies fell off",
    topic: "how to get sales calls from YouTube with a small channel",
    current_offer: "done-for-you YouTube for B2B companies",
    proof: "a client booked 15 sales calls in 30 days from a channel under 800 subscribers",
    cta: "book a call",
  };
  for (const [key, value] of Object.entries(values)) form.elements[key].value = value;
};

$("brief-form").onsubmit = async (event) => {
  event.preventDefault();
  showWarn("");
  const brief = Object.fromEntries(new FormData(event.target).entries());
  try {
    const created = await api("/api/projects", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(brief),
    });
    state.id = created.id;
    state.project = await api(`/api/projects/${state.id}/suggest`, { method: "POST" });
    state.picks = { offer_id: null, hook_id: null, title_id: null };
    drawPicks();
    showWarn(state.project.suggestion_warning || "");
  } catch (error) {
    showWarn(error.message);
  }
};

function drawPicks() {
  const suggestions = state.project.suggestions;
  $("pick-section").hidden = false;
  fillCards("offers", suggestions.offers, "offer_id", (item) => `<strong>${item.name}</strong>${item.pitch}`);
  fillCards("hooks", suggestions.hooks, "hook_id", (item) => `<strong>${item.label} · ${item.seconds}</strong>${item.text}`);
  fillCards("titles", suggestions.titles, "title_id", (item) => `<strong>${item.text}</strong>${item.thumb_lines.join(" / ")}`);
  $("save-picks").disabled = true;
}

function fillCards(elementId, items, key, html) {
  const root = $(elementId);
  root.innerHTML = "";
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "card";
    button.innerHTML = html(item);
    button.onclick = () => {
      state.picks[key] = item.id;
      for (const child of root.children) child.classList.remove("on");
      button.classList.add("on");
      $("save-picks").disabled = !(state.picks.offer_id && state.picks.hook_id && state.picks.title_id);
      if (key === "title_id") fillThumbFields(item);
    };
    root.appendChild(button);
  }
}

function fillThumbFields(title) {
  const root = $("thumb-lines");
  root.innerHTML = "";
  title.thumb_lines.forEach((line, index) => {
    const label = document.createElement("label");
    label.textContent = `Line ${index + 1}`;
    const input = document.createElement("input");
    input.value = line;
    input.dataset.line = "1";
    label.appendChild(input);
    root.appendChild(label);
  });
  $("highlight").value = title.highlight || title.thumb_lines[0];
}

$("save-picks").onclick = async () => {
  showWarn("");
  const lines = [...$("thumb-lines").querySelectorAll("input")].map((input) => input.value);
  try {
    state.project = await api(`/api/projects/${state.id}/picks`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...state.picks,
        thumb_lines: lines.length ? lines : undefined,
        highlight: $("highlight").value,
        accent: $("accent").value,
        desaturate: $("desaturate").checked,
      }),
    });
    $("thumb-section").hidden = false;
    $("script-section").hidden = false;
  } catch (error) {
    showWarn(error.message);
  }
};

$("thumb-form").onsubmit = async (event) => {
  event.preventDefault();
  await saveThumbSettings();
  const body = new FormData();
  const face = $("face").files[0];
  if (face) body.append("face", face);
  try {
    state.project = await api(`/api/projects/${state.id}/thumbnail`, { method: "POST", body });
    const image = $("thumb-preview");
    image.hidden = false;
    image.src = `/api/projects/${state.id}/thumbnail.jpg?t=${Date.now()}`;
  } catch (error) {
    showWarn(error.message);
  }
};

async function saveThumbSettings() {
  const lines = [...$("thumb-lines").querySelectorAll("input")].map((input) => input.value);
  state.project = await api(`/api/projects/${state.id}/picks`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...state.picks,
      thumb_lines: lines,
      highlight: $("highlight").value,
      accent: $("accent").value,
      desaturate: $("desaturate").checked,
    }),
  });
}

$("write-script").onclick = async () => {
  try {
    state.project = await api(`/api/projects/${state.id}/script`, { method: "POST" });
    $("script").value = state.project.script;
    $("script-state").textContent = `Draft from ${state.project.script_source}. Edit it, then approve.`;
    $("plan-section").hidden = false;
    showWarn(state.project.suggestion_warning || "");
  } catch (error) {
    showWarn(error.message);
  }
};

$("save-script").onclick = async () => {
  state.project = await api(`/api/projects/${state.id}/script`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: $("script").value }),
  });
  $("script-state").textContent = "Edits saved. Approve when it is the video you want to record.";
};

$("approve-script").onclick = async () => {
  if ($("script").value !== state.project.script) await $("save-script").onclick();
  state.project = await api(`/api/projects/${state.id}/script/approve`, { method: "POST" });
  $("script-state").textContent = "Script approved. You can record from this, then price the edit.";
  $("plan-section").hidden = false;
};

$("footage-form").onsubmit = async (event) => {
  event.preventDefault();
  const body = new FormData();
  for (const [key, value] of new FormData(event.target).entries()) {
    if (value && value.name) body.append(key, value);
  }
  state.project = await api(`/api/projects/${state.id}/footage`, { method: "POST", body });
  $("sync-line").textContent = syncText(state.project.footage.sync);
};

function syncText(sync) {
  if (!sync || !Object.keys(sync).length) return "No camera and mic pair attached yet. The price will assume 40 minutes of raw footage.";
  return Object.entries(sync).map(([name, result]) => {
    if (result.ok) return `${name}: clap lock, shift ${result.offset_seconds}s (confidence ${result.confidence}).`;
    return `${name}: ${result.reason}`;
  }).join(" ");
}

$("build-plan").onclick = async () => {
  try {
    state.project = await api(`/api/projects/${state.id}/plan`, { method: "POST" });
    drawPlan(state.project.plan);
    $("approve-edit").hidden = false;
  } catch (error) {
    showWarn(error.message);
  }
};

function drawPlan(plan) {
  const checks = plan.checklist.map((item) => `<li class="${item.ok ? "ok" : "bad"}">${item.ok ? "Ready" : "Fix"} — ${item.label}</li>`).join("");
  const money = plan.quote.lines.map((line) => `<li>${line.item}: $${line.amount.toFixed(2)}</li>`).join("");
  const issues = (plan.cadence_issues || []).map((issue) => `<li class="bad">${issue}</li>`).join("");
  $("plan").innerHTML = `
    <p class="quote">$${plan.quote.total.toFixed(2)}</p>
    <p class="meta">${plan.quote.note}</p>
    <ul>${money}</ul>
    <h3>Checklist</h3>
    <ul>${checks}</ul>
    ${issues ? `<h3>Picture changes</h3><ul>${issues}</ul>` : ""}
  `;
}

$("approve-edit").onclick = async () => {
  state.project = await api(`/api/projects/${state.id}/approve`, { method: "POST" });
  const preview = state.project.preview
    ? ` Caption preview: /api/projects/${state.id}/preview.mp4`
    : "";
  $("approved-line").textContent = `Approved. Edit plan saved for ${state.id}.${preview}`;
};

api("/api/vendors").then((vendors) => {
  const mode = vendors.suggestion_mode === "template"
    ? "Hooks and scripts are using the local template. Add an Anthropic or OpenAI key for model drafts."
    : `Hooks and scripts are using ${vendors.suggestion_mode}.`;
  const captions = vendors.deepgram ? "Deepgram is connected for captions." : "Captions wait on a Deepgram key.";
  const music = vendors.epidemic ? "Epidemic Sound is connected." : "Attach two music files, or add Epidemic Sound later.";
  $("vendor-line").textContent = `${mode} ${captions} ${music}`;
});
