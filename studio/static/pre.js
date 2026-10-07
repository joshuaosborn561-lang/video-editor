const state = {
  id: null,
  project: null,
  picks: { offer_id: null, hook_id: null, title_id: null },
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
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
    };
    root.appendChild(button);
  }
}

$("save-picks").onclick = async () => {
  showWarn("");
  try {
    state.project = await api(`/api/projects/${state.id}/picks`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(state.picks),
    });
    $("script-section").hidden = false;
  } catch (error) {
    showWarn(error.message);
  }
};

$("write-script").onclick = async () => {
  try {
    state.project = await api(`/api/projects/${state.id}/script`, { method: "POST" });
    $("script").value = state.project.script;
    $("script-state").textContent = `Draft from ${state.project.script_source}. Edit it, then approve.`;
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
  $("script-state").textContent = "Edits saved.";
};

$("approve-script").onclick = async () => {
  if ($("script").value !== state.project.script) await $("save-script").onclick();
  state.project = await api(`/api/projects/${state.id}/script/approve`, { method: "POST" });
  $("script-state").textContent = "Script approved. Open the editor when the recording is ready.";
};

api("/api/vendors").then((vendors) => {
  const mode = vendors.suggestion_mode === "template"
    ? "Hooks and scripts are using the local template."
    : `Hooks and scripts are using ${vendors.suggestion_mode}.`;
  $("vendor-line").textContent = mode;
});
