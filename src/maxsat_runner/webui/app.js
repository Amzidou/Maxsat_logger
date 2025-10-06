let solverCount = 0;

function addSolver(valueCmd = "", valueAlias = "") {
  solverCount++;
  const div = document.createElement("div");
  div.className = "row g-2 align-items-start mb-2";
  div.id = "solver-" + solverCount;
  div.innerHTML = `
    <div class="col-md-3">
      <input type="text" class="form-control solver-alias" placeholder="Alias (ex: ttinc)" value="${valueAlias}">
      <div class="invalid-feedback">Alias requis.</div>
    </div>
    <div class="col-md-8">
      <input type="text" class="form-control solver-cmd" placeholder="Commande solver avec {inst}" value="${valueCmd}">
      <div class="invalid-feedback">La commande doit contenir {inst}.</div>
    </div>
    <div class="col-md-1 d-grid">
      <button class="btn btn-outline-danger" type="button" onclick="removeSolver(${solverCount})">✕</button>
    </div>
  `;
  document.getElementById("solversList").appendChild(div);
  const inAlias = div.querySelector(".solver-alias");
  const inCmd = div.querySelector(".solver-cmd");

  // Auto-alias (si vide) lorsqu'on quitte le champ commande
  inCmd.addEventListener("blur", () => {
    if (inAlias.value.trim() === "") {
      const derived = deriveAliasFromCmd(inCmd.value.trim());
      if (derived) inAlias.value = derived;
    }
    validateSolvers();
  });
  inAlias.addEventListener("input", validateSolvers);
  inCmd.addEventListener("input", validateSolvers);
}

function deriveAliasFromCmd(cmd) {
  if (!cmd) return "";
  let s = cmd.trim();
  if (s.startsWith("[cwd=")) {
    const r = s.indexOf("]");
    if (r !== -1) s = s.slice(r + 1).trim();
  }
  const tok = s.split(/\s+/)[0] || "";
  const base = tok.split("/").pop().split("\\").pop();
  return base || "";
}

function removeSolver(id) {
  const div = document.getElementById("solver-" + id);
  if (div) div.remove();
  validateSolvers();
}

function validateSolvers() {
  let ok = true;
  const aliases = document.querySelectorAll(".solver-alias");
  const cmds = document.querySelectorAll(".solver-cmd");
  if (aliases.length === 0) ok = false;

  aliases.forEach((el) => {
    const valid = el.value.trim().length > 0;
    el.classList.toggle("is-invalid", !valid);
    el.classList.toggle("is-valid", valid);
    ok = ok && valid;
  });
  cmds.forEach((el) => {
    const v = el.value.trim();
    const valid = v.length > 0 && v.includes("{inst}");
    el.classList.toggle("is-invalid", !valid);
    el.classList.toggle("is-valid", valid);
    ok = ok && valid;
  });
  return ok;
}

function showMessage(msg, type = "danger") {
  const out = document.getElementById("out");
  out.textContent = `[${type.toUpperCase()}] ${msg}`;
}

