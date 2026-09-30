/* Kotak recording pipeline - front end.
   Two independent product panels (TW, CC) driving the same three-step flow. */

const PRODUCTS = { TW: "Two-Wheeler", CC: "Credit Card" };
const state = {};        // product -> { token, sheet, report }
let config = null;

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const fmt = (n) => Number(n).toLocaleString();

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function secs(s) {
  if (s == null) return "";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${Math.round(s % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  config = await api("/api/config");

  const panels = $("#panels");
  Object.keys(PRODUCTS).forEach((product) => {
    state[product] = {};
    const node = $("#panelTpl").content.cloneNode(true);
    const panel = $(".panel", node);
    panel.dataset.product = product;
    $(".pagetitle", panel).textContent = PRODUCTS[product];
    // Each product is delivered under its own prefix; show this panel's.
    const prefix = (window.FILENAME_PREFIXES || {})[product];
    if (prefix) {
      $(".namePattern", panel).textContent = `${prefix}_…_KMBL02120_….mp3`;
    }
    if (product === "TW") panel.classList.add("active");
    wirePanel(panel, product);
    panels.appendChild(node);
  });

  $$(".tab").forEach((tab) => {
    tab.onclick = () => {
      $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".panel").forEach((p) =>
        p.classList.toggle("active", p.dataset.product === tab.dataset.product)
      );
      moveThumb();
    };
  });

  wireSettings();
  paintOutputPaths();
  moveThumb();
  addEventListener("resize", moveThumb);
}

/* Slide the segmented-control highlight under the active tab. */
function moveThumb() {
  const active = $(".tab.active");
  const thumb = $(".segmented .thumb");
  if (!active || !thumb) return;
  const a = active.getBoundingClientRect();
  const seg = active.parentElement.getBoundingClientRect();
  thumb.style.width = `${a.width}px`;
  thumb.style.height = `${a.height}px`;
  thumb.style.transform = `translate(${a.left - seg.left}px, ${a.top - seg.top}px)`;
}

function paintOutputPaths() {
  Object.keys(PRODUCTS).forEach((product) => {
    $(".outPath", panelOf(product)).textContent =
      product === "TW" ? config.output_tw : config.output_cc;
  });
}

const panelOf = (product) => $(`.panel[data-product="${product}"]`);
const tabOf = (product) => $(`.tab[data-product="${product}"]`);

/* ------------------------------------------------------- panel plumbing */

function setStep(panel, n, mode) {
  const step = $(`.step[data-step="${n}"]`, panel);
  step.classList.remove("locked", "active", "done");
  if (mode) step.classList.add(mode);
}

function setChip(panel, text, kind = "") {
  const chip = $(".stateChip", panel);
  chip.textContent = text;
  chip.className = `chip stateChip${kind ? " is-" + kind : ""}`;
}

function wirePanel(panel, product) {
  const drop = $(".drop", panel);
  const input = $("input[type=file]", panel);

  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    if (e.dataTransfer.files[0]) upload(panel, product, e.dataTransfer.files[0]);
  });
  input.onchange = () => input.files[0] && upload(panel, product, input.files[0]);

  $(".sheetSelect", panel).onchange = (e) => inspect(panel, product, e.target.value);
  $(".fillBtn", panel).onclick = () => startFill(panel, product);
  $(".dlBtn", panel).onclick = () => startDownload(panel, product);
}

/* ----------------------------------------------------------- step 1 --- */

async function upload(panel, product, file) {
  const drop = $(".drop", panel);
  $(".dropTitle", drop).textContent = "Reading workbook…";
  $(".dropSub", drop).textContent = file.name;
  setChip(panel, "Reading workbook…", "busy");

  const body = new FormData();
  body.append("file", file);
  body.append("product", product);

  try {
    const data = await api("/api/upload", { method: "POST", body });
    state[product].token = data.token;

    $(".dropTitle", drop).textContent = data.filename;
    $(".dropSub", drop).textContent = "Click to choose a different workbook";

    const select = $(".sheetSelect", panel);
    select.innerHTML = data.sheets
      .map((s) => `<option value="${s}">${s}</option>`)
      .join("");
    $(".sheetRow", panel).classList.remove("hidden");
    $(".sheetPill", panel).textContent =
      data.sheets.length > 1 ? `${data.sheets.length} sheets` : "";

    inspect(panel, product, data.sheets[0]);
  } catch (err) {
    $(".dropTitle", drop).textContent = "Drop the sheet here";
    $(".dropSub", drop).textContent = err.message;
    setChip(panel, "Upload failed", "bad");
  }
}

