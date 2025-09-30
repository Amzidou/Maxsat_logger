# maxsat-runner

Orchestrateur MaxSAT **anytime** :
- **CLI** pour lancer des campagnes (1..N solveurs, séquentiel),
- **API FastAPI** + **UI web Bootstrap** (sans terminal),
- Capture en **temps réel** des lignes `o <cost>` + horodatage,
- **Logs par run** (événements + méta) dans `runs/logs/`, puis **agrégation** en CSV globaux,
- Exports **CSV** + **graphiques** (trajectoires, scores relatifs, leaderboards, boxplot TTB),
- **Réplicas par solveur** : moyenne ± écart-type des coûts finaux par instance, **un PNG par solveur**,
- **Clustering d’instances** (MST/k-means-like) avec plusieurs métriques (Spearman, Pearson, Cosine, Euclidean/L2, Manhattan/L1, DTW).

---

## Sommaire
- [Installation](#installation)
- [Organisation des données](#organisation-des-données)
- [Utilisation CLI](#utilisation-cli)
  - [Campagnes](#campagnes)
  - [Statistiques](#statistiques)
  - [Clustering](#clustering)
- [Utilisation API & UI Web](#utilisation-api--ui-web)
- [Schéma des sorties](#schéma-des-sorties)
- [Dépannage](#dépannage)
- [Tests](#tests)
- [Roadmap](#roadmap)
- [Licence](#licence)

---

## Installation

> Requis : Python ≥ 3.10 (Linux/WSL/Mac recommandé)

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .\.venv\Scripts\Activate.ps1  # Windows PowerShell

pip install -U pip
pip install -e ".[dev]"
```

### Dépendances principales

* `pandas`, `numpy`, `matplotlib` — traitement & visualisation
* `typer`, `uvicorn`, `fastapi` — CLI et API
* `networkx` — construction de graphes et MST (clustering)
* `scipy` — statistiques & distances (`spearmanr`, `pearsonr`, `euclidean`, etc.)
* `fastdtw` — pour la métrique `dtw` (Dynamic Time Warping, optionnelle)

Installation rapide des extras clustering :

```bash
pip install networkx scipy fastdtw
```

## Vérification installation

```bash
# Vérifier pandas / matplotlib
python -c "import pandas, matplotlib; print('pandas', pandas.__version__, '| matplotlib', matplotlib.__version__)"

# Vérifier le binaire CLI
maxsat-runner --help
```
---

## Organisation des données

Par défaut, tout est confiné sous `./data/` :

```
data/
├─ instances/                                   # vos .wcnf d’entrée (ex: data/instances/demo/*.wcnf)
├─ runs/                                        # sorties agrégées + logs bruts
│  ├─ trajectories.csv                          # reconstruit par les stats (voir ci-dessous)
│  ├─ summary.csv                               # reconstruit par les stats (voir ci-dessous)
│  └─ logs/<alias>/<basename>/                  # source de vérité par run (events + meta)
│     ├─ <alias>_<basename>_<run_id>.csv
│     └─ <alias>_<basename>_<run_id>_meta.csv
└─ reports/                                     # graphiques/CSV générés par les stats
```

> **Important** : les **statistiques** reconstruisent à chaque appel des CSV globaux
`trajectories.csv` et `summary.csv` **à partir des derniers runs** pour chaque couple
(**solver × instance**) présent sous `runs/logs/`.

---

## Utilisation CLI

### Campagnes

Lancer une campagne (un ou **plusieurs** solveurs, **séquentiel**):

```bash
maxsat-runner run   --solver "solverA=/chemin/solverA {inst}"   --solver "solverB=/chemin/solverB --old-format {inst}"   --instances data/instances/demo   --pattern .wcnf   --out data/runs   --tag demo_cli   --timeout-sec 30
```

**Règles & options :**
- Chaque `--solver` **doit contenir `{inst}`** (remplacé par le chemin absolu de l’instance).
- `--pattern` filtre par extension (ex : `.wcnf`). Laissez vide pour prendre tous les fichiers.
- `--timeout-sec` (optionnel) : arrêt au dépassement, avec `exit_code = 124`.
- Sorties :
  - **Logs par run** dans `data/runs/logs/` (événements et méta),
  - **Par instance** : `data/runs/<tag>/<basename>.csv` (pratique pour inspection rapide).

**Working directory spécifique par solveur** (si un binaire dépend de `cwd`) :
```bash
--solver "[cwd=/chemin/vers/bin] ./solver_exec {inst}"
```

### Générer des statistiques

En plus des CSV globaux (`trajectories.csv`, `summary.csv`), tu peux générer des rapports et graphiques directement en CLI.

```bash
maxsat-runner stats \
  --runs data/runs \
  --out data/reports \
  --by solver_alias
````

**Options principales :**

* `--runs` : dossier contenant `trajectories.csv` et `summary.csv` (par défaut `data/runs`)
* `--out`  : dossier de sortie pour les rapports/PNGs (par défaut `data/reports`)
* `--by`   : clé d’agrégation (`solver_alias`, `solver_cmd`, `solver_tag`)
* `--instance` : (optionnel) nom d’une instance (sans `.wcnf`) pour générer une trajectoire spécifique
* `--t-min` : borne inférieure de temps (sec) pour les analyses
* `--t-max` : borne supérieure de temps (sec) pour les analyses
* `--t-at`  : snapshot à `t_at` (sec), utilisé pour compter les gagnants dans le **leaderboard relatif**

**Exemples :**

```bash
# Stats globales
maxsat-runner stats --runs data/runs --out data/reports

# Limiter l’analyse à la fenêtre [0s, 10s]
maxsat-runner stats --runs data/runs --out data/reports --t-min 0 --t-max 10

# Snapshot à 5s
maxsat-runner stats --runs data/runs --out data/reports --t-at 5.0

# Statistiques pour une instance précise
maxsat-runner stats --runs data/runs --out data/reports --instance tiny2
```

Rapports produits (principaux) :
- `reports/leaderboard.csv`, `reports/plot_leaderboard_wins.png`
- `reports/plot_time_to_best_box.png`
- `reports/instances/plot_<instance>.png` (coût(t) par instance)
- `reports/instances_scores/scores_<instance>.png` (score relatif(t) par instance)
- `reports/average_scores_over_time.csv`, `reports/average_scores_over_time.png`
- `reports/replicas_by_solver.csv` (solver, instance, n_runs, mean_final_cost, std_final_cost)
- `reports/replicas_by_solver/replicas_<solver>.png` (**un PNG par solveur**, barres = instances, erreurs = ± écart-type)

---

### Clustering

```bash
maxsat-runner clusters \
  --runs data/runs \
  --out data/reports \
  --by solver_alias \
  --metric spearman \
  --k 3 \
  --t-min 0 --t-max 10
```

**Métriques disponibles :**

* `spearman` (corrélation de rang)  
  - Compare les **formes relatives** des courbes (classement des points).  
  - Ignore l’échelle absolue : deux courbes qui montent et descendent de la même façon auront une forte corrélation, même si leurs valeurs exactes diffèrent.  
  - Pertinent si on veut capturer la **tendance** d’amélioration entre instances.

* `pearson` (corrélation linéaire)  
  - Mesure la **corrélation linéaire** point à point.  
  - Sensible aux décalages et aux échelles (si une courbe est 2× plus haute que l’autre, la corrélation chute).  
  - Pertinent si on veut comparer les courbes en valeur absolue, pas seulement en tendance.

* `cosine` (similarité cosinus)  
  - Compare l’**orientation des vecteurs** (indépendant de la norme).  
  - Deux courbes ayant des formes similaires mais des amplitudes différentes auront une distance faible.  
  - Utile pour détecter des profils similaires indépendamment de la magnitude.

* `l2` ou `euclidean` (distance euclidienne)  
  - Différence point à point, mise en carré puis sommée.  
  - Sensible aux grands écarts locaux.  
  - Pertinent si l’on veut mesurer la **distance globale** brute entre courbes.

* `manhattan` ou `l1` (distance de Manhattan)  
  - Différence point à point, sommée en valeur absolue.  
  - Moins sensible aux gros écarts ponctuels que l’euclidienne.  
  - Pertinent pour des comparaisons plus **robustes aux outliers**.

* `dtw` (Dynamic Time Warping, nécessite `fastdtw`)  
  - Aligne dynamiquement les séries temporelles avant de calculer la distance.  
  - Permet de comparer deux courbes qui ont la **même forme mais décalée dans le temps**.  
  - Pertinent si les solveurs atteignent leurs améliorations au même rythme relatif, mais pas aux mêmes timestamps.


**Paramètre supplémentaire :**

* `--T` (défaut 100) : nombre de points pour la discrétisation des courbes score(t).

  * `T` = **nombre de points d’échantillonnage sur la durée totale du run**
  * Plus `T` est grand → meilleure résolution temporelle, mais aussi **plus de bruit** (et calculs plus lourds).
  * Plus `T` est petit → les courbes sont compressées, tu perds de la finesse (risque de lisser trop les comportements rapides).
  
  * Supposons un run qui dure 10 secondes et produit 300 événements. 
    * Si `T = 50` → tu prends un point tous les 0.2s → courbe lissée.
    * Si `T = 200` → tu prends un point tous les 0.05s → courbe détaillée.

  *  Bonnes pratiques pour choisir `T`.

      * **Petits problèmes / runs très rapides** → `T = 50` ou `100` suffit.
      * **Gros benchmarks / runs longs** → tu peux monter à `T = 200` ou `300`.
      * **Clustering rapide** (exploration initiale) → `T = 50`.
      * **Analyse fine / publication scientifique** → `T = 200`+ pour capturer les détails.



Sorties :

* `distances.csv` (matrice des distances),
* `clusters_<k>.csv` (affectation instance → cluster),
* `mst.png` (visualisation du graphe minimal, coloré par cluster),
* résumé JSON dans la sortie CLI / API.

## Utilisation API & UI Web

### Démarrer le serveur

```bash
maxsat-runner serve --host 127.0.0.1 --port 8000
```

- Ouvrez **http://127.0.0.1:8000** → redirection vers **`/ui/`** (UI Bootstrap).
- UI : **liste dynamique** de solveurs (“Ajouter un solver”), validation `{inst}` en direct.

### API (JSON)

**POST `/run`** — soumettre une campagne
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

**GET `/status/{job_id}`** — suivre l’exécution (`status: "queued" | "running" | "done" | "error"`).

**POST `/stats`** — générer les statistiques (reconstruit `trajectories.csv`/`summary.csv` depuis `runs/logs/`)
```json
{
  "runs_dir": "runs",
  "out_dir": "reports",
  "by": "solver_alias",
  "instance": "tiny2",
  "t_min": 0.0,
  "t_max": 5.0,
  "t_at": 2.0
}
```

### Endpoints principaux

* `POST /run` → lancer une campagne
* `GET  /status/{job_id}` → état
* `POST /stats` → statistiques
* `POST /clusters` → clustering (params: `runs_dir`, `out_dir`, `by`, `metric`, `k`, `t_min`, `t_max`, `T`)

UI Web (`/ui/`) :

* Formulaire solveurs, exécution de campagnes,
* Statistiques avec filtres,
* Clustering (choix k, métrique, paramètre T),
* Affichage direct des PNG et tables CSV.

L’API renvoie les **chemins locaux** et des **URL publiques** sous `/data/...` pour consulter PNG/CSV.

**FS sandbox** (facultatif) :
- `POST /fs/mkdir` : `{"path":"instances/demo"}`
- `POST /fs/upload` (multipart) : `dir=instances/demo`, `files=@x.wcnf` …
- `GET  /fs/ls?path=instances/demo`
- `GET  /fs/root` → renvoie la racine `data/`

---

## Schéma des sorties

### Logs par run (source de vérité)

`data/runs/logs/<alias>/<basename>/<alias>_<basename>_<run_id>.csv` (événements — **aucun trailer/commentaire**)
```
solver_tag,solver_alias,solver_cmd,instance,run_id,event_idx,elapsed_sec,cost
```

`data/runs/logs/<alias>/<basename>/<alias>_<basename>_<run_id>_meta.csv` (méta du run)
```
solver_tag,solver_alias,solver_cmd,instance,run_id,optimum_found,exit_code
```

### CSV globaux (reconstruits par les stats, dernier run **par solver×instance**)

`data/runs/trajectories.csv` :
```
solver_tag,solver_alias,solver_cmd,instance,run_id,event_idx,elapsed_sec,cost,basename
```

`data/runs/summary.csv` :
```
solver_tag,solver_alias,solver_cmd,instance,run_id,final_cost,time_to_best_sec,optimum_found,exit_code
```

---

## Dépannage

- **Rien ne s’exécute côté UI (multi-solveurs)** : assurez-vous d’avoir **une ligne par solveur** et que chaque commande contient `{inst}`.
- **`exit_code = 127`** : binaire introuvable → utilisez des **chemins absolus** ou fixez le `cwd` via `[cwd=…]`.
- **`exit_code = 126`** : non exécutable → `chmod +x`.
- **Aucun `o <cost>`** : instance trop facile / format non supporté / option solveur manquante (`--old-format`). 
- **Timeout** : augmentez `--timeout-sec` ou choisissez des instances plus lourdes.

---

## Tests

```bash
pytest -q
```