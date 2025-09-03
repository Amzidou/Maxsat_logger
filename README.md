# Logger maxsat-runner

Orchestrateur MaxSAT : **CLI** pour compétitions et **API + mini-UI** pour usage sans terminal.
Capture en temps réel des lignes `o <cost>`, timestamps, export CSV.

## Installation
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Commandes (setup, tests, exécution)

```bash
# Cloner (ou créer votre repo puis copier l’arborescence ci-dessous)
python -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"   # installe deps + extras dev (pytest)

# Tests
pytest -q

# CLI (mode compétition)
maxsat-runner run \
  --solver "./bin/uwrmaxsat --cpu-lim=300 {inst}" \
  --solver "./bin/maxhs {inst}" \
  --instances ./instances --pattern .wcnf \
  --out ./data/runs --tag competition_300s

# CLI avec timeout et CSV par instance
maxsat-runner run \
  --solver "./bin/uwrmaxsat --cpu-lim=300 {inst}" \
  --instances data/instances/demo \
  --pattern .wcnf \
  --out data/runs \
  --tag demo_anytime \
  --timeout-sec 10

# Serveur API + UI (http://127.0.0.1:8000)
maxsat-runner serve --host 0.0.0.0 --port 8000
```

---

## Arborescence du repo

```
maxsat-runner/
├─ pyproject.toml
├─ README.md
├─ LICENSE
├─ .gitignore
├─ .github/workflows/ci.yml
├─ src/maxsat_runner/
│  ├─ __init__.py
│  ├─ cli.py
│  ├─ api.py
│  ├─ ui_html.py
│  ├─ core/
│  │  ├─ types.py
│  │  ├─ parser.py
│  │  ├─ runner.py
│  │  └─ campaign.py
│  └─ io/
│     └─ csvsink.py
├─ tests/
│  ├─ test_parser.py
│  ├─ test_campaign_fake.py
│  └─ assets/fake_solver.py
└─ Dockerfile
```

## Notes complexité & perf

* Temps par instance: **O(T + L)** (T = durée solveur, L = lignes lues). Overhead Python marginal.
* Mémoire: **O(E)** (E = nb d’événements `o` stockés avant flush CSV).
* I/O: lecture stdout en flux + append CSV → séquentiel cache-friendly.