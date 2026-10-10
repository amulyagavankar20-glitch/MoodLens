// moodlens frontend. webcam -> jpeg frames -> websocket -> json -> canvas.

const MOODS = {
  happy:    { emoji: "😄", color: "#facc15" },
  sad:      { emoji: "😢", color: "#60a5fa" },
  angry:    { emoji: "😠", color: "#f43f5e" },
  surprise: { emoji: "😲", color: "#f97316" },
  fear:     { emoji: "😨", color: "#a78bfa" },
  disgust:  { emoji: "🤢", color: "#4ade80" },
  neutral:  { emoji: "😐", color: "#94a3b8" },
};
const ALL = Object.keys(MOODS);

const $ = (id) => document.getElementById(id);
const el = {
  video: $("video"), overlay: $("overlay"), stage: $("tab-live"),
  placeholder: $("placeholder"), flash: $("flash"),
  start: $("btn-start"), stop: $("btn-stop"), snap: $("btn-snap"),
  engine: $("engine"), note: $("engine-note"),
  conn: $("conn"), fps: $("fps"), lat: $("lat"),
  moodCard: $("mood-card"), moodEmoji: $("mood-emoji"), moodName: $("mood-name"),
  moodConf: $("mood-conf"), facesCount: $("faces-count"),
  bars: $("bars"), timeline: $("timeline"), legend: $("legend"), stats: $("stats"),
  clear: $("btn-clear"),
};

const state = {
  ws: null, stream: null, running: false, inflight: false,
  engines: {}, activeEngine: "haar",
  lastMood: null, history: [], counts: {}, frames: 0, startedAt: 0,
  fpsTicks: [], facesSeen: new Set(), peak: 0,
};

const SEND_W = 480;       // downscale before sending, keeps latency low
const JPEG_Q = 0.7;
const HISTORY_MAX = 120;
const grab = document.createElement("canvas");

// ---------- ui scaffolding ----------

function buildBars() {
  el.bars.innerHTML = "";
  ALL.forEach((m) => {
    const row = document.createElement("div");
    row.className = "bar";
    row.id = "bar-" + m;
    row.style.setProperty("--c", MOODS[m].color);
    row.innerHTML = `<span>${MOODS[m].emoji}</span><span>${m}</span>
      <div class="track"><div class="fill"></div></div><span class="pct">0%</span>`;
    el.bars.appendChild(row);
  });
  el.legend.innerHTML = ALL.map(
    (m) => `<span><i style="--c:${MOODS[m].color}"></i>${m}</span>`
  ).join("");
}

function setBars(scores) {
  const supported = state.engines[state.activeEngine]?.moods || ALL;
  ALL.forEach((m) => {
    const row = $("bar-" + m);
    const v = scores?.[m] ?? 0;
    row.classList.toggle("dead", !supported.includes(m));
    row.querySelector(".fill").style.width = (v * 100).toFixed(0) + "%";
    row.querySelector(".pct").textContent = (v * 100).toFixed(0) + "%";
  });
}

function setHero(face, count) {
  el.facesCount.textContent = `${count} face${count === 1 ? "" : "s"}`;
  if (!face) {
    el.moodEmoji.textContent = "🫥";
    el.moodName.textContent = "no face yet";
    el.moodConf.textContent = "waiting…";
    el.moodCard.style.boxShadow = "";
    el.moodCard.style.borderColor = "";
    el.stage.classList.remove("glow");
    state.lastMood = null;
    return;
  }
  const meta = MOODS[face.mood];
  if (state.lastMood !== face.mood) {
    el.moodEmoji.textContent = meta.emoji;
    el.moodEmoji.classList.remove("pop");
    void el.moodEmoji.offsetWidth;
    el.moodEmoji.classList.add("pop");
    state.lastMood = face.mood;
  }
  el.moodName.textContent = face.mood;
  el.moodName.style.color = meta.color;
  el.moodConf.textContent = `${(face.confidence * 100).toFixed(0)}% sure`;
  el.moodCard.style.boxShadow = `0 0 50px -12px ${meta.color}`;
  el.moodCard.style.borderColor = meta.color + "66";
  el.stage.style.setProperty("--glow", meta.color);
  el.stage.classList.add("glow");
}

function renderStats() {
  const secs = state.startedAt ? (Date.now() - state.startedAt) / 1000 : 0;
  const total = Object.values(state.counts).reduce((a, b) => a + b, 0);
  let top = "–";
  if (total) {
    const m = Object.entries(state.counts).sort((a, b) => b[1] - a[1])[0][0];
    top = MOODS[m].emoji + " " + m;
  }
  const cells = [
    [top, "dominant mood"],
    [state.facesSeen.size, "unique faces"],
    [state.frames, "frames analysed"],
    [secs < 60 ? secs.toFixed(0) + "s" : (secs / 60).toFixed(1) + "m", "session time"],
  ];
  el.stats.innerHTML = cells.map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
}

// ---------- drawing ----------

