const state = { id: null, project: null };

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

$("edit-form").onsubmit = async (event) => {
  event.preventDefault();
  showWarn("");
  const capUrl = $("cap-url").value.trim();
  const camera = $("camera").files[0];
  if (!capUrl && !camera) {
    showWarn("Paste a Cap link or choose the recording.");
    return;
  }
  $("edit").disabled = true;
  try {
    const created = await api("/api/edits", { method: "POST" });
    state.id = created.id;
    state.project = created;
    if (capUrl) {
      $("status-line").textContent = "Pulling the Cap…";
      state.project = await api(`/api/projects/${state.id}/cap`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: capUrl, slot: $("cap-slot").value }),
      });
    }
    const body = new FormData();
    for (const name of ["camera", "mic", "screen", "music_intro", "music_body", "sfx_hit", "sfx_click", "sfx_riser"]) {
      const file = $(name).files[0];
      if (file) body.append(name, file);
    }
    if ([...body.keys()].length) {
      $("status-line").textContent = "Attaching the files…";
      state.project = await api(`/api/projects/${state.id}/footage`, { method: "POST", body });
    }
    if (!state.project.footage || !state.project.footage.camera) {
      showWarn("The edit needs a main recording. Pull the Cap as the main recording, or choose a file.");
      return;
    }
    $("status-line").textContent = "Cutting…";
    state.project = await api(`/api/projects/${state.id}/plan`, { method: "POST" });
    drawPlan(state.project.plan);
    state.project = await api(`/api/projects/${state.id}/approve`, { method: "POST" });
    const player = $("cut-player");
    player.hidden = false;
    player.src = `/api/projects/${state.id}/cut.mp4?t=${Date.now()}`;
    const kept = state.project.render ? state.project.render.kept_seconds : "";
    const captions = state.project.render ? state.project.render.captions : "the script";
    $("approved-line").textContent = `Cut is ${kept}s. Captions follow the ${captions}.`;
    if (state.project.render && state.project.render.warning) showWarn(state.project.render.warning);
    $("status-line").textContent = "";
  } catch (error) {
    showWarn(error.message);
    $("status-line").textContent = "";
  } finally {
    $("edit").disabled = false;
  }
};

function drawPlan(plan) {
  const money = plan.quote.lines.map((line) => `<li>${line.item}: $${line.amount.toFixed(2)}</li>`).join("");
  $("plan").innerHTML = `
    <p class="quote">$${plan.quote.total.toFixed(2)}</p>
    <p class="meta">${plan.quote.note}</p>
    <ul>${money}</ul>
  `;
}

api("/api/vendors").then((vendors) => {
  const storage = vendors.storage === "supabase"
    ? "The cut is stored in Supabase."
    : "The cut stays on this server's disk until Supabase is connected.";
  const captions = vendors.deepgram
    ? "Deepgram times the captions to the spoken words."
    : "Captions stay off the spoken words until a Deepgram key is set.";
  $("vendor-line").textContent = `${storage} ${captions}`;
});
