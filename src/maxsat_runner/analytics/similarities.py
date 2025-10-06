# similarities.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import networkx as nx
from fastdtw import fastdtw
from scipy.spatial.distance import cosine as cosine_dist, euclidean, cityblock
from scipy.stats import spearmanr, pearsonr
from sklearn.cluster import KMeans

from .segments import compute_relative_scores_timewindow_for_instance


def _make_resample_points(
    t0: float,
    t1: float,
    T: int,
    sampling: str = "linear",   # "linear" | "log" | "geom"
    ratio: float = 1.10,        # >1 ⇒ densifie le début (front-loaded)
) -> np.ndarray:
    """
    Crée les T abscisses de ré-échantillonnage dans [t0, t1].

    sampling
      - "linear" : u_k = k/(T-1)
      - "log"    : continu, u(r) = (ratio**r - 1) / (ratio - 1), r ∈ [0,1]
                   (ratio>1 ⇒ croissance lente au début ⇒ plus de points tôt)
      - "geom"   : discret, u_k = (ratio**k - 1) / (ratio**(T-1) - 1), k=0..T-1
                   (ratio>1 ⇒ plus dense au début)

    ratio
      - ratio > 1 : plus il est grand, plus on pondère le début
      - ratio ≤ 1 : fallback linéaire (sécurité)
    """
    if T <= 0 or not np.isfinite(t0) or not np.isfinite(t1) or t1 <= t0:
        return np.array([], dtype=float)

    sampling = (sampling or "linear").lower()

    if sampling == "log":
        if not np.isfinite(ratio) or ratio <= 1.0:
            u = np.linspace(0.0, 1.0, T)
        else:
            r = np.linspace(0.0, 1.0, T)
            # Courbe C^1, bornée, 0→1 ; front-loaded quand ratio>1
            u = (np.power(ratio, r) - 1.0) / (ratio - 1.0)

    elif sampling == "geom":
        if not np.isfinite(ratio) or ratio <= 1.0 or T == 1:
            u = np.linspace(0.0, 1.0, T)
        else:
            ks = np.arange(T, dtype=float)
            ln_r = np.log(ratio)
            num = np.expm1(ln_r * ks)           # ratio**k - 1
            den = np.expm1(ln_r * (T - 1.0))    # ratio**(T-1) - 1
            u = num / den if np.isfinite(den) and den != 0.0 else np.linspace(0.0, 1.0, T)

    else:  # "linear"
        u = np.linspace(0.0, 1.0, T)

    return t0 + (t1 - t0) * u


def _resample_score_curve(
    df: pd.DataFrame,
    T: int = 100,
    sampling: str = "linear",   # "linear" | "log" | "geom"
    ratio: float = 1.10,
) -> np.ndarray:
    """
    Transforme les segments en une courbe score(t) normalisée sur [0,1] avec T points.
    Retourne un vecteur numpy de longueur T.
    """
    if df.empty:
        return np.zeros(T, dtype=float)

    t0, t1 = float(df["t_start"].min()), float(df["t_end"].max())
    if not np.isfinite(t0) or not np.isfinite(t1) or t1 <= t0:
        return np.zeros(T, dtype=float)

    xs = _make_resample_points(t0, t1, T, sampling=sampling, ratio=ratio)
    if xs.size == 0:
        return np.zeros(T, dtype=float)

    ys = np.zeros(xs.shape[0], dtype=float)
    segs = df.sort_values(["t_start", "t_end"]).reset_index(drop=True)

    j = 0
    n = len(segs)
    for i, x in enumerate(xs):
        while j < n and float(segs.loc[j, "t_end"]) <= x:
            j += 1
        if j < n:
            ys[i] = float(segs.loc[j, "score"])
        else:
            ys[i] = ys[i - 1] if i > 0 else 0.0
    return ys


