# maxsat-runner

Orchestrateur MaxSAT **anytime** :
- **CLI** pour lancer des campagnes (1..N solveurs, séquentiel),
- **API FastAPI** + **UI web Bootstrap** (sans terminal),
- Capture en **temps réel** des lignes `o <cost>` + horodatage,
- Exports **CSV agrégés** et **CSV par instance**.

---

## Sommaire
- [Installation](#installation)
- [Organisation des données](#organisation-des-données)
- [Utilisation CLI](#utilisation-cli)
- [Utilisation API & UI Web](#utilisation-api--ui-web)
- [Schéma des sorties CSV](#schéma-des-sorties-csv)
- [Dépannage](#dépannage)
- [Tests](#tests)
- [Roadmap](#roadmap)
- [Licence](#licence)

---

## Installation

> Requis : Python ≥ 3.10 (Linux/WSL/Mac recommandé)

```bash
python -m venv .venv
# Linux/Mac
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -U pip
pip install -e ".[dev]"

```



## Organisation des données

Par défaut, tout est confiné sous `./data/` :

```
data/
├─ instances/          # vos .wcnf d’entrée
│  └─ demo/            # ex: data/instances/demo/*.wcnf
└─ runs/               # sorties CSV
```

Vous pouvez déposer les fichiers manuellement ou utiliser les endpoints `/fs/*` (voir [API & UI](#utilisation-api--ui-web)).

---

## Utilisation CLI

### Lancer une campagne (un ou **plusieurs** solveurs, **séquentiel**)

```bash
maxsat-runner run \
  --solver "/chemin/solverA {inst}" \
  --solver "/chemin/solverB --old-format {inst}" \
  --instances data/instances/demo \
  --pattern .wcnf \
  --out data/runs \
  --tag demo_cli \
  --timeout-sec 30
```

**Règles & options :**

* Chaque `--solver` **doit contenir `{inst}`** (remplacé par le chemin absolu de l’instance).
* `--pattern` filtre par extension (ex : `.wcnf`). Laissez vide pour prendre tous les fichiers.
* `--timeout-sec` (optionnel) : kill au dépassement, avec `exit_code = 124`.
* **CSV générés**

  * Agrégats : `data/runs/trajectories.csv`, `data/runs/summary.csv`
  * **Par instance** : `data/runs/<tag>/<basename>.csv`

### Working directory spécifique par solveur

Si un binaire dépend de son répertoire courant :

```
--solver "[cwd=/chemin/vers/bin] ./solver_exec {inst}"
```

---

## Utilisation API & UI Web

### Démarrer le serveur

```bash
maxsat-runner serve --host 127.0.0.1 --port 8000
```

* Ouvrez **[http://127.0.0.1:8000](http://127.0.0.1:8000)** → redirection vers **`/ui/`** (UI Bootstrap).
* UI : **liste dynamique** de solveurs (bouton “Ajouter un solver”).
* **Validation immédiate** : chaque commande doit contenir `{inst}` (surlignage rouge sinon).

### API (JSON)

* **POST `/run`** — soumettre une campagne

```json
{
  "solver_cmds": ["<cmd1 {inst}>", "<cmd2 {inst}>"],
  "instances_dir": "instances/demo",
  "pattern": ".wcnf",
  "out_dir": "runs",
  "tag": "web_run",
  "timeout_sec": 30
}
```

* **GET `/status/{job_id}`** — suivre l’exécution (jusqu’à `status: "done"`)

* **FS sandbox** (facultatif) :

  * `POST /fs/mkdir` : `{"path":"instances/demo"}`
  * `POST /fs/upload` (multipart) : `dir=instances/demo`, `files=@x.wcnf` …
  * `GET  /fs/ls?path=instances/demo`
  * `GET  /fs/root` → renvoie la racine `data/`

Exemples `curl` :

```bash
# créer un sous-dossier
curl -s -X POST http://127.0.0.1:8000/fs/mkdir \
  -H "Content-Type: application/json" \
  -d '{"path":"instances/demo"}'

# lister
curl -s "http://127.0.0.1:8000/fs/ls?path=instances/demo"

# lancer un run (multi-solveurs)
curl -s -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "solver_cmds": ["/abs/solverA {inst}", "/abs/solverB --old-format {inst}"],
    "instances_dir": "instances/demo",
    "pattern": ".wcnf",
    "out_dir": "runs",
    "tag": "api_demo",
    "timeout_sec": 30
  }'
```

---

## Schéma des sorties CSV

* `data/runs/trajectories.csv`
  Colonnes : `solver_tag, solver_cmd, instance, event_idx, elapsed_sec, cost`

* `data/runs/summary.csv`
  Colonnes : `solver_tag, solver_cmd, instance, final_cost, time_to_best_sec, optimum_found, exit_code`

* `data/runs/<tag>/<basename>.csv` (par instance)
  Colonnes : `solver_tag, solver_cmd, instance, event_idx, elapsed_sec, cost`


## Dépannage

* **Rien ne s’exécute côté UI (multi-solveurs)** : assurez-vous d’avoir **une ligne par solveur** dans l’UI (la liste dynamique s’en charge) ; `{inst}` est **obligatoire**.
* **`exit_code = 127`** : binaire introuvable → utilisez des **chemins absolus** ou fixez le `cwd` via `[cwd=…]`.
* **`exit_code = 126`** : non exécutable → `chmod +x`.
* **Aucun `o <cost>`** : instance trop facile / format non supporté / option solveur manquante (`--old-format`).
* **Timeout** : augmentez `--timeout-sec` ou choisissez des instances plus lourdes.

---

## Tests

```bash
pytest -q
```

* `test_parser.py` : parsing `o <cost>` / `s OPTIMUM FOUND`.
* `test_campaign_fake.py` : campagne end-to-end (solveur factice).
* `test_timeout.py` : kill au timeout (`exit_code = 124`).

---

## Roadmap

* `--jobs N` (parallélisme contrôlé),
* Export Parquet (pyarrow) pour gros volumes,
* Persistance des jobs (SQLite),
* `solver_tag` auto (nom de binaire) pour comparaison directe dans un même run.