let solverCount = 0;

function addSolver(value = "") {
  solverCount++;
  const div = document.createElement("div");
  div.className = "input-group mb-2";
  div.id = "solver-" + solverCount;
  div.innerHTML = `
    <input type="text" class="form-control solver-cmd" placeholder="Commande solver avec {inst}" value="${value}">
    <button class="btn btn-outline-danger" type="button" onclick="removeSolver(${solverCount})">Supprimer</button>
  `;
  document.getElementById("solversList").appendChild(div);

  // Ajoute un bloc de feedback Bootstrap si absent
  ensureInvalidFeedback(div);

  // Revalider en live
  const input = div.querySelector(".solver-cmd");
  input.addEventListener("input", validateSolvers);
}

function removeSolver(id) {
  const div = document.getElementById("solver-" + id);
  if (div) div.remove();
  validateSolvers();
}

function ensureInvalidFeedback(container) {
  // Si pas déjà présent, on ajoute un <div class="invalid-feedback"> sous le champ
  if (!container.querySelector(".invalid-feedback")) {
    const fb = document.createElement("div");
    fb.className = "invalid-feedback";
    fb.textContent = "La commande doit contenir le placeholder {inst}.";
    // L'invalid-feedback doit être sœur directe de l'input pour Bootstrap
    container.appendChild(fb);
  }
}

function validateSolvers() {
  // Retourne true si tout est OK, sinon false
  let ok = true;
  const inputs = document.querySelectorAll(".solver-cmd");
  if (inputs.length === 0) ok = false;

  inputs.forEach((el) => {
    const v = el.value.trim();
    const hasInst = v.includes("{inst}");
    // Règles: non vide ET contient {inst}
    const valid = v.length > 0 && hasInst;
    toggleValidity(el, valid, hasInst);
    ok = ok && valid;
  });

  return ok;
}

function toggleValidity(inputEl, valid, hasInst) {
  if (valid) {
    inputEl.classList.remove("is-invalid");
    inputEl.classList.add("is-valid");
    const fb = inputEl.parentElement.querySelector(".invalid-feedback");
    if (fb) fb.textContent = "";
  } else {
    inputEl.classList.remove("is-valid");
    inputEl.classList.add("is-invalid");
    const fb = inputEl.parentElement.querySelector(".invalid-feedback");
    if (fb) {
      fb.textContent = hasInst
        ? "La commande ne doit pas être vide."
        : "La commande doit contenir le placeholder {inst}.";
    }
  }
}

function showMessage(msg, type = "danger") {
  // Affiche un message simple dans la zone #out (JSON/texte)
  const out = document.getElementById("out");
  out.textContent = `[${type.toUpperCase()}] ${msg}`;
}

async function submitJob() {
  // Validation rapide avant envoi
  if (!validateSolvers()) {
    showMessage("Corrige les commandes solver (placeholder {inst} requis).", "danger");
    // Scroll vers le premier invalide
    const firstBad = document.querySelector(".solver-cmd.is-invalid");
    if (firstBad) firstBad.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  const solverElems = document.querySelectorAll(".solver-cmd");
  const solver_cmds = Array.from(solverElems)
    .map(el => el.value.trim())
    .filter(Boolean);

  const body = {
    solver_cmds,
    instances_dir: document.getElementById("instances_dir").value.trim(),
    pattern: document.getElementById("pattern").value.trim(),
    out_dir: document.getElementById("out_dir").value.trim(),
    tag: document.getElementById("tag").value.trim(),
  };
  const timeoutVal = document.getElementById("timeout_sec").value.trim();
  if (timeoutVal) body.timeout_sec = parseInt(timeoutVal, 10);

  try {
    const r = await fetch("/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const j = await r.json();
    document.getElementById("out").textContent = JSON.stringify(j, null, 2);
    if(j.ok){ pollStatus(j.job_id); }
  } catch(e) {
    showMessage("Erreur réseau: " + e, "danger");
  }
}

async function pollStatus(id){
  let done=false;
  while(!done){
    await new Promise(r=>setTimeout(r,1000));
    const r = await fetch("/status/"+encodeURIComponent(id));
    const j = await r.json();
    document.getElementById("out").textContent = JSON.stringify(j, null, 2);
    if(j.status==='done' || j.status==='error'){ done=true; }
  }
}

// init
addSolver();