def compute_instance_curves(
    df_traj: pd.DataFrame,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    T: int = 100,
    sampling: str = "linear",   # "linear" | "log" | "geom"
    ratio: float = 1.10,
) -> Dict[str, np.ndarray]:
    """
    Calcule une courbe score(t) moyenne par instance (moyenne sur solveurs).
    Retourne {instance_basename: vecteur numpy}.
    """
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    instances = sorted(df["basename"].unique())
    curves: Dict[str, np.ndarray] = {}

    for inst in instances:
        seg = compute_relative_scores_timewindow_for_instance(
            df_traj, inst, by=by, t_min=t_min, t_max=t_max
        )
        if seg.empty:
            curves[inst] = np.zeros(T, dtype=float)
            continue

        parts = []
        for _, g in seg.groupby("solver"):
            parts.append(_resample_score_curve(g, T=T, sampling=sampling, ratio=ratio))

        if parts:
            curves[inst] = np.mean(parts, axis=0)
        else:
            curves[inst] = np.zeros(T, dtype=float)

    return curves


def compute_distance_matrix(curves: Dict[str, np.ndarray], metric: str = "spearman") -> pd.DataFrame:
    """
    Construit une matrice de distances entre instances.
    metric ∈ {spearman, pearson, cosine, l2, manhattan, dtw}
    """
    insts = list(curves.keys())
    rows = []
    for i in range(len(insts)):
        for j in range(i + 1, len(insts)):
            a, b = curves[insts[i]], curves[insts[j]]
            a = np.asarray(a).ravel()
            b = np.asarray(b).ravel()

            if metric == "spearman":
                rho, _ = spearmanr(a, b)
                d = 1.0 - (rho if np.isfinite(rho) else 0.0)
            elif metric == "pearson":
                r, _ = pearsonr(a, b)
                d = 1.0 - (r if np.isfinite(r) else 0.0)
            elif metric == "cosine":
                d = float(cosine_dist(a, b))
            elif metric in ("l2", "euclidean"):
                d = float(euclidean(a, b))
            elif metric in ("manhattan", "l1"):
                d = float(cityblock(a, b))
            elif metric == "dtw":
                d, _ = fastdtw(a, b)
            else:
                raise ValueError(f"Métrique inconnue: {metric}")

            rows.append({"instance_i": insts[i], "instance_j": insts[j], "distance": d})
    return pd.DataFrame(rows)


def cluster_instances(dist_df: pd.DataFrame, k: int) -> pd.DataFrame:
    """
    Construit le MST puis coupe en k clusters.
    """
    G = nx.Graph()
    for _, row in dist_df.iterrows():
        G.add_edge(row["instance_i"], row["instance_j"], weight=row["distance"])
    mst = nx.minimum_spanning_tree(G, weight="weight")

    # Supprimer les (k-1) arêtes les plus lourdes
    edges_sorted = sorted(mst.edges(data=True), key=lambda e: e[2]["weight"], reverse=True)
    for idx in range(min(k - 1, len(edges_sorted))):
        u, v, _ = edges_sorted[idx]
        if mst.has_edge(u, v):
            mst.remove_edge(u, v)

    clusters: Dict[str, int] = {}
    for cid, comp in enumerate(nx.connected_components(mst), start=1):
        for inst in comp:
            clusters[inst] = cid

    rows = [{"instance": inst, "cluster_id": clusters[inst]} for inst in sorted(clusters.keys())]
    return pd.DataFrame(rows)


