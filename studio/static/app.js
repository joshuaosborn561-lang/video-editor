const state = { id: null, project: null, folderUrl: "", videos: [] };

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
  if (isFolder(capUrl)) {
    await loadFolder(capUrl);
    return;
  }
  $("edit").disabled = true;
  try {
    await beginEdit();
    if (capUrl) {
      $("status-line").textContent = "Pulling the Cap…";
      state.project = await api(`/api/projects/${state.id}/cap`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: capUrl, slot: $("cap-slot").value }),
      });
    }
    await finishEdit(false);
  } catch (error) {
    showWarn(error.message);
    $("status-line").textContent = "";
  } finally {
    $("edit").disabled = false;
  }
};

function isFolder(url) {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    const head = parsed.pathname.split("/").filter(Boolean)[0];
    return host === "cap.so" && head === "c";
  } catch {
    return false;
  }
}

async function loadFolder(url) {
  $("edit").disabled = true;
  $("status-line").textContent = "Reading the folder…";
  try {
    const listing = await api("/api/cap-folder", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url }),
    });
    state.folderUrl = listing.url;
    state.videos = listing.videos;
    drawOrder(listing);
    $("status-line").textContent = listing.truncated
      ? "Showing the first 40 videos. Set the order, then edit."
      : "Set the order, then edit.";
  } catch (error) {
    showWarn(error.message);
    $("status-line").textContent = "";
  } finally {
    $("edit").disabled = false;
  }
}

function drawOrder(listing) {
  $("order-section").hidden = false;
  $("order-title").textContent = listing.title ? listing.title : "Videos in this folder";
  const root = $("order-list");
  root.innerHTML = "";
  state.videos.forEach((video, index) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${index + 1}. ${video.title || video.id}`;
    const up = document.createElement("button");
    up.type = "button";
    up.className = "ghost";
    up.textContent = "Up";
    up.disabled = index === 0;
    up.onclick = () => moveVideo(index, -1);
    const down = document.createElement("button");
    down.type = "button";
    down.className = "ghost";
    down.textContent = "Down";
    down.disabled = index === state.videos.length - 1;
    down.onclick = () => moveVideo(index, 1);
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "ghost";
    drop.textContent = "Leave out";
    drop.onclick = () => {
      state.videos.splice(index, 1);
      drawOrder({ title: $("order-title").textContent, videos: state.videos });
    };
    item.append(label, up, down, drop);
    root.appendChild(item);
  });
}

function moveVideo(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= state.videos.length) return;
  const [video] = state.videos.splice(index, 1);
  state.videos.splice(next, 0, video);
  drawOrder({ title: $("order-title").textContent, videos: state.videos });
}

$("edit-order").onclick = async () => {
  if (!state.videos.length) {
    showWarn("Leave at least one video in the order.");
    return;
  }
  $("edit-order").disabled = true;
  showWarn("");
  try {
    await beginEdit();
    $("status-line").textContent = "Pulling the folder in this order…";
    state.project = await api(`/api/projects/${state.id}/cap-folder`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: state.folderUrl, video_ids: state.videos.map((video) => video.id) }),
    });
    await finishEdit(true);
  } catch (error) {
    showWarn(error.message);
    $("status-line").textContent = "";
  } finally {
    $("edit-order").disabled = false;
  }
};

async function beginEdit() {
  if (state.id) return;
  const created = await api("/api/edits", { method: "POST" });
  state.id = created.id;
  state.project = created;
}

async function finishEdit(skipCameraFile) {
  const body = new FormData();
  for (const name of ["camera", "mic", "screen", "music_intro", "music_body", "sfx_hit", "sfx_click", "sfx_riser"]) {
    if (skipCameraFile && name === "camera") continue;
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
}

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
