const $ = (id) => document.getElementById(id);

$("image-form").onsubmit = async (event) => {
  event.preventDefault();
  $("warn").hidden = true;
  const body = new FormData();
  const face = $("face").files[0];
  if (face) body.append("face", face);
  body.append("line1", $("line1").value.trim());
  body.append("line2", $("line2").value.trim());
  body.append("line3", $("line3").value.trim());
  body.append("highlight", $("highlight").value.trim() || $("line1").value.trim());
  body.append("accent", $("accent").value);
  body.append("desaturate", $("desaturate").checked ? "true" : "false");
  const response = await fetch("/api/images", { method: "POST", body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || response.statusText;
    $("warn").hidden = false;
    $("warn").textContent = typeof detail === "string" ? detail : JSON.stringify(detail);
    return;
  }
  const image = $("thumb-preview");
  image.hidden = false;
  image.src = `/api/projects/${data.id}/thumbnail.jpg?t=${Date.now()}`;
};