async function submitJob() {
  if (!validateSolvers()) {
    showMessage(
      "Complète les solveurs: alias requis et {inst} dans la commande.",
      "danger"
    );
    const bad = document.querySelector(".is-invalid");
    if (bad) bad.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const pairs = Array.from(document.querySelectorAll("#solversList .row")).map(
    (row) => {
      return {
        alias: row.querySelector(".solver-alias").value.trim(),
        cmd: row.querySelector(".solver-cmd").value.trim(),
      };
    }
  );

  const timeoutStr = document.getElementById("timeout_sec").value.trim();
  const timeout_sec = timeoutStr ? parseInt(timeoutStr, 10) : null;

  const body = {
    solver_pairs: pairs,
    instances_dir: document.getElementById("instances_dir").value.trim(),
    pattern: document.getElementById("pattern").value.trim(),
    out_dir: document.getElementById("out_dir").value.trim(),
  };
  if (timeout_sec) body.timeout_sec = timeout_sec;

  try {
    const r = await fetch("/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    document.getElementById("out").textContent = JSON.stringify(j, null, 2);
    if (j.ok) {
      pollStatus(j.job_id);
    }
  } catch (e) {
    showMessage("Erreur réseau: " + e, "danger");
  }
}

async function pollStatus(id) {
  let done = false;
  while (!done) {
    await new Promise((r) => setTimeout(r, 1000));
    const r = await fetch("/status/" + encodeURIComponent(id));
    const j = await r.json();
    document.getElementById("out").textContent = JSON.stringify(j, null, 2);
    if (j.status === "done" || j.status === "error") {
      done = true;
    }
  }
}

// ======== STATS =========

function readFloat(id) {
  const v = document.getElementById(id).value.trim();
  if (v === "") return null;
  const f = parseFloat(v);
  return Number.isFinite(f) ? f : null;
}

function addCacheBuster(url, ts) {
  if (!url) return null;
  return url + (url.includes("?") ? "&" : "?") + "t=" + ts;
}

function hideTable(wrapId, tableId) {
  const table = document.getElementById(tableId);
  table.innerHTML = "";
  document.getElementById(wrapId).style.display = "none";
}

async function renderCSVTable(csvUrl, wrapId, tableId) {
  try {
    const res = await fetch(
      csvUrl + (csvUrl.includes("?") ? "&" : "?") + "t=" + Date.now()
    );
    const text = await res.text();
    const rows = parseCSV(text);
    if (!rows || rows.length === 0) {
      hideTable(wrapId, tableId);
      return;
    }
    const table = document.getElementById(tableId);
    table.innerHTML = "";

    const thead = document.createElement("thead");
    const hdrTr = document.createElement("tr");
    for (const h of rows[0]) {
      const th = document.createElement("th");
      th.textContent = h;
      hdrTr.appendChild(th);
    }
    thead.appendChild(hdrTr);

    const tbody = document.createElement("tbody");
    for (let i = 1; i < rows.length; i++) {
      const tr = document.createElement("tr");
      for (const cell of rows[i]) {
        const td = document.createElement("td");
        td.textContent = cell;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(thead);
    table.appendChild(tbody);

    document.getElementById(wrapId).style.display = "";
  } catch (e) {
    console.error("renderCSVTable error:", e);
    hideTable(wrapId, tableId);
  }
}

async function submitStats() {
  const runs_dir = document.getElementById("runs_dir").value.trim() || "runs";
  const out_dir =
    document.getElementById("reports_dir").value.trim() || "reports";
  const by = document.getElementById("by_key").value || "solver_alias";
  const instance = document.getElementById("instance_name").value.trim();

  const t_min = readFloat("t_min");
  const t_max = readFloat("t_max");
  const t_at = readFloat("t_at");
  const log_time = document.getElementById("log_time").checked;

  const body = { runs_dir, out_dir, by, log_time };
  if (instance) body.instance = instance;
  if (t_min !== null) body.t_min = t_min;
  if (t_max !== null) body.t_max = t_max;
  if (t_at !== null) body.t_at = t_at;

  try {
    const r = await fetch("/stats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    document.getElementById("out").textContent = JSON.stringify(j, null, 2);

    // Images globales
    const prev = document.getElementById("previews");
    prev.innerHTML = "";
    const ts = Date.now();
    const imgs = [
      ["Leaderboard (wins)", j.plot_leaderboard_wins_url],
      ["Time to Best (box)", j.plot_time_to_best_box_url],
      ["Trajectoire (instance)", j.plot_trajectory_url],
      ["Average scores over time", j.avg_scores_png_url],
      ["Distribution scores over time", j.score_dist_png_url],
    ];
    for (const [title, url0] of imgs) {
      const url = addCacheBuster(url0, ts);
      if (!url) continue;
      const card = document.createElement("div");
      card.className = "card my-3";
      card.innerHTML = `
        <div class="card-header">${title}</div>
        <div class="card-body">
          <img src="${url}" class="img-fluid" alt="${title}">
          <div class="mt-2"><a href="${url}" target="_blank" rel="noopener">Ouvrir l'image</a></div>
        </div>
      `;
      prev.appendChild(card);
    }

    // Lien CSV des moyennes
    if (j.avg_scores_csv_url) {
      const wrap = document.createElement("div");
      wrap.className = "my-2";
      const a = document.createElement("a");
      a.href =
        j.avg_scores_csv_url +
        (j.avg_scores_csv_url.includes("?") ? "&" : "?") +
        "t=" +
        Date.now();
      a.textContent = "Télécharger average_scores_over_time.csv";
      a.target = "_blank";
      a.rel = "noopener";
      wrap.appendChild(a);
      prev.appendChild(wrap);
    }

    // Leaderboard (classique) CSV
    if (j.leaderboard_csv_url) {
      await renderCSVTable(
        j.leaderboard_csv_url,
        "leaderboardWrap",
        "leaderboardTable"
      );
      addDownloadLink(
        "leaderboardWrap",
        j.leaderboard_csv_url,
        "Télécharger leaderboard.csv"
      );
    } else {
      hideTable("leaderboardWrap", "leaderboardTable");
    }

    // Leaderboard relatif CSV
    if (j.leaderboard_relative_csv_url) {
      await renderCSVTable(
        j.leaderboard_relative_csv_url,
        "leaderboardRelWrap",
        "leaderboardRelTable"
      );
      addDownloadLink(
        "leaderboardRelWrap",
        j.leaderboard_relative_csv_url,
        "Télécharger leaderboard_relative.csv"
      );
    } else {
      hideTable("leaderboardRelWrap", "leaderboardRelTable");
    }

    const galR = document.getElementById("meanGallery");
    galR.innerHTML = "";
    if (
      Array.isArray(j.replicas_by_solver_plots) &&
      j.replicas_by_solver_plots.length > 0
    ) {
      const row = document.createElement("div");
      row.className = "row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3";
      for (const it of j.replicas_by_solver_plots) {
        const col = document.createElement("div");
        col.className = "col";
        const url = addCacheBuster(it.url, Date.now());
        col.innerHTML = `
      <div class="card h-100">
        <div class="card-header">Replicas – ${it.solver}</div>
        <img class="card-img-top" src="${url}">
        <div class="card-body">
          <a href="${url}" target="_blank" rel="noopener">Ouvrir l'image</a>
        </div>
      </div>`;
        row.appendChild(col);
      }
      galR.appendChild(row);
    }

    // Galerie COST par instance
    const gal = document.getElementById("instGallery");
    gal.innerHTML = "";
    if (Array.isArray(j.instance_plots) && j.instance_plots.length > 0) {
      const row = document.createElement("div");
      row.className = "row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3";
      for (const it of j.instance_plots) {
        if (!it.url) continue;
        const col = document.createElement("div");
        col.className = "col";
        const url = addCacheBuster(it.url, Date.now());
        col.innerHTML = `
          <div class="card h-100">
            <div class="card-header">${it.instance}</div>
            <img class="card-img-top" src="${url}" alt="${it.instance}">
            <div class="card-body">
              <a href="${url}" target="_blank" rel="noopener">Ouvrir l'image</a>
            </div>
          </div>`;
        row.appendChild(col);
      }
      gal.appendChild(row);
    }

    // Galerie SCORES par instance
    const galS = document.getElementById("instScoreGallery");
    galS.innerHTML = "";
    if (
      Array.isArray(j.instance_score_plots) &&
      j.instance_score_plots.length > 0
    ) {
      const row = document.createElement("div");
      row.className = "row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3";
      for (const it of j.instance_score_plots) {
        if (!it.url) continue;
        const col = document.createElement("div");
        col.className = "col";
        const url = addCacheBuster(it.url, Date.now());
        col.innerHTML = `
          <div class="card h-100">
            <div class="card-header">Scores – ${it.instance}</div>
            <img class="card-img-top" src="${url}" alt="${it.instance}">
            <div class="card-body">
              <a href="${url}" target="_blank" rel="noopener">Ouvrir l'image</a>
            </div>
          </div>`;
        row.appendChild(col);
      }
      galS.appendChild(row);
    }
  } catch (e) {
    showMessage("Erreur réseau: " + e, "danger");
  }
}

function addDownloadLink(wrapperId, url, label) {
  const wrap = document.getElementById(wrapperId);
  if (!wrap || !url) return;

  // on réutilise le même <a> si présent
  let a = wrap.querySelector('a[data-role="download"]');
  const href = addCacheBuster(url, Date.now());

  if (!a) {
    a = document.createElement("a");
    a.setAttribute("data-role", "download");
    a.className = "btn btn-sm btn-outline-primary ms-2";
    wrap.prepend(a); // place le bouton en tête du wrapper (ou choisis appendChild)
  }
  a.href = href;
  a.textContent = label || "Télécharger";
  a.target = "_blank";
  a.rel = "noopener";
}

// CSV parser minimal
function parseCSV(text) {
  const rows = [];
  let row = [],
    field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else {
      if (c === '"') inQuotes = true;
      else if (c === ",") {
        row.push(field);
        field = "";
      } else if (c === "\n") {
        row.push(field);
        rows.push(row);
        row = [];
        field = "";
      } else if (c === "\r") {
        /* ignore */
      } else {
        field += c;
      }
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.map((r) => r.map((x) => x.trim()));
}

// ======== Clusters =========

// ======== Clusters =========
async function submitClusters() {
  const runs_dir = document.getElementById("runs_dir").value.trim() || "runs";
  const out_dir =
    document.getElementById("reports_dir").value.trim() || "reports";
  const k =
    parseInt(document.getElementById("clusters_k").value.trim(), 10) || 2;
  const metric = document.getElementById("clusters_metric").value;
  const T =
    parseInt(
      (document.getElementById("clusters_T").value || "100").trim(),
      10
    ) || 100;

  // nouveaux paramètres
  const sampling = document.getElementById("clusters_sampling").value; // 'linear' | 'log'
  const alphaRaw = document.getElementById("clusters_ratio").value;
  const ratio = alphaRaw === "" ? 3.0 : Number(alphaRaw);

  const body = { runs_dir, out_dir, k, metric, T, sampling, ratio };

  try {
    const r = await fetch("/clusters", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await r.json();

    const container = document.getElementById("out");
    container.innerHTML = ""; // reset avant affichage
    container.textContent = JSON.stringify(j, null, 2);

    if (!j.ok) return;

    const result = document.getElementById("clustersResult");
    result.innerHTML = ""; // reset avant affichage

    const ts = Date.now();

    // Clés robustes (compat v1 et v2)
    const distancesCsvUrl = j.distances_csv_url || j.distances_mst_csv_url;
    const clustersCsvUrl = j.clusters_csv_url || j.clusters_mst_csv_url;
    const mstPngUrl = j.mst_png_url;
    const kmeansPngUrl = j.kmeans_png_url;
    const kmeansCsvUrl = j.clusters_kmeans_csv_url;

    // --- Carte MST --- archived
    // if (mstPngUrl) {
    //   const mstCard = document.createElement("div");
    //   mstCard.className = "card my-3";
    //   mstCard.innerHTML = `
    //     <div class="card-header">Clustering MST</div>
    //     <div class="card-body">
    //       <img src="${mstPngUrl}?t=${ts}" class="img-fluid mb-2" alt="MST">
    //       <div class="small">
    //         ${
    //           distancesCsvUrl
    //             ? `<a href="${distancesCsvUrl}?t=${ts}" target="_blank" rel="noopener">Télécharger distances.csv</a> | `
    //             : ""
    //         }
    //         ${
    //           clustersCsvUrl
    //             ? `<a href="${clustersCsvUrl}?t=${ts}" target="_blank" rel="noopener">Télécharger clusters_mst.csv</a>`
    //             : ""
    //         }
    //       </div>
    //     </div>`;
    //   result.appendChild(mstCard);
    // }

    // --- Carte KMeans ---
    if (kmeansPngUrl) {
      const kmCard = document.createElement("div");
      kmCard.className = "card my-3";
      kmCard.innerHTML = `
        <div class="card-header">Clustering KMeans</div>
        <div class="card-body">
          <img src="${kmeansPngUrl}?t=${ts}" class="img-fluid mb-2" alt="KMeans">
          <div class="small">
            ${
              kmeansCsvUrl
                ? `<a href="${kmeansCsvUrl}?t=${ts}" target="_blank" rel="noopener">Télécharger clusters_kmeans.csv</a>`
                : ""
            }
          </div>
        </div>`;
      result.appendChild(kmCard);
    }

    // --- Tableau (résumé clusters MST si dispo) ---
    const wrap = document.getElementById("clustersTableWrap");
    const tableId = "clustersTable";
    if (clustersCsvUrl && typeof renderCSVTable === "function") {
      await renderCSVTable(clustersCsvUrl, "clustersTableWrap", tableId);
    } else {
      // si pas de CSV ou pas de helper, on masque le wrapper
      wrap.style.display = "none";
      document.getElementById(tableId).innerHTML = "";
    }
  } catch (e) {
    showMessage("Erreur réseau: " + e, "danger");
  }
}

// init
addSolver();