function drawFaces(canvas, faces, srcW, srcH) {
  canvas.width = srcW; canvas.height = srcH;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, srcW, srcH);
  const lw = Math.max(2, srcW / 320);
  faces.forEach((f) => {
    const [x, y, w, h] = f.box;
    const c = MOODS[f.mood].color;
    ctx.strokeStyle = c; ctx.lineWidth = lw;
    ctx.shadowColor = c; ctx.shadowBlur = 14;
    // corner brackets instead of a full box. more "scanner", less 2012
    const L = Math.min(w, h) * 0.22;
    ctx.beginPath();
    [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]].forEach(([cx, cy, dx, dy]) => {
      ctx.moveTo(cx, cy + dy * L); ctx.lineTo(cx, cy); ctx.lineTo(cx + dx * L, cy);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;

    // label (canvas is mirrored by css, so un-mirror the text for the live view)
    const label = `${MOODS[f.mood].emoji} ${f.mood} ${(f.confidence * 100).toFixed(0)}%`;
    const fs = Math.max(14, srcW / 34);
    ctx.font = `600 ${fs}px "Space Grotesk", sans-serif`;
    const tw = ctx.measureText(label).width + fs;
    const ly = Math.max(0, y - fs * 1.7);
    ctx.save();
    if (canvas.dataset.mirror === "1") {
      ctx.translate(x + tw / 2, 0); ctx.scale(-1, 1); ctx.translate(-(x + tw / 2), 0);
    }
    ctx.fillStyle = "rgba(10,10,20,.78)";
    ctx.beginPath(); ctx.roundRect(x, ly, tw, fs * 1.5, 8); ctx.fill();
    ctx.fillStyle = c;
    ctx.fillText(label, x + fs / 2, ly + fs * 1.1);
    ctx.restore();
  });
}

function drawTimeline() {
  const c = el.timeline, ctx = c.getContext("2d");
  const W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(255,255,255,.06)"; ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) { ctx.beginPath(); ctx.moveTo(0, (H / 4) * i); ctx.lineTo(W, (H / 4) * i); ctx.stroke(); }
  if (state.history.length < 2) return;
  const step = W / (HISTORY_MAX - 1);
  const off = HISTORY_MAX - state.history.length;
  ALL.forEach((m) => {
    if (!state.history.some((h) => (h[m] || 0) > 0.02)) return;
    ctx.strokeStyle = MOODS[m].color; ctx.lineWidth = 2; ctx.lineJoin = "round";
    ctx.shadowColor = MOODS[m].color; ctx.shadowBlur = 6;
    ctx.beginPath();
    state.history.forEach((h, i) => {
      const x = (off + i) * step, y = H - 4 - (h[m] || 0) * (H - 8);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  });
  ctx.shadowBlur = 0;
}

// ---------- results ----------

function handleResult(r) {
  state.frames++;
  state.fpsTicks.push(performance.now());
  el.lat.textContent = r.latency_ms + " ms";

  drawFaces(el.overlay, r.faces, r.width, r.height);

  // hero shows the biggest face
  const main = [...r.faces].sort((a, b) => b.box[2] * b.box[3] - a.box[2] * a.box[3])[0];
  setHero(main, r.faces.length);
  setBars(main?.scores);

  if (main) {
    state.counts[main.mood] = (state.counts[main.mood] || 0) + 1;
    state.history.push(main.scores);
    if (state.history.length > HISTORY_MAX) state.history.shift();
    drawTimeline();
  }
  r.faces.forEach((f) => state.facesSeen.add(f.id));
}

// ---------- websocket + camera loop ----------

function connect() {
  return new Promise((resolve, reject) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      el.conn.textContent = "live"; el.conn.className = "pill on";
      ws.send(JSON.stringify({ engine: state.activeEngine }));
      resolve(ws);
    };
    ws.onerror = () => reject(new Error("ws failed"));
    ws.onclose = () => {
      el.conn.textContent = "offline"; el.conn.className = "pill off";
      if (state.running) stopCamera();
    };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "result") { state.inflight = false; handleResult(msg); }
      else if (msg.type === "error") { showNote(msg.message, true); revertEngine(); }
      else if (msg.type === "engine") showNote(engineNote(msg.engine));
    };
    state.ws = ws;
  });
}

function sendFrame() {
  if (!state.running) return;
  const v = el.video;
  if (!state.inflight && v.videoWidth && state.ws?.readyState === 1) {
    const s = SEND_W / v.videoWidth;
    grab.width = SEND_W; grab.height = Math.round(v.videoHeight * s);
    grab.getContext("2d").drawImage(v, 0, 0, grab.width, grab.height);
    state.inflight = true;
    grab.toBlob((b) => b && state.ws?.readyState === 1 && b.arrayBuffer().then((buf) => state.ws.send(buf)), "image/jpeg", JPEG_Q);
  }
  requestAnimationFrame(sendFrame);
}

async function startCamera() {
  el.start.disabled = true;
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 960, height: 720, facingMode: "user" }, audio: false });
  } catch (e) {
    showNote("camera blocked or missing: " + e.message, true);
    el.start.disabled = false;
    return;
  }
  el.video.srcObject = state.stream;
  await el.video.play();
  try { await connect(); } catch { showNote("can't reach the backend websocket", true); stopCamera(); return; }
  el.placeholder.classList.add("hidden");
  el.overlay.dataset.mirror = "1";
  state.running = true; state.inflight = false;
  state.startedAt = state.startedAt || Date.now();
  el.stop.disabled = false; el.snap.disabled = false;
  requestAnimationFrame(sendFrame);
}

function stopCamera() {
  state.running = false;
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  try { state.ws?.close(); } catch {}
  el.video.srcObject = null;
  el.overlay.getContext("2d").clearRect(0, 0, el.overlay.width, el.overlay.height);
  el.placeholder.classList.remove("hidden");
  el.start.disabled = false; el.stop.disabled = true; el.snap.disabled = true;
  setHero(null, 0); setBars(null);
  el.fps.textContent = "0 fps";
}

function snapshot() {
  const v = el.video;
  const c = document.createElement("canvas");
  c.width = v.videoWidth; c.height = v.videoHeight;
  const ctx = c.getContext("2d");
  ctx.translate(c.width, 0); ctx.scale(-1, 1);
  ctx.drawImage(v, 0, 0);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.save(); ctx.translate(c.width, 0); ctx.scale(-1, 1);
  ctx.drawImage(el.overlay, 0, 0, c.width, c.height);
  ctx.restore();
  const a = document.createElement("a");
  a.href = c.toDataURL("image/png");
  a.download = `moodlens-${Date.now()}.png`;
  a.click();
  el.flash.classList.remove("go"); void el.flash.offsetWidth; el.flash.classList.add("go");
}

// ---------- engine picker ----------

function engineNote(name) {
  return name === "haar"
    ? "haar can only tell smiling from not smiling. switch to deepface for sad / angry / surprise etc."
    : "deepface: 7 moods. first frame is slow while the model loads.";
}
function showNote(t, warn = false) { el.note.textContent = t; el.note.classList.toggle("warn", warn); }
function revertEngine() { state.activeEngine = "haar"; el.engine.value = "haar"; }

async function loadEngines() {
  try {
    const r = await (await fetch("/api/engines")).json();
    state.engines = r.engines;
    state.activeEngine = r.default;
    el.engine.value = r.default;
    [...el.engine.options].forEach((o) => {
      if (!r.engines[o.value]?.available) o.textContent += " — not installed";
    });
    showNote(engineNote(r.default));
  } catch { showNote("backend not reachable", true); }
}

el.engine.addEventListener("change", () => {
  const name = el.engine.value;
  if (!state.engines[name]?.available) {
    showNote("deepface isn't installed on this server. see README → 'full mood mode'.", true);
    el.engine.value = state.activeEngine;
    return;
  }
  state.activeEngine = name;
  setBars(null);
  if (state.ws?.readyState === 1) state.ws.send(JSON.stringify({ engine: name }));
  showNote(engineNote(name));
});

// ---------- upload tab ----------

const upImg = $("upload-img"), upOv = $("upload-overlay"), upView = $("upload-view"), drop = $("drop"), file = $("file");

async function analyzeFile(f) {
  if (!f || !f.type.startsWith("image/")) return;
  upImg.src = URL.createObjectURL(f);
  upView.classList.remove("hidden"); drop.classList.add("hidden");
  const fd = new FormData(); fd.append("file", f);
  try {
    const res = await fetch(`/api/analyze?engine=${state.activeEngine}`, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const r = await res.json();
    await upImg.decode();
    upOv.dataset.mirror = "0";
    drawFaces(upOv, r.faces, r.width, r.height);
    const main = r.faces[0];
    setHero(main, r.faces.length); setBars(main?.scores);
    el.lat.textContent = r.latency_ms + " ms";
    showNote(r.faces.length ? "" : "no faces found in that image");
  } catch (e) { showNote(e.message, true); }
}
file.addEventListener("change", () => analyzeFile(file.files[0]));
["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => analyzeFile(e.dataTransfer.files[0]));
upView.addEventListener("click", () => { upView.classList.add("hidden"); drop.classList.remove("hidden"); file.value = ""; });

// ---------- tabs / wiring ----------

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $("tab-live").classList.toggle("hidden", t.dataset.tab !== "live");
    $("tab-upload").classList.toggle("hidden", t.dataset.tab !== "upload");
  })
);
el.start.addEventListener("click", startCamera);
el.stop.addEventListener("click", stopCamera);
el.snap.addEventListener("click", snapshot);
el.clear.addEventListener("click", () => {
  state.history = []; state.counts = {}; state.frames = 0; state.facesSeen.clear();
  state.startedAt = state.running ? Date.now() : 0;
  state.ws?.readyState === 1 && state.ws.send(JSON.stringify({ reset: true }));
  drawTimeline(); renderStats();
});

setInterval(() => {
  const now = performance.now();
  state.fpsTicks = state.fpsTicks.filter((t) => now - t < 1000);
  if (state.running) el.fps.textContent = state.fpsTicks.length + " fps";
  renderStats();
}, 500);

buildBars(); setBars(null); renderStats(); drawTimeline(); loadEngines();
