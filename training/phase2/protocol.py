"""Predeclared Phase 2 corpus prerequisites, alternatives and promotion gate."""

from __future__ import annotations

SPLITS = ("train", "validation", "test", "adversarial", "regression", "holdout")
DEVELOPMENT = ("train", "validation")
PROTECTED = ("test", "adversarial", "regression", "holdout")
ABSTAIN = "ABSTAIN"
REASONS = ("out-of-domain", "ambiguous", "unsupported", "incomplete", "multi-operation")
PHENOMENA = (
    "ordinary",
    "short-command",
    "verbose",
    "paraphrase",
    "misleading-keyword",
    "adversarial-phrasing",
    "incomplete",
    "multi-operation",
    "out-of-domain",
    "ambiguous",
    "unsupported",
)
MIN_FAMILIES = {"train": 25, "validation": 5, "test": 5, "adversarial": 2, "regression": 2, "holdout": 5}
MIN_ABSTAIN_REASON_FAMILIES = {"train": 10, "validation": 3, "test": 3, "adversarial": 3, "regression": 3, "holdout": 3}
CONFIG = {
    "seed": 1729,
    "C": 4.0,
    "max_iter": 2000,
    "class_weight": "balanced",
    "solver": "lbfgs",
    "temperatures": [0.5, 0.75, 1.0, 1.5, 2.0],
    "thresholds": [0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95],
    "selection": "maximum validation authoritative macro F1 among candidates meeting safety targets",
    "candidates": ["word-hierarchy-reference", "word-char-threshold", "word-char-explicit-abstain"],
    "diagnostics": ["family-only", "deterministic-family", "hybrid"],
    "validation_min_coverage": 0.5,
    "validation_min_selective_accuracy": 0.95,
    "validation_min_abstain_recall": 0.9,
    "validation_min_abstain_precision": 0.9,
}
GATE = {
    "test_accuracy_margin_over_majority": 0.05,
    "test_authoritative_macro_f1_margin_over_keyword": 0.05,
    "test_holdout_min_coverage": 0.5,
    "all_protected_min_selective_accuracy": 0.95,
    "all_protected_min_abstain_precision": 0.9,
    "all_protected_min_abstain_recall": 0.9,
    "all_protected_min_ood_recall": 0.95,
    "test_holdout_min_family_accuracy": 0.95,
    "complete_training_and_validation_support": True,
    "no_contamination": True,
    "reproducible": True,
    "portable_artifact_validated": True,
    "authority_preserved": True,
}