async function inspect(panel, product, sheet) {
  state[product].sheet = sheet;
  const box = $(".report", panel);
  box.classList.remove("hidden");
  box.innerHTML = `<p class="hint">Checking ${sheet}…</p>`;
  setChip(panel, "Checking sheet…", "busy");

  try {
    const r = await api("/api/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: state[product].token, sheet }),
    });
    state[product].report = r;
    renderReport(panel, product, r);
  } catch (err) {
    box.innerHTML = `<div class="note bad">${err.message}</div>`;
    setChip(panel, "Sheet unreadable", "bad");
  }
}

function renderReport(panel, product, r) {
  const box = $(".report", panel);
  tabOf(product).classList.toggle("loaded", r.ok);

  if (!r.ok) {
    box.innerHTML = `
      <div class="note bad">
        <div><b>This sheet cannot be used.</b> Missing required columns:
        <code>${r.missing_columns.join("</code> <code>")}</code></div>
      </div>
      <p class="hint">Date and time columns are matched automatically whatever
      they are called, so this means a column is genuinely absent &mdash; check
      you picked the right sheet.</p>`;
    setStep(panel, 1, "active");
    setStep(panel, 2, "locked");
    setStep(panel, 3, "locked");
    setChip(panel, "Sheet not usable", "bad");
    return;
  }

  const notes = [];
  const poolReady = (r.tones_available || 0) > 0;

  // This export spelled its date/time columns differently. Name the ROLE each
  // column was read for -- an arrow between two column names reads as if the
  // column were being converted, and in this export the same name is both a
  // source and a destination.
  const ROLE = {
    Call_connect_time: "The date of the call",
    Call_time: "The time of the call",
  };
  const mapped = Object.entries(r.header_mapping || {});
  if (mapped.length) {
    const lines = mapped
      .map(([slot, from]) => `${ROLE[slot] || slot} is read from <code>${from}</code>`)
      .join(".<br>");
    notes.push(`<div class="note ok"><div>This sheet names its date and time
      columns differently from the usual export. ${lines}.</div></div>`);
  }

  // Every row dateless means the sheet is structurally wrong (a raw ADO export
  // keeps the date in its own column). Filling it would only produce a
  // workbook that still cannot be downloaded, so stop here.
  if (r.unparseable_rows === r.rows && r.rows > 0) {
    box.innerHTML = `
      <div class="metrics">
        <div class="metric"><b>${fmt(r.rows)}</b><span>rows</span></div>
        <div class="metric hot"><b>${fmt(r.rows)}</b><span>no usable date</span></div>
      </div>
      <div class="note bad"><div><b>No row has a usable date.</b>
        Every date-like column holds a time of day only, so there is nothing to
        build a <code>YYYY/MM/DD</code> folder from.</div></div>
      <p class="hint">A separate date column is normally detected and used
      automatically. This sheet appears to have none at all &mdash; check the
      export included its date column.</p>`;
    setStep(panel, 1, "active");
    setStep(panel, 2, "locked");
    setStep(panel, 3, "locked");
    setChip(panel, "No usable dates", "bad");
    return;
  }

  if (r.needs_fill > 0) {
    const parts = [];
    if (r.blank) parts.push(`${fmt(r.blank)} blank`);
    if (r.unknown_tone) {
      const eg = r.missing_tone_names.length
        ? ` (${r.missing_tone_names.slice(0, 3).join(", ")})`
        : "";
      parts.push(`${fmt(r.unknown_tone)} naming a tone the folder lacks${eg}`);
    }
    notes.push(`<div class="note warn"><div><b>${fmt(r.needs_fill)}</b> rows have no
      usable recording &mdash; ${parts.join(", ")}. Run step 2 first.</div></div>`);
  } else {
    notes.push(`<div class="note ok"><div>Every row resolves to a recording &mdash;
      step 2 can be skipped.</div></div>`);
  }

  if (!poolReady) {
    notes.push(`<div class="note bad"><div>The NC tone folder has no audio files.
      Set it in Settings.</div></div>`);
  }

  if (r.unparseable_rows > 0) {
    notes.push(`<div class="note bad"><div><b>${fmt(r.unparseable_rows)}</b> rows have no
      usable <code>Call_connect_time</code> (a time with no date, or unreadable) and will
      be logged as failures.</div></div>`);
  }

  if (r.colliding_files > 0) {
    const dup =
      r.exact_duplicate_rows > 0
        ? ` ${fmt(r.exact_duplicate_rows)} are byte-for-byte duplicate rows, which
           usually means a bad export.`
        : "";
    const fate =
      config.on_collision === "suffix"
        ? "They will be saved with <code>_1</code>, <code>_2</code> suffixes."
        : "<b>They will overwrite each other.</b>";
    notes.push(`<div class="note warn"><div><b>${fmt(r.colliding_files)}</b> rows reuse a
      filename another row already produced.${dup} ${fate}</div></div>`);
  }

  box.innerHTML = `
    <div class="metrics">
      <div class="metric"><b>${fmt(r.rows)}</b><span>rows</span></div>
      <div class="metric"><b>${fmt(r.with_link)}</b><span>from S3</span></div>
      <div class="metric"><b>${fmt(r.with_tone)}</b><span>from tones</span></div>
      <div class="metric${r.needs_fill ? " hot" : ""}"><b>${fmt(r.needs_fill)}</b><span>need a tone</span></div>
    </div>
    ${notes.join("")}`;

  const needsFill = r.needs_fill > 0;

  setStep(panel, 1, "done");
  setStep(panel, 2, needsFill ? "active" : "done");
  setStep(panel, 3, needsFill ? "locked" : "active");

  $(".fillBtn", panel).disabled = !needsFill || !poolReady;
  $(".fillPill", panel).textContent = !needsFill
    ? "nothing to fill"
    : poolReady
    ? `${fmt(r.needs_fill)} rows to fill`
    : "set the tone folder in Settings";

  $(".dlBtn", panel).disabled = r.ready === 0;
  $(".dlPill", panel).textContent = `${fmt(r.ready)} ready`;

  setChip(
    panel,
    needsFill ? `${fmt(r.needs_fill)} rows need a tone` : `${fmt(r.ready)} ready to save`,
    needsFill ? "warn" : "ok"
  );
}

