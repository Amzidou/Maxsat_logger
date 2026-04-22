# maxsat-runner

Orchestrateur MaxSAT **anytime** pour lancer des campagnes, reconstruire des trajectoires de coût, produire des rapports analytiques et regrouper des instances selon la similarité de leurs courbes.

## Fonctionnalités

- **CLI** pour lancer des campagnes sur un ou plusieurs solveurs
- **Run unitaire** (`run-one`) adapté aux scripts cluster / Slurm
- Capture en **temps réel** des améliorations de coût
- **Logs par run** dans `runs/logs/`, puis **agrégation** en CSV globaux
- Exports **CSV** + **graphiques** : leaderboards, trajectoires, scores relatifs, statistiques temporelles
- **Réplicas par solveur**
- **Clustering d’instances** avec plusieurs métriques (`spearman`, `pearson`, `cosine`, `l2`, `manhattan`, `dtw`)
- Démarrage d’un **serveur web / API** via `serve`

---

## Sommaire

- [Installation](#installation)
- [Vérification de l’installation](#vérification-de-linstallation)
- [Organisation des données](#organisation-des-données)
- [Utilisation CLI](#utilisation-cli)
  - [Lancer une campagne](#lancer-une-campagne)
  - [Exécuter un seul run](#exécuter-un-seul-run)
  - [Générer les statistiques](#générer-les-statistiques)
  - [Clustering des instances](#clustering-des-instances)
  - [Lancer le serveur web](#lancer-le-serveur-web)
- [Sorties produites](#sorties-produites)
- [Dépannage](#dépannage)

---

## Installation

> Requis : Python 3.10 ou plus

```bash
python -m venv .venv
source .venv/bin/activate   # Linux / macOS
# .\.venv\Scripts\Activate.ps1   # Windows PowerShell

pip install -U pip
pip install -e ".[dev]"
````

### Dépendances utiles

```bash
pip install scikit-learn
pip install networkx scipy fastdtw
```

---

## Vérification de l’installation

```bash
python -c "import pandas, matplotlib; print('pandas', pandas.__version__, '| matplotlib', matplotlib.__version__)"
maxsat-runner --help
```
---

## Organisation des données

Par défaut, les campagnes produisent des logs bruts puis des fichiers agrégés.

```text
data/
├─ instances/                                   # fichiers .wcnf d’entrée
├─ runs/                                        # logs bruts + CSV agrégés
│  ├─ trajectories.csv                          # trajectoires agrégées
│  ├─ summary.csv                               # résumé agrégé
│  └─ logs/<alias>/<basename>/                  # source de vérité par run
│     ├─ <alias>_<basename>_<run_id>.csv
│     └─ <alias>_<basename>_<run_id>_meta.csv
└─ reports/                                     # graphiques et CSV générés
```

---

## Utilisation CLI

### Lancer une campagne

La commande `run` lance une campagne sur un ou plusieurs solveurs. Chaque option `--solver` doit contenir le placeholder `{inst}`, remplacé automatiquement par le chemin de l’instance.

```bash
maxsat-runner run \
  --solver "solverA=/chemin/solverA {inst}" \
  --solver "solverB=/chemin/solverB --old-format {inst}" \
  --instances data/instances/demo \
  --pattern .wcnf \
  --out data/runs \
  --timeout-sec 30
```

#### Options principales

* `--solver` : commande solveur, répétable, éventuellement sous la forme `alias=commande`
* `--instances` : dossier d’instances
* `--pattern` : extension à filtrer, par exemple `.wcnf`
* `--out` : dossier de sortie
* `--timeout-sec` : timeout par run

#### Répertoire de travail spécifique par solveur

Si un binaire dépend d’un répertoire de travail particulier :

```bash
maxsat-runner run \
  --solver "[cwd=/chemin/vers/bin] ./solver_exec {inst}" \
  --instances data/instances/demo \
  --pattern .wcnf \
  --out data/runs \
  --timeout-sec 30
```

---

### Exécuter un seul run

La commande `run-one` exécute un unique couple solveur–instance. Elle est utile pour le cluster ou pour un script Slurm.

```bash
maxsat-runner run-one \
  --solver-alias solverA \
  --cmd "/chemin/solverA {inst}" \
  --instance data/instances/demo/exemple.wcnf \
  --out data/runs \
  --timeout-sec 30
```

#### Options principales

* `--solver-alias` : alias du solveur
* `--cmd` : commande solveur, doit contenir `{inst}`
* `--instance` : chemin de l’instance
* `--out` : dossier de sortie des logs
* `--timeout-sec` : timeout du run

---

### Générer les statistiques

La commande `stats` lit les runs, reconstruit les fichiers agrégés et génère des rapports CSV / PNG.

```bash
maxsat-runner stats \
  --runs data/runs \
  --out data/reports \
  --by solver_alias
```

#### Options principales

* `--runs` : dossier contenant `trajectories.csv` / `summary.csv`
* `--out` : dossier de sortie des rapports
* `--by` : clé d’agrégation (`solver_alias`, `solver_cmd`, `solver_tag`)
* `--instance` : basename d’une instance pour tracer une trajectoire spécifique
* `--t-min` : borne inférieure de temps
* `--t-max` : borne supérieure de temps
* `--t-at` : snapshot à un instant donné
* `--log-time` : axe du temps en échelle logarithmique
* `--per-instance / --no-per-instance` : activer ou non les trajectoires coût/temps par instance
* `--per-instance-scores / --no-per-instance-scores` : activer ou non les scores relatifs par instance
* `--do-leaderboard / --no-leaderboard`
* `--do-relative-leaderboard / --no-relative-leaderboard`
* `--do-final-summary / --no-final-summary`
* `--do-replicas-by-solver`
* `--min-n-instances` : nombre minimal d’instances pour conserver un point temporel

#### Exemples

```bash
# Statistiques globales
maxsat-runner stats --runs data/runs --out data/reports

# Fenêtre temporelle [0, 10]
maxsat-runner stats --runs data/runs --out data/reports --t-min 0 --t-max 10

# Snapshot à 5 secondes
maxsat-runner stats --runs data/runs --out data/reports --t-at 5.0

# Une instance particulière
maxsat-runner stats --runs data/runs --out data/reports --instance tiny2

# Sans figures par instance
maxsat-runner stats --runs data/runs --out data/reports --no-per-instance --no-per-instance-scores

# Sans résumé temporel final
maxsat-runner stats --runs data/runs --out data/reports --no-final-summary

# Avec statistiques de réplicas
maxsat-runner stats --runs data/runs --out data/reports --do-replicas-by-solver
```

---

### Clustering des instances

La commande `clusters` regroupe les instances à partir de la similarité de leurs trajectoires.

```bash
maxsat-runner clusters \
  --runs data/runs \
  --out data/reports \
  --by solver_alias \
  --metric spearman \
  --k 3 \
  --t-min 0 --t-max 10 \
  --T 100 \
  --sampling log \
  --ratio 1.25
```

#### Options principales

* `--runs` : dossier contenant `trajectories.csv`
* `--out` : dossier de sortie
* `--by` : clé d’agrégation
* `--metric` : `spearman|pearson|cosine|l2|manhattan|dtw`
* `--k` : nombre de clusters
* `--t-min`, `--t-max` : fenêtre temporelle
* `--T` : nombre de points de discrétisation
* `--sampling` : `linear|log|geom`
* `--ratio` : intensité du front-loading quand `sampling=log`

#### Métriques disponibles

* `spearman`
* `pearson`
* `cosine`
* `l2` / `euclidean`
* `manhattan` / `l1`
* `dtw`

---

### Lancer le serveur web

```bash
maxsat-runner serve --host 127.0.0.1 --port 8000
```

Ouvrir ensuite dans un navigateur :

```text
http://127.0.0.1:8000
```

---

## Sorties produites

### Logs par run

```text
data/runs/logs/<alias>/<basename>/<alias>_<basename>_<run_id>.csv
data/runs/logs/<alias>/<basename>/<alias>_<basename>_<run_id>_meta.csv
```

### Fichiers agrégés

```text
data/runs/trajectories.csv
data/runs/summary.csv
```

### Rapports de `stats`

```text
reports/leaderboard.csv
reports/plot_leaderboard_wins.png
reports/plot_time_to_best_box.png
reports/instances/plot_<instance>.png
reports/instances_scores/scores_<instance>.png
reports/average_scores_over_time.csv
reports/average_scores_over_time.png
reports/auc_scores.csv
reports/auc_scores_table.png
reports/replicas_by_solver.csv
reports/replicas_by_solver/replicas_<solver>.png
```

## Dépannage

* **Le solveur ne démarre pas** : vérifier que la commande contient bien `{inst}`.
* **`exit_code = 127`** : binaire introuvable, utiliser un chemin absolu ou un `cwd`.
* **`exit_code = 126`** : binaire non exécutable, vérifier `chmod +x`.
* **Aucune amélioration capturée** : vérifier que le solveur écrit bien des lignes compatibles avec le parseur attendu.
* **Timeout fréquent** : augmenter `--timeout-sec` ou réduire la taille des instances.