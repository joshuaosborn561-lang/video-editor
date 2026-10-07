const state = { id: null, project: null, folderUrl: "", videos: [], broll: [] };

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
  const clips = $("clips").files;
  if (!capUrl && !camera && !clips.length) {
    showWarn("Paste a Cap link, add the b-roll, or choose the recording.");
    return;
  }
  $("edit").disabled = true;
  try {
    if (isFolder(capUrl) || (clips.length && !capUrl)) {
      await beginEdit();
      if (hasFiles()) {
        $("status-line").textContent = "Reading the b-roll and the mic…";
        state.project = await uploadFootage(true);
      }
      if (isFolder(capUrl)) {
        await loadFolder(capUrl);
        return;
      }
      if (showLocalOrder()) return;
      await confirmOrder();
      return;
    }
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

function hasFiles() {
  if ($("clips").files.length || $("mic").files[0]) return true;
  for (const name of ["camera", "screen", "music_intro", "music_body", "sfx_hit", "sfx_click", "sfx_riser"]) {
    if ($(name).files[0]) return true;
  }
  return false;
}

function roleFromTitle(title) {
  if (/\bintro\b/i.test(title || "")) return "intro";
  if (/\boutro\b|\bclosing\b/i.test(title || "")) return "outro";
  return "screen";
}

async function loadFolder(url) {
  $("status-line").textContent = "Reading the folder…";
  const listing = await api("/api/cap-folder", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url }),
  });
  state.folderUrl = listing.url;
  state.broll = [];
  state.videos = (listing.videos || []).map((video) => ({
    id: video.id,
    title: video.title || video.id,
    role: roleFromTitle(video.title),
    source: "cap",
  }));
  absorbUploads();
  drawOrder(listing.title || "Videos in this folder");
}

function showLocalOrder() {
  state.folderUrl = "";
  state.videos = [];
  state.broll = [];
  absorbUploads();
  if (!state.videos.length) return false;
  drawOrder("Clips that did not lock to the mic");
  $("status-line").textContent = "These did not lock to the mic. Set their order, then edit.";
  return true;
}

function absorbUploads() {
  const uploads = (state.project && state.project.footage && state.project.footage.uploads) || [];
  for (const item of uploads) {
    const role = item.suggested_role || "broll";
    if (role === "broll") {
      state.broll.push({ id: item.id, title: item.title || item.id, role: "broll", sync: item.sync || null, source: "file" });
    } else {
      state.videos.push({ id: item.id, title: item.title || item.id, role, source: "file" });
    }
  }
}

function drawOrder(title) {
  $("order-section").hidden = false;
  $("order-title").textContent = title || "Videos in this folder";
  const root = $("order-list");
  root.innerHTML = "";
  state.videos.forEach((video, index) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${index + 1}. ${video.title || video.id}`;
    const role = document.createElement("select");
    for (const [value, text] of [["screen", "Screen"], ["intro", "Intro"], ["outro", "Outro"]]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      option.selected = video.role === value;
      role.appendChild(option);
    }
    role.onchange = () => {
      state.videos[index].role = role.value;
    };
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
    const asBroll = document.createElement("button");
    asBroll.type = "button";
    asBroll.className = "ghost";
    asBroll.textContent = "This is b-roll";
    asBroll.onclick = () => {
      const [moved] = state.videos.splice(index, 1);
      state.broll.push({ ...moved, role: "broll" });
      drawOrder($("order-title").textContent);
    };
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "ghost";
    drop.textContent = "Leave out";
    drop.onclick = () => {
      state.videos.splice(index, 1);
      drawOrder($("order-title").textContent);
    };
    item.append(label, role, up, down, asBroll, drop);
    root.appendChild(item);
  });
  drawBroll();
  const locked = state.broll.filter((video) => video.sync && video.sync.ok).length;
  if (locked) {
    $("status-line").textContent = `${locked} b-roll clip${locked === 1 ? "" : "s"} lock to the mic. Set the Cap order, then edit.`;
  } else if (state.videos.length) {
    $("status-line").textContent = "Set the Cap order, then edit.";
  }
}

function drawBroll() {
  const root = $("broll-list");
  root.innerHTML = "";
  if (!state.broll.length) {
    const item = document.createElement("li");
    item.textContent = "No b-roll locked to the mic yet.";
    root.appendChild(item);
    return;
  }
  state.broll.forEach((video, index) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = video.title || video.id;
    const note = document.createElement("span");
    note.className = "note";
    note.textContent = video.sync && video.sync.ok ? `mic ${clock(video.sync.mic_start)}` : "will sit in the order";
    const into = document.createElement("button");
    into.type = "button";
    into.className = "ghost";
    into.textContent = "Put in the sequence";
    into.onclick = () => {
      const [moved] = state.broll.splice(index, 1);
      state.videos.push({ ...moved, role: roleFromTitle(moved.title) });
      drawOrder($("order-title").textContent);
    };
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "ghost";
    drop.textContent = "Leave out";
    drop.onclick = () => {
      state.broll.splice(index, 1);
      drawBroll();
    };
    item.append(label, note, into, drop);
    root.appendChild(item);
  });
}

function clock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function moveVideo(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= state.videos.length) return;
  const [video] = state.videos.splice(index, 1);
  state.videos.splice(next, 0, video);
  drawOrder($("order-title").textContent);
}

$("edit-order").onclick = async () => {
  if (!state.videos.length && !state.broll.length) {
    showWarn("Leave at least one video in the order.");
    return;
  }
  $("edit-order").disabled = true;
  showWarn("");
  try {
    await confirmOrder();
  } catch (error) {
    showWarn(error.message);
    $("status-line").textContent = "";
  } finally {
    $("edit-order").disabled = false;
  }
};

async function confirmOrder() {
  await beginEdit();
  if (hasFiles()) {
    $("status-line").textContent = "Uploading the b-roll and the mic…";
    state.project = await uploadFootage(true);
  }
  $("status-line").textContent = "Pulling the folder and locking b-roll to the mic…";
  const roles = {};
  for (const video of state.videos) roles[video.id] = video.role;
  state.project = await api(`/api/projects/${state.id}/cap-folder`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      url: state.folderUrl,
      video_ids: state.videos.map((video) => video.id),
      roles,
      broll_ids: state.broll.map((video) => video.id),
    }),
  });
  await finishEdit(true);
}

async function beginEdit() {
  if (state.id) return;
  const created = await api("/api/edits", { method: "POST" });
  state.id = created.id;
  state.project = created;
}

async function uploadFootage(skipCameraFile) {
  const body = new FormData();
  for (const file of $("clips").files) body.append("clips", file);
  for (const name of ["camera", "mic", "screen", "music_intro", "music_body", "sfx_hit", "sfx_click", "sfx_riser"]) {
    if (skipCameraFile && name === "camera") continue;
    const file = $(name).files[0];
    if (file) body.append(name, file);
  }
  if (![...body.keys()].length) return state.project;
  return api(`/api/projects/${state.id}/footage`, { method: "POST", body });
}

async function finishEdit(skipCameraFile) {
  if (!skipCameraFile || hasFiles()) {
    const uploaded = await uploadFootage(skipCameraFile);
    if (uploaded) state.project = uploaded;
  }
  const footage = (state.project && state.project.footage) || {};
  if (!footage.camera && !footage.sequence) {
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
  const note = state.project.render && state.project.render.note ? ` ${state.project.render.note}` : "";
  $("approved-line").textContent = `Cut is ${kept}s. Captions follow the ${captions}.${note}`;
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