/* ----------------------------------------------------------- step 2 --- */

async function startFill(panel, product) {
  const btn = $(".fillBtn", panel);
  btn.disabled = true;
  btn.textContent = "Filling…";
  setChip(panel, "Filling links…", "busy");

  const box = mountProgress(panel, "fill");

  try {
    const { job } = await api("/api/fill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: state[product].token,
        sheet: state[product].sheet,
        product,
      }),
    });

    const snap = await poll(job, (s) => paintProgress(box, s, "fill"));
    btn.textContent = "Fill links";

    if (snap.state === "finished") {
      setStep(panel, 2, "done");
      setChip(panel, `${fmt(snap.filled)} links filled`, "ok");

      const link = $(".getWorkbook", panel);
      link.href = `/api/job/${job}/workbook`;
      link.classList.remove("hidden");

      const use = $(".useFilled", panel);
      use.classList.remove("hidden");
      use.onclick = () => {
        state[product].token = snap.message;   // token of the filled workbook
        use.textContent = "Using filled sheet";
        use.disabled = true;
        inspect(panel, product, state[product].sheet);
      };
    } else {
      btn.disabled = false;
      setChip(panel, snap.state === "error" ? "Fill failed" : "Fill cancelled", "bad");
    }
  } catch (err) {
    box.innerHTML = `<div class="note bad"><div>${err.message}</div></div>`;
    btn.disabled = false;
    btn.textContent = "Fill links";
    setChip(panel, "Fill failed", "bad");
  }
}

/* ----------------------------------------------------------- step 3 --- */

async function startDownload(panel, product) {
  const btn = $(".dlBtn", panel);
  const cancel = $(".cancelBtn", panel);
  btn.disabled = true;
  btn.textContent = "Downloading…";
  $(".getFailures", panel).classList.add("hidden");
  setStep(panel, 3, "active");
  setChip(panel, "Downloading…", "busy");

  const box = mountProgress(panel, "download");

  try {
    const { job } = await api("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: state[product].token,
        sheet: state[product].sheet,
        product,
      }),
    });

    cancel.classList.remove("hidden");
    cancel.onclick = () => {
      cancel.disabled = true;
      cancel.textContent = "Stopping…";
      api(`/api/job/${job}/cancel`, { method: "POST" });
    };

    const snap = await poll(job, (s) => paintProgress(box, s, "download"));

    cancel.classList.add("hidden");
    cancel.disabled = false;
    cancel.textContent = "Cancel";
    btn.disabled = false;
    btn.textContent = snap.state === "cancelled" ? "Resume download" : "Run again";

    if (snap.state === "finished" && snap.failed === 0) {
      setStep(panel, 3, "done");
      setChip(panel, `${fmt(snap.downloaded + (snap.copied || 0) + snap.skipped)} recordings saved`, "ok");
    } else if (snap.state === "cancelled") {
      setChip(panel, "Cancelled — re-run to resume", "warn");
    } else {
      setChip(panel, `${fmt(snap.failed)} rows failed`, "bad");
    }

    if (snap.failure_count > 0) {
      const link = $(".getFailures", panel);
      link.href = `/api/job/${job}/failures.csv`;
      link.textContent = `Failed rows (${fmt(snap.failure_count)})`;
      link.classList.remove("hidden");
    }
  } catch (err) {
    box.innerHTML = `<div class="note bad"><div>${err.message}</div></div>`;
    btn.disabled = false;
    btn.textContent = "Start download";
    setChip(panel, "Download failed", "bad");
  }
}