def plot_mst(dist_df: pd.DataFrame, clusters_df: pd.DataFrame, out_png: Path) -> None:
    """
    Sauvegarde une visualisation du MST colorée par cluster.
    - Labels plus petits
    - Plus de marge
    - Arêtes uniquement intra-cluster
    """
    G = nx.Graph()
    for _, row in dist_df.iterrows():
        G.add_edge(row["instance_i"], row["instance_j"], weight=row["distance"])

    mst = nx.minimum_spanning_tree(G, weight="weight")

    cluster_map = dict(zip(clusters_df["instance"], clusters_df["cluster_id"]))
    colors = [cluster_map.get(node, 0) for node in mst.nodes]

    # Positionnement stable
    pos = nx.spring_layout(mst, seed=42, k=0.6)

    plt.figure(figsize=(9, 7), dpi=150)

    # Arêtes intra-cluster uniquement
    intra_edges = [(u, v) for u, v in mst.edges() if cluster_map.get(u) == cluster_map.get(v)]

    nx.draw_networkx_nodes(
        mst, pos,
        node_color=colors,
        node_size=800,
        cmap=plt.cm.Set2,
        alpha=0.9,
    )
    nx.draw_networkx_edges(
        mst, pos,
        edgelist=intra_edges,
        edge_color="gray",
        width=1.5,
    )
    nx.draw_networkx_labels(
        mst, pos,
        font_size=8,
        font_family="sans-serif"
    )

    plt.title("MST clustering des instances", fontsize=12)
    plt.margins(0.2)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, bbox_inches="tight")
    plt.close()


def plot_kmeans(curves: Dict[str, np.ndarray], k: int, out_png: Path) -> None:
    """
    Applique KMeans sur les courbes (vecteurs T-dim).
    Génère un scatter 2D via PCA pour visualiser les clusters.
    """
    from sklearn.decomposition import PCA

    insts = list(curves.keys())
    X = np.vstack([curves[i] for i in insts])  # (n_instances × T)

    km = KMeans(n_clusters=k, random_state=42, n_init="auto")
    labels = km.fit_predict(X)

    X2d = PCA(n_components=2, random_state=42).fit_transform(X)

    plt.figure(figsize=(8, 6), dpi=150)
    plt.scatter(
        X2d[:, 0], X2d[:, 1],
        c=labels, cmap=plt.cm.Set2, s=120, alpha=0.85, edgecolors="k"
    )

    for i, inst in enumerate(insts):
        plt.text(X2d[i, 0] + 0.02, X2d[i, 1] + 0.02, inst, fontsize=8)

    plt.title(f"KMeans clustering (k={k}) sur courbes resamplées")
    plt.xlabel("PCA dim 1"); plt.ylabel("PCA dim 2"); plt.margins(0.1)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, bbox_inches="tight")
    plt.close()

    return {inst: int(lbl) for inst, lbl in zip(insts, labels)}


def generate_clusters(
    df_traj: pd.DataFrame,
    out_dir: Path,
    by: str = "solver_alias",
    metric: str = "spearman",
    k: int = 2,
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    T: int = 100,
    sampling: str = "linear",
    ratio: float = 1.10,
) -> Dict[str, str]:
    """
    Pipeline complet : courbes -> distances -> clusters -> PNG
    sampling: "linear" | "log"
    alpha: intensité du front-loading quand sampling="log" (alpha>0)
    """
    curves = compute_instance_curves(
        df_traj, by=by, t_min=t_min, t_max=t_max, T=T, sampling=sampling, ratio=ratio)
    dist_df = compute_distance_matrix(curves, metric=metric)
    clusters_df = cluster_instances(dist_df, k=k)

    out_dir.mkdir(parents=True, exist_ok=True)
    dist_csv     = out_dir / "distances.csv"
    clusters_csv = out_dir / f"clusters_{k}.csv"
    mst_png      = out_dir / "mst.png"
    kmeans_png   = out_dir / "kmeans.png"
    kmeans_csv   = out_dir / f"clusters_kmeans_{k}.csv"

    dist_df.to_csv(dist_csv, index=False)
    clusters_df.to_csv(clusters_csv, index=False)

    plot_mst(dist_df, clusters_df, mst_png)
    kmeans_assignments = plot_kmeans(curves, k, kmeans_png)

    pd.DataFrame(
        [{"instance": inst, "cluster_id": cid} for inst, cid in kmeans_assignments.items()]
    ).to_csv(kmeans_csv, index=False)

    return {
        "distances_csv": str(dist_csv),
        "clusters_csv": str(clusters_csv),
        "mst_png": str(mst_png),
        "kmeans_png": str(kmeans_png),
        "kmeans_assignments": kmeans_assignments,
        "clusters_kmeans_csv": str(kmeans_csv),
    }
