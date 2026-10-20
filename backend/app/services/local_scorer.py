"""
Local job scorer: a small model trained on the user's own AI-scored jobs, so new jobs are scored
instantly on this machine (no AI quota, no slow local LLM).

Features per job: the job's local embedding (nomic-embed-text, already computed for the similarity
pre-filter) reduced to its 8 main directions, its cosine similarity to the résumé, the skill match
(matched / missing / coverage), whether the title fits the résumé's roles, and remote. Model: ridge
regression (numpy), strength picked by 5-fold cross-validation. (Also tried on real data: all 768
dimensions, k-nearest neighbours and a blend — this was the most accurate.)
One model per user, stored at data/models/scorer_<user_id>.json — never shared between users.

train(user_id, session)  -> stats dict (None when there isn't enough data yet)
load(user_id)            -> model dict or None
predict(model, job_emb, matched, missing, title_fit, remote) -> score 0..10
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

MIN_EXAMPLES = 40          # below this the AI keeps scoring, which is how the model gets its examples
MAX_USEFUL_MAE = 1.6       # a model whose typical error is worse than this (score points) isn't used
_LAMBDAS = (0.1, 0.3, 1, 3, 10, 30, 100)
_PCA_DIMS = 8


def _path(user_id: int):
    return DATA_DIR / "models" / f"scorer_{int(user_id)}.json"


def _unit(v) -> np.ndarray:
    """Embeddings saved earlier are unnormalised (length ~17), newer ones have length 1: compare directions only."""
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) + 1e-12)


def _features(job_emb: List[float], model: Dict[str, Any], matched: int, missing: int,
              title_fit: bool, remote: bool) -> np.ndarray:
    j = _unit(job_emb)
    r = _unit(model["resume_emb"])
    cos = float(j @ r / (np.linalg.norm(j) * np.linalg.norm(r) + 1e-9))
    req = matched + missing
    pca = (j - np.asarray(model["emb_mean"])) @ np.asarray(model["components"]).T
    return np.concatenate([pca, [cos, matched, missing, matched / req if req else 0.5,
                                 1.0 if title_fit else 0.0, 1.0 if remote else 0.0]])


def _fit(X: np.ndarray, y: np.ndarray, lam: float):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return w, float(y.mean()), mu, sd


def _oof(X: np.ndarray, y: np.ndarray, lam: float, folds: int = 5) -> np.ndarray:
    """Out-of-fold predictions: each job predicted by a model that never saw it."""
    idx = np.random.default_rng(0).permutation(len(y))
    pred = np.empty(len(y))
    for k in range(folds):
        test = idx[k::folds]
        train = np.setdiff1d(idx, test)
        w, b, mu, sd = _fit(X[train], y[train], lam)
        pred[test] = ((X[test] - mu) / sd) @ w + b
    return pred


def _cv_mae(X: np.ndarray, y: np.ndarray, lam: float, folds: int = 5) -> float:
    return float(np.abs(np.clip(_oof(X, y, lam, folds), 0, 10) - y).mean())


def _calibration(pred: np.ndarray, y: np.ndarray, points: int = 41):
    """Quantile map from the model's (squashed) predictions onto the AI's own score spread; keeps the ranking.

    Plain regression pulls scores to the middle (an AI 9 came out ~6), so an apply threshold of 7 would
    almost never be reached.
    """
    q = np.linspace(0, 1, points)
    return np.quantile(pred, q).tolist(), np.quantile(y, q).tolist()


async def train(user_id: int, session) -> Optional[Dict[str, Any]]:
    """(Re)train this user's scorer from jobs the AI scored. Returns its stats, or None if too few examples."""
    from sqlmodel import col, select

    from app.config import EMBED_MODEL, OLLAMA_URL
    from app.models import Job, Profile, json_decode, json_encode
    from app.services import llm
    from app.services.matcher import selected_skills, skill_match
    from app.services.sources import relevant, resume_titles

    profile = session.exec(select(Profile).where(Profile.user_id == user_id)).first()
    if not profile or not profile.resume_text:
        return None
    resume_emb = await llm.embed(profile.resume_text[:4000], EMBED_MODEL, OLLAMA_URL)
    if not resume_emb:
        return None
    skills, titles = selected_skills(profile), resume_titles(profile)
    jobs = session.exec(select(Job).where(Job.user_id == user_id, Job.status == "scored",
                                          col(Job.score).is_not(None))).all()
    rows, y = [], []
    for job in jobs:
        if (job.score_source or "ai") not in ("ai", "local_llm"):  # learn from an LLM, never from its own guesses
            continue
        emb = json_decode(job.embedding, None) if job.embedding else None
        if not emb and job.description:
            emb = await llm.embed(job.description[:4000], EMBED_MODEL, OLLAMA_URL)
            if emb:
                job.embedding = json_encode(emb)
                session.add(job)
        if not emb:
            continue
        matched, missing = skill_match(skills, job.title + "\n" + (job.description or ""))
        title_fit = relevant({"title": job.title, "description": ""}, titles, skills)
        rows.append((emb, len(matched), len(missing), title_fit, bool(job.is_remote)))
        y.append(float(job.score))
    session.commit()
    if len(y) < MIN_EXAMPLES:
        return None

    E = np.vstack([_unit(r[0]) for r in rows])
    emb_mean = E.mean(0)
    components = np.linalg.svd(E - emb_mean, full_matrices=False)[2][:_PCA_DIMS]
    model: Dict[str, Any] = {"resume_emb": resume_emb, "emb_mean": emb_mean.tolist(), "components": components.tolist(),
                             "unit": True}
    X = np.vstack([_features(r[0], model, *r[1:]) for r in rows])
    y_arr = np.asarray(y)
    lam = min(_LAMBDAS, key=lambda lam_: _cv_mae(X, y_arr, lam_))
    w, b, mu, sd = _fit(X, y_arr, lam)
    oof = _oof(X, y_arr, lam)
    cal_x, cal_y = _calibration(oof, y_arr)
    calibrated = np.interp(oof, cal_x, cal_y)  # honest: every job scored by a model that never saw it
    mae = float(np.abs(calibrated - y_arr).mean())
    agree = float(((calibrated >= 7) == (y_arr >= 7)).mean())
    stats = {"examples": len(y), "mae": round(mae, 2), "baseline_mae": round(float(np.abs(y_arr - y_arr.mean()).mean()), 2),
             "agree_at_7": round(agree * 100), "lambda": lam, "trained_at": time.strftime("%Y-%m-%d %H:%M"),
             "usable": mae <= MAX_USEFUL_MAE}
    model["cal_x"], model["cal_y"] = cal_x, cal_y
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**stats, **model, "w": w.tolist(), "b": b, "mu": mu.tolist(), "sd": sd.tolist()}))
    logger.info("local_scorer: user_id=%s trained on %s jobs, CV error ±%.2f (guessing the average: ±%.2f), "
                "agrees with the AI on apply/skip for %s%%", user_id, len(y), mae, stats["baseline_mae"], stats["agree_at_7"])
    return stats


def load(user_id: int) -> Optional[Dict[str, Any]]:
    path = _path(user_id)
    if not path.is_file():
        return None
    try:
        model = json.loads(path.read_text())
    except ValueError:
        return None
    return model if model.get("unit") else None  # older format: retrain


def predict(model: Dict[str, Any], job_emb: List[float], matched: int, missing: int,
            title_fit: bool, remote: bool) -> float:
    x = _features(job_emb, model, matched, missing, title_fit, remote)
    z = (x - np.asarray(model["mu"])) / np.asarray(model["sd"])
    raw = float(z @ np.asarray(model["w"]) + model["b"])
    return round(float(np.clip(np.interp(raw, model["cal_x"], model["cal_y"]), 0, 10)), 1)


def stats(user_id: int) -> Optional[Dict[str, Any]]:
    model = load(user_id)
    return {k: model.get(k) for k in ("examples", "mae", "baseline_mae", "agree_at_7", "trained_at", "usable")} if model else None