/* -------------------------------------------------------------- progress */

function mountProgress(panel, kind) {
  const box = $(`.progress[data-kind="${kind}"]`, panel);
  box.classList.remove("hidden", "done");
  box.classList.add("running");
  box.innerHTML = "";
  box.appendChild($("#progressTpl").content.cloneNode(true));
  return box;
}

function paintProgress(box, s, kind) {
  if (!$(".bar", box)) return;      // an error replaced the block

  $(".bar .fill", box).style.width = `${s.percent}%`;
  $(".pct", box).textContent = `${Math.round(s.percent)}%`;
  $(".counter", box).textContent = `${fmt(s.done)} of ${fmt(s.total)}`;
  $(".eta", box).textContent =
    s.state === "running"
      ? s.eta != null
        ? `about ${secs(s.eta)} left`
        : "starting…"
      : `${s.state} in ${secs(s.elapsed)}`;

  if (s.state !== "running") {
    box.classList.remove("running");
    if (s.state === "finished" && !s.failed) box.classList.add("done");
  }

  $(".tallies", box).innerHTML =
    kind === "fill"
      ? `<span class="t-ok">filled <b>${fmt(s.filled)}</b></span>`
      : `<span class="t-ok">downloaded <b>${fmt(s.downloaded)}</b></span>
         <span class="t-ok">copied <b>${fmt(s.copied || 0)}</b></span>
         <span>skipped <b>${fmt(s.skipped)}</b></span>
         <span class="${s.failed ? "t-bad" : ""}">failed <b>${fmt(s.failed)}</b></span>`;

  const log = $(".log", box);
  log.textContent = s.log.join("\n") + (s.state === "error" && s.message ? `\n${s.message}` : "");
}

function poll(jobId, onTick) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const snap = await api(`/api/job/${jobId}`);
        onTick(snap);
        if (snap.state !== "running") {
          clearInterval(timer);
          resolve(snap);
        }
      } catch (err) {
        clearInterval(timer);
        reject(err);
      }
    }, 500);
  });
}

/* -------------------------------------------------------------- settings */

function wireSettings() {
  const modal = $("#settings");
  const open = () => {
    fillSettings();
    modal.classList.remove("hidden");
  };
  const close = () => modal.classList.add("hidden");

  $("#openSettings").onclick = open;
  $("#closeSettings").onclick = close;
  modal.onclick = (e) => e.target === modal && close();
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) close();
  });
  $("#saveSettings").onclick = saveSettings;

  let t;
  $("#nc_folder").oninput = (e) => {
    clearTimeout(t);
    t = setTimeout(() => paintTones(e.target.value.trim()), 350);
  };
}

function fillSettings() {
  $("#output_tw").value = config.output_tw;
  $("#output_cc").value = config.output_cc;
  $("#workers").value = config.workers;
  $("#retries").value = config.retries;
  $("#timeout").value = config.timeout;
  $("#on_collision").value = config.on_collision;
  $("#nc_folder").value = config.nc_folder;
  paintTones(config.nc_folder);
}

/* Show what the tone folder actually contains. */
async function paintTones(folder) {
  const box = $("#pool");
  box.innerHTML = `<p class="hint">Reading folder…</p>`;
  try {
    const r = await api(`/api/tones?folder=${encodeURIComponent(folder)}`);
    config.tones_available = r.tones.length;
    if (!r.exists) {
      box.innerHTML = `<div class="note bad"><div>That folder does not exist.</div></div>`;
    } else if (!r.tones.length) {
      box.innerHTML = `<div class="note bad"><div>No audio files in that folder.</div></div>`;
    } else {
      box.innerHTML =
        `<div class="tonegrid">${r.tones
          .map((t) => `<span class="tone"><b>${t.label}</b>${(t.size / 1024).toFixed(0)} KB</span>`)
          .join("")}</div>
         <p class="hint">${r.tones.length} tones found.</p>`;
    }
  } catch (err) {
    box.innerHTML = `<div class="note bad"><div>${err.message}</div></div>`;
  }
}

async function saveSettings() {
  config = await api("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      output_tw: $("#output_tw").value.trim(),
      output_cc: $("#output_cc").value.trim(),
      workers: +$("#workers").value,
      retries: +$("#retries").value,
      timeout: +$("#timeout").value,
      on_collision: $("#on_collision").value,
      nc_folder: $("#nc_folder").value.trim(),
    }),
  });

  paintOutputPaths();
  Object.keys(PRODUCTS).forEach((product) => {
    const r = state[product].report;
    if (r && r.ok) renderReport(panelOf(product), product, r);
  });

  const saved = $(".saved");
  saved.classList.remove("hidden");
  setTimeout(() => saved.classList.add("hidden"), 1800);
}

boot();
