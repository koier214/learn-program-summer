#!/usr/bin/env python3
"""
ISF Level 3 - Two-stage large-scale ISF detection workflow (Python port)

Designed for:
  - 1000+ mzXML files
  - 100,000+ aligned features
  - limited RAM
  - resumable execution with progress bars

Scientific workflow:
  Stage 1:
    1. Generate ALL candidates satisfying RT and mass-loss rules.
    2. Do not keep only a fixed "top N".
    3. Select up to several strong co-present files per pair.
    4. Use these files for a lenient EIC-shape screen.

  Stage 2:
    1. For every pair that passes Stage 1, identify ALL files in
       which precursor and fragment are both present.
    2. Recalculate EIC correlations in all of those files.
    3. Apply the final peakCOR threshold to the full-file result.

Memory strategy:
  - mzXML files are opened one at a time per worker.
  - Feature intensities are stored in a memory-mapped numpy array.
  - Candidate features are processed in batches without top-N truncation.
  - Stage-2 pair-file assignments are stored as memory-mapped arrays.
  - Every expensive stage has checkpoints and progress bars.

Required packages:
  pip install pandas numpy tqdm pyopenms
"""

import os
import sys
import hashlib
import pickle
import shutil
import warnings
import tempfile
import time
import multiprocessing
from pathlib import Path
from typing import Optional, List, Dict, Union, Tuple, Any

import numpy as np
import pandas as pd
from tqdm import tqdm

# ============================================================
# Module-level helper functions (must be picklable for multiprocessing)
# ============================================================


def _peak_smooth(x: np.ndarray, level: int = 2) -> np.ndarray:
    """Triangular weighted moving average, identical to .isf_peak_smooth.

    Accepts 1D array or 2D array (scans × features, smoothed column-wise).
    """
    x = np.asarray(x, dtype=np.float64)
    x[~np.isfinite(x)] = 0.0

    n = int(level)

    if x.ndim == 1:
        N = len(x)
        if N <= 2 * n or N < 3 or np.max(x) == np.min(x):
            return x.copy()
        # Pre-compute weight matrix and apply
        W = _build_smooth_weights(N, n)
        return W @ x

    # 2D: matrix (scans × features), smooth each column
    N = x.shape[0]
    if N <= 2 * n or N < 3:
        return x.copy()
    W = _build_smooth_weights(N, n)
    # 只对非全零列做平滑（全零列结果也是零）
    col_range = np.max(x, axis=0) - np.min(x, axis=0)
    active = col_range > 0
    result = x.copy()
    if np.any(active):
        result[:, active] = W @ x[:, active]
    return result


def _build_smooth_weights(N: int, n: int) -> np.ndarray:
    """Build normalized (N, N) weight matrix for triangular moving average."""
    W = np.zeros((N, N), dtype=np.float64)

    # Head rows
    for i in range(n):
        w1 = list(range(n - i + 1, n + 2))
        w2 = list(range(n, 0, -1))
        weights = np.array(w1 + w2, dtype=np.float64)
        W[i, : i + n + 1] = weights / np.sum(weights)

    # Middle rows
    mid_weights = np.array(
        list(range(1, n + 2)) + list(range(n, 0, -1)),
        dtype=np.float64,
    )
    mid_sum = np.sum(mid_weights)
    half_window = n
    for i in range(n, N - n):
        W[i, i - half_window : i + half_window + 1] = mid_weights / mid_sum

    # Tail rows
    for i in range(N - n, N):
        i_r = i + 1
        w1 = list(range(1, n + 1))
        start = n + 1
        end = n + i_r - N + 1
        if start >= end:
            w2 = list(range(start, end - 1, -1))
        else:
            w2 = []
        weights = np.array(w1 + w2, dtype=np.float64)
        W[i, i_r - n - 1 : N] = weights / np.sum(weights)

    return W


def _cor_one_to_many(precursor_eic: np.ndarray, fragment_matrix: np.ndarray) -> np.ndarray:
    """Pearson correlation of one precursor EIC vs many fragment EICs (column-wise).

    Identical to .isf_cor_one_to_many.
    """
    x = np.asarray(precursor_eic, dtype=np.float64).copy()
    Y = np.asarray(fragment_matrix, dtype=np.float64).copy()

    if len(x) == 0 or Y.shape[1] == 0 or Y.shape[0] != len(x):
        return np.full(Y.shape[1], np.nan)

    x[~np.isfinite(x)] = 0.0
    Y[~np.isfinite(Y)] = 0.0

    xc = x - np.mean(x)
    Yc = Y - np.mean(Y, axis=0)

    numerator = np.dot(xc, Yc)
    denominator = np.sqrt(np.sum(xc * xc) * np.sum(Yc * Yc, axis=0))

    with np.errstate(divide="ignore", invalid="ignore"):
        result = numerator / denominator

    result[~np.isfinite(result) | (denominator == 0)] = np.nan
    return result


def _row_cor(candidate_matrix: np.ndarray, precursor_vector: np.ndarray) -> np.ndarray:
    """Row-wise Pearson correlation: each row of candidate_matrix vs precursor_vector.

    Identical to .isf_row_cor. Uses log1p transformation.
    """
    Y = np.asarray(candidate_matrix, dtype=np.float64).copy()
    x = np.asarray(precursor_vector, dtype=np.float64).copy()

    if Y.shape[0] == 0 or Y.shape[1] != len(x):
        return np.array([], dtype=np.float64)

    x[~np.isfinite(x)] = 0.0
    Y[~np.isfinite(Y)] = 0.0

    x = np.log1p(np.maximum(x, 0))
    Y = np.log1p(np.maximum(Y, 0))

    xc = x - np.mean(x)
    Yc = Y - np.mean(Y, axis=1, keepdims=True)

    numerator = np.dot(Yc, xc)
    denominator = np.sqrt(np.sum(Yc * Yc, axis=1) * np.sum(xc * xc))

    with np.errstate(divide="ignore", invalid="ignore"):
        result = numerator / denominator

    result[~np.isfinite(result) | (denominator == 0)] = np.nan
    return result


# ============================================================
# Module-level EIC processor (picklable for multiprocessing on Windows)
# ============================================================

def _atomic_save_pkl_static(obj: Any, path: str) -> str:
    """Standalone atomic pickle save for multiprocessing workers."""
    tmp_path = path + f".tmp_{os.getpid()}"
    with open(tmp_path, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    if os.path.exists(path):
        os.unlink(path)
    try:
        os.rename(tmp_path, path)
    except OSError:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise OSError(f"Could not write checkpoint: {path}")
    return path


def _process_one_file_eic(
    file_id: int,
    assignments: pd.DataFrame,
    mzxml_file: str,
    mz: np.ndarray,
    rt: np.ndarray,
    mz_tol: float,
    rt_tol: float,
    block_width: float,
    smooth_level: int,
    result_path: str,
) -> dict:
    """Compute EIC correlations for all assigned pairs in one mzXML/mzML file.

    This is a module-level function so it can be pickled for multiprocessing.
    All data is passed as arguments (no `self` reference).

    Optimizations:
      - m/z range computed once (not per block)
      - Per-block scan data pre-extracted once, reused for all features
      - Binary search for RT window (not linear scan)
      - Direct EIC matrix construction (no alignment step)
    """
    if os.path.exists(result_path):
        return {"file_id": file_id, "skipped": True, "n_correlations": None}

    if len(assignments) == 0:
        empty_result = pd.DataFrame(
            {"pair_id": pd.Series([], dtype="int64"), "cor": pd.Series([], dtype="float64")}
        )
        _atomic_save_pkl_static(empty_result, result_path)
        return {"file_id": file_id, "skipped": False, "n_correlations": 0}

    import pyopenms

    exp = pyopenms.MSExperiment()
    fname_lower = mzxml_file.lower()
    if fname_lower.endswith(".mzml"):
        pyopenms.MzMLFile().load(mzxml_file, exp)
    else:
        pyopenms.MzXMLFile().load(mzxml_file, exp)

    scan_times_all = np.array([spec.getRT() for spec in exp])

    # 检查 RT 是否严格递增
    if len(scan_times_all) > 1 and not np.all(np.diff(scan_times_all) >= 0):
        raise ValueError(
            f"mzML file RT is not monotonically increasing: {mzxml_file}"
        )

    first_rt = scan_times_all[0]
    last_rt = scan_times_all[-1]

    # ---- 一次性计算整个文件的 m/z 范围 ----
    mz_min_file, mz_max_file = float("inf"), float("-inf")
    for s in exp:
        peaks = s.get_peaks()
        if len(peaks[0]) > 0:
            mz_min_file = min(mz_min_file, float(peaks[0][0]))
            mz_max_file = max(mz_max_file, float(peaks[0][-1]))
    mz_range_file = (
        mz_min_file if np.isfinite(mz_min_file) else 0.0,
        mz_max_file if np.isfinite(mz_max_file) else 1e10,
    )

    # Assign blocks (group precursors by RT proximity)
    assignments = assignments.copy()
    assignments["block"] = np.floor(
        rt[assignments["precursor"].values.astype(int)] / block_width
    ).astype(np.int64)
    assignments = assignments.sort_values(["block", "precursor", "fragment"]).reset_index(drop=True)

    block_ids = assignments["block"].unique()
    result_parts = []

    for current_block_id in block_ids:
        current_block = assignments[assignments["block"] == current_block_id]
        precursor_ids = np.unique(current_block["precursor"].values.astype(int))

        block_rt_lower = max(first_rt, np.min(rt[precursor_ids]) - rt_tol)
        block_rt_upper = min(last_rt, np.max(rt[precursor_ids]) + rt_tol)

        if (
            not np.isfinite(block_rt_lower)
            or not np.isfinite(block_rt_upper)
            or block_rt_upper <= block_rt_lower
        ):
            continue

        # ---- 二分查找 RT 窗口对应的扫描范围 ----
        scan_start = int(np.searchsorted(scan_times_all, block_rt_lower, side="left"))
        scan_end = int(np.searchsorted(scan_times_all, block_rt_upper, side="right"))

        if scan_end - scan_start < 5:
            continue

        # ---- 一次性预提取窗口内所有扫描的 m/z 和 intensity 数据 ----
        n_scans_window = scan_end - scan_start
        window_mz = [None] * n_scans_window
        window_int = [None] * n_scans_window
        window_rts = np.zeros(n_scans_window, dtype=np.float64)

        for i, si in enumerate(range(scan_start, scan_end)):
            mz_arr, int_arr = exp[si].get_peaks()
            window_mz[i] = mz_arr if len(mz_arr) > 0 else np.zeros(0, dtype=np.float64)
            window_int[i] = int_arr if len(int_arr) > 0 else np.zeros(0, dtype=np.float64)
            window_rts[i] = scan_times_all[si]

        # ---- 确定需要的特征并过滤 ----
        required_features = np.unique(
            np.concatenate(
                [
                    current_block["precursor"].values.astype(int),
                    current_block["fragment"].values.astype(int),
                ]
            )
        )

        valid_mz = (
            (mz[required_features] - mz_tol >= mz_range_file[0])
            & (mz[required_features] + mz_tol <= mz_range_file[1])
        )
        required_features = required_features[valid_mz]

        if len(required_features) == 0:
            continue

        # ---- 直接构建 EIC 矩阵（所有特征共用同一扫描网格） ----
        feature_ids_in_eic = np.array(required_features)
        n_features_in_block = len(feature_ids_in_eic)
        eic_matrix = np.zeros((n_scans_window, n_features_in_block), dtype=np.float64)

        # 预计算所有特征的 m/z 上下界（向量化准备）
        mz_lows = mz[feature_ids_in_eic] - mz_tol
        mz_highs = mz[feature_ids_in_eic] + mz_tol

        for i in range(n_scans_window):
            mz_arr = window_mz[i]
            if len(mz_arr) == 0:
                continue
            int_arr = window_int[i]

            # 向量化二分查找：所有特征一次完成
            lefts = np.searchsorted(mz_arr, mz_lows, side="left")
            rights = np.searchsorted(mz_arr, mz_highs, side="right")

            # 累积强度，O(1) 区间求和
            cum_int = np.empty(len(int_arr) + 1, dtype=np.float64)
            cum_int[0] = 0.0
            np.cumsum(int_arr, out=cum_int[1:])

            valid = lefts < rights
            if np.any(valid):
                eic_matrix[i, valid] = cum_int[rights[valid]] - cum_int[lefts[valid]]

        # Smooth all EICs at once (vectorized matrix multiply)
        eic_matrix = _peak_smooth(eic_matrix, level=smooth_level)

        # 释放窗口数据
        del window_mz, window_int
        feature_names = {str(fid): j for j, fid in enumerate(feature_ids_in_eic)}

        # Group by precursor, compute correlations
        grouped = current_block.groupby("precursor")
        block_results = []

        for precursor_id, group_rows in grouped:
            precursor_id = int(precursor_id)
            precursor_name = str(precursor_id)

            if precursor_name not in feature_names:
                continue

            window_idx = np.where(
                (window_rts >= rt[precursor_id] - rt_tol)
                & (window_rts <= rt[precursor_id] + rt_tol)
            )[0]

            if len(window_idx) < 5:
                continue

            fragment_ids = group_rows["fragment"].values.astype(int)
            fragment_names = [str(fid) for fid in fragment_ids]

            valid_frag = [fn in feature_names for fn in fragment_names]
            if not any(valid_frag):
                continue

            fragment_names = [fn for fn, v in zip(fragment_names, valid_frag) if v]
            fragment_indices_row = [group_rows.index[i] for i, v in enumerate(valid_frag) if v]
            group_row_indices = np.array(fragment_indices_row)

            precursor_eic = eic_matrix[window_idx, feature_names[precursor_name]]
            frag_cols = [feature_names[fn] for fn in fragment_names]
            frag_eic_matrix = eic_matrix[np.ix_(window_idx, frag_cols)]

            correlations = _cor_one_to_many(precursor_eic, frag_eic_matrix)

            keep = np.isfinite(correlations)
            if not np.any(keep):
                continue

            keep_indices = np.where(keep)[0]
            pair_ids_in_block = current_block.loc[group_row_indices[keep_indices], "pair_id"].values

            block_results.append(
                pd.DataFrame(
                    {
                        "pair_id": pair_ids_in_block.astype(np.int64),
                        "cor": correlations[keep_indices].astype(np.float64),
                    }
                )
            )

        if block_results:
            result_parts.append(pd.concat(block_results, ignore_index=True))

        del eic_matrix

    if result_parts:
        result = pd.concat(result_parts, ignore_index=True)
        if result["pair_id"].duplicated().any():
            result = result.groupby("pair_id", as_index=False)["cor"].mean()
    else:
        result = pd.DataFrame(
            {"pair_id": pd.Series([], dtype="int64"), "cor": pd.Series([], dtype="float64")}
        )

    _atomic_save_pkl_static(result, result_path)

    return {"file_id": file_id, "skipped": False, "n_correlations": len(result)}


def _stage1_file_worker(args_tuple):
    """Module-level worker for Stage 1 EIC processing."""
    (file_id, assignment_path, mzxml_file, mz, rt, mz_tol, rt_tol,
     block_width, smooth_level, result_path) = args_tuple

    with open(assignment_path, "rb") as f:
        assignments = pickle.load(f)

    return _process_one_file_eic(
        file_id=file_id, assignments=assignments, mzxml_file=mzxml_file,
        mz=mz, rt=rt, mz_tol=mz_tol, rt_tol=rt_tol,
        block_width=block_width, smooth_level=smooth_level,
        result_path=result_path,
    )


def _stage2_file_worker(args_tuple):
    """Module-level worker for Stage 2 EIC processing."""
    (file_id, mzxml_file, mz, rt, mz_tol, rt_tol, block_width,
     smooth_level, result_path, count, descriptor_path, starts) = args_tuple

    if os.path.exists(result_path):
        return {"file_id": file_id, "skipped": True, "n_correlations": None}

    if count <= 0:
        assignments = pd.DataFrame(
            {"pair_id": pd.Series([], dtype="int64"),
             "precursor": pd.Series([], dtype="int64"),
             "fragment": pd.Series([], dtype="int64")}
        )
    else:
        if os.path.getsize(descriptor_path) > 0:
            mmap = np.memmap(descriptor_path, dtype="int64", mode="r",
                             shape=(int(os.path.getsize(descriptor_path) // 24), 3))
            start = starts[file_id]
            values = mmap[start: start + count, :].copy()
            del mmap
            assignments = pd.DataFrame(
                {"pair_id": values[:, 0].astype(np.int64),
                 "precursor": values[:, 1].astype(np.int64),
                 "fragment": values[:, 2].astype(np.int64)}
            )
        else:
            assignments = pd.DataFrame(
                {"pair_id": pd.Series([], dtype="int64"),
                 "precursor": pd.Series([], dtype="int64"),
                 "fragment": pd.Series([], dtype="int64")}
            )

    return _process_one_file_eic(
        file_id=file_id, assignments=assignments, mzxml_file=mzxml_file,
        mz=mz, rt=rt, mz_tol=mz_tol, rt_tol=rt_tol,
        block_width=block_width, smooth_level=smooth_level,
        result_path=result_path,
    )


# ============================================================
# Main class
# ============================================================


class ISFLevel3TwoStage:
    """Two-stage ISF Level 3 analysis (Python port of ISFlevel3_two_stage R function).

    Usage
    -----
    >>> analyzer = ISFLevel3TwoStage(
    ...     MS1directory="D:/MS1_mzXML",
    ...     MS1_files=["sample1.mzXML", "sample2.mzXML"],
    ...     featureTable=feature_df,
    ...     peakCOR=0.80,
    ...     loss=10,
    ...     workers=2,
    ... )
    >>> result = analyzer.run()
    >>> print(result["hits"])
    """

    def __init__(
        self,
        MS1directory: str,
        MS1_files: List[str],
        featureTable: pd.DataFrame,
        # Column names
        mz_col: str = "mz",
        rt_col: str = "rt",
        intensity_cols: Optional[Union[List[int], List[str]]] = None,
        # Final scientific thresholds
        peakCOR: float = 0.80,
        loss: float = 10.0,
        mz_tol: float = 0.01,
        rt_tol: float = 30.0,
        candidate_rt: float = 10.0,
        # Candidate presence requirements
        min_copresent_files: int = 3,
        # Stage 1: representative-file screen
        stage1_files_per_pair: int = 5,
        screenCOR: Optional[float] = 0.65,
        stage1_min_valid: int = 2,
        stage1_fail_open_sparse: bool = True,
        # Optional cross-sample intensity prefilter
        prefilter_cor: Optional[float] = None,
        prefilter_samples: int = 64,
        # Stage 2: final all-co-present-file decision
        min_final_valid: int = 3,
        final_min_proportion: float = 0.0,
        # Chunk and memory controls
        candidate_feature_chunk: int = 500,
        candidate_batch_size: int = 250,
        stage2_pair_batch_size: int = 1000,
        block_width: float = 60.0,
        smooth_level: int = 2,
        # File-level parallelism
        workers: int = 2,
        # Cache and resume
        work_dir: Optional[str] = None,
        run_id: str = "default",
        batch_label: str = "",
        rebuild_intensity_cache: bool = False,
        rebuild_candidates: bool = False,
        rebuild_stage1: bool = False,
        rebuild_stage2_assignments: bool = False,
        rebuild_stage2: bool = False,
        # Output
        build_groups: bool = False,
    ):
        # Core data
        self.MS1directory = MS1directory
        self.featureTable = featureTable
        self.mz_col = mz_col
        self.rt_col = rt_col
        self.intensity_cols_in = intensity_cols

        # Files
        self.MS1_files_raw = list(MS1_files)

        # Scientific parameters
        self.peakCOR = peakCOR
        self.loss = loss
        self.mz_tol = mz_tol
        self.rt_tol = rt_tol
        self.candidate_rt = candidate_rt

        # Presence
        self.min_copresent_files = int(min_copresent_files)
        self.stage1_files_per_pair = int(stage1_files_per_pair)
        self.screenCOR = screenCOR
        self.stage1_min_valid = int(stage1_min_valid)
        self.stage1_fail_open_sparse = stage1_fail_open_sparse

        # Prefilter
        self.prefilter_cor = prefilter_cor
        self.prefilter_samples = int(prefilter_samples)

        # Stage 2
        self.min_final_valid = int(min_final_valid)
        self.final_min_proportion = float(final_min_proportion)

        # Chunk / memory
        self.candidate_feature_chunk = int(candidate_feature_chunk)
        self.candidate_batch_size = int(candidate_batch_size)
        self.stage2_pair_batch_size = int(stage2_pair_batch_size)
        self.block_width = float(block_width)
        self.smooth_level = int(smooth_level)

        # Parallelism
        self.workers = int(workers)

        # Cache
        self.work_dir_in = work_dir
        self.run_id = run_id
        self.batch_label = batch_label

        # Rebuild flags
        self.rebuild_intensity_cache = rebuild_intensity_cache
        self.rebuild_candidates = rebuild_candidates
        self.rebuild_stage1 = rebuild_stage1
        self.rebuild_stage2_assignments = rebuild_stage2_assignments
        self.rebuild_stage2 = rebuild_stage2

        # Output
        self.build_groups = build_groups

        # Internal state (populated during run)
        self.files = None
        self.n_files = 0
        self.n_features = 0
        self.intensity_cols = None
        self.mz = None
        self.rt = None
        self.work_dir = None
        self.data_signature = None
        self.run_signature = None
        self.run_dir = None

    # ----------------------------------------------------------
    # Package checks
    # ----------------------------------------------------------
    @staticmethod
    def _check_packages():
        """Check that required packages are available."""
        missing = []
        for pkg in ["numpy", "pandas"]:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)

        # pyopenms is optional — only needed if processing mzXML
        try:
            import pyopenms  # noqa: F401
        except ImportError:
            missing.append("pyopenms")

        if missing:
            raise ImportError(
                f"Missing packages: {', '.join(missing)}\n"
                f"Install with: pip install {' '.join(missing)}"
            )

    # ----------------------------------------------------------
    # General helpers
    # ----------------------------------------------------------
    @staticmethod
    def _make_dir(path: str) -> str:
        os.makedirs(path, exist_ok=True)
        return os.path.normpath(path)

    @staticmethod
    def _clear_dir(path: str) -> None:
        if not os.path.exists(path):
            return
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)

    @staticmethod
    def _atomic_save_pkl(obj: Any, path: str) -> str:
        """Atomically save a pickle file, like .isf_atomic_save_rds."""
        tmp_path = path + f".tmp_{os.getpid()}"
        with open(tmp_path, "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)

        if os.path.exists(path):
            os.unlink(path)

        try:
            os.rename(tmp_path, path)
        except OSError:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise OSError(f"Could not write checkpoint: {path}")

        return path

    @staticmethod
    def _hash_text(lines: List[str]) -> str:
        """MD5 hash of text lines, like .isf_hash_text."""
        content = "\n".join(lines).encode("utf-8")
        return hashlib.md5(content).hexdigest()

    def _data_signature(self) -> str:
        """Create a data fingerprint for cache identification."""
        file_info = []
        for f in self.files:
            st = os.stat(f)
            file_info.append((f, st.st_size, st.st_mtime))

        lines = [
            f"n_features={self.n_features}",
            f"n_files={self.n_files}",
            f"file_names={'|'.join(os.path.basename(f) for f in self.files)}",
            f"file_sizes={'|'.join(str(sz) for _, sz, _ in file_info)}",
            f"file_mtime={'|'.join(str(mt) for _, _, mt in file_info)}",
            f"mz_sum={np.sum(self.mz):.15g}",
            f"rt_sum={np.sum(self.rt):.15g}",
            f"intensity_columns={'|'.join(str(self.featureTable.columns[c]) for c in self.intensity_cols)}",
        ]
        return self._hash_text(lines)

    def _run_signature(self) -> str:
        """Create a run-parameter fingerprint for cache identification."""
        prefilter_text = "NULL" if self.prefilter_cor is None else str(self.prefilter_cor)
        screen_text = "NULL" if self.screenCOR is None else str(self.screenCOR)

        lines = [
            f"data_signature={self.data_signature}",
            f"run_id={self.run_id}",
            f"peakCOR={self.peakCOR}",
            f"screenCOR={screen_text}",
            f"loss={self.loss}",
            f"mz.tol={self.mz_tol}",
            f"rt.tol={self.rt_tol}",
            f"candidate.rt={self.candidate_rt}",
            f"min_copresent_files={self.min_copresent_files}",
            f"stage1_files_per_pair={self.stage1_files_per_pair}",
            f"stage1_min_valid={self.stage1_min_valid}",
            f"stage1_fail_open_sparse={self.stage1_fail_open_sparse}",
            f"prefilter_cor={prefilter_text}",
            f"prefilter_samples={self.prefilter_samples}",
            f"min_final_valid={self.min_final_valid}",
            f"final_min_proportion={self.final_min_proportion}",
            f"candidate_feature_chunk={self.candidate_feature_chunk}",
            f"candidate_batch_size={self.candidate_batch_size}",
            f"stage2_pair_batch_size={self.stage2_pair_batch_size}",
            f"block.width={self.block_width}",
            f"smooth_level={self.smooth_level}",
        ]
        return self._hash_text(lines)

    @staticmethod
    def _split_indices(n: int, chunk_size: int) -> List[np.ndarray]:
        """Split 0..n-1 into chunks of given size."""
        if n <= 0:
            return []
        indices = np.arange(n)
        return np.array_split(indices, max(1, int(np.ceil(n / chunk_size))))

    @staticmethod
    def _empty_hits() -> pd.DataFrame:
        """Return an empty hits DataFrame with the expected schema."""
        return pd.DataFrame(
            {
                "pair_id": pd.Series([], dtype="int64"),
                "precursor": pd.Series([], dtype="int64"),
                "fragment": pd.Series([], dtype="int64"),
                "n_copresent": pd.Series([], dtype="int64"),
                "prefilter_cor": pd.Series([], dtype="float64"),
                "stage1_valid_files": pd.Series([], dtype="int64"),
                "stage1_mean_cor": pd.Series([], dtype="float64"),
                "stage1_max_cor": pd.Series([], dtype="float64"),
                "stage1_prop_ge_peak": pd.Series([], dtype="float64"),
                "stage1_pass": pd.Series([], dtype="bool"),
                "stage2_id": pd.Series([], dtype="int64"),
                "final_valid_files": pd.Series([], dtype="int64"),
                "final_mean_cor": pd.Series([], dtype="float64"),
                "final_prop_ge_peak": pd.Series([], dtype="float64"),
                "final_min_cor": pd.Series([], dtype="float64"),
                "final_max_cor": pd.Series([], dtype="float64"),
                "final_pass": pd.Series([], dtype="bool"),
            }
        )

    # ----------------------------------------------------------
    # File-backed intensity matrix
    # ----------------------------------------------------------
    def _prepare_intensity_matrix(self) -> np.memmap:
        """Build or reuse a memory-mapped float32 intensity matrix."""
        if self.batch_label:
            cache_dir = self._make_dir(
                os.path.join(self.work_dir, f"intensity_{self.batch_label}")
            )
        else:
            cache_dir = self._make_dir(
                os.path.join(self.work_dir, f"intensity_{self.data_signature}")
            )
        mmap_path = os.path.join(cache_dir, "intensity_float.dat")
        meta_path = os.path.join(cache_dir, "intensity_metadata.pkl")

        expected_meta = {
            "n_features": self.n_features,
            "n_files": self.n_files,
            "intensity_names": [self.featureTable.columns[c] for c in self.intensity_cols],
        }

        if (
            not self.rebuild_intensity_cache
            and os.path.exists(mmap_path)
            and os.path.exists(meta_path)
        ):
            with open(meta_path, "rb") as f:
                stored_meta = pickle.load(f)
            if stored_meta == expected_meta:
                print("Reusing the memory-mapped intensity matrix.")
                return np.memmap(
                    mmap_path, dtype="float32", mode="r",
                    shape=(self.n_features, self.n_files)
                )

        # Clear old cache
        for p in [mmap_path, meta_path]:
            if os.path.exists(p):
                os.unlink(p)

        print("Creating the memory-mapped float intensity matrix...")

        # Create memmap in write mode
        bm = np.memmap(
            mmap_path, dtype="float32", mode="w+",
            shape=(self.n_features, self.n_files)
        )

        for j in tqdm(range(self.n_files), desc="Intensity matrix", unit="col"):
            col_idx = self.intensity_cols[j]
            values = pd.to_numeric(self.featureTable.iloc[:, col_idx], errors="coerce").to_numpy(dtype="float32")
            values[~np.isfinite(values)] = 0.0
            values[values < 0] = 0.0
            bm[:, j] = values

        bm.flush()

        self._atomic_save_pkl(expected_meta, meta_path)

        # Re-open in read mode for safety
        return np.memmap(
            mmap_path, dtype="float32", mode="r",
            shape=(self.n_features, self.n_files)
        )

    # ----------------------------------------------------------
    # Candidate generation – single chunk
    # ----------------------------------------------------------
    def _build_candidate_chunk(
        self,
        precursor_ids: np.ndarray,
        bm: np.memmap,
        order_rt: np.ndarray,
        position_in_order: np.ndarray,
        rt_sorted: np.ndarray,
        mz_sorted: np.ndarray,
        prefilter_file_ids: np.ndarray,
    ) -> dict:
        """Build candidate pairs for a chunk of precursor features."""
        pair_parts = []
        assignment_parts = []
        next_local_pair_id = 0

        n_files = self.n_files
        loss = self.loss
        candidate_rt = self.candidate_rt

        # 一次性读入本块所有 precursor 的强度行，避免循环内逐行读 memmap
        chunk_precursor_data = bm[precursor_ids.astype(int), :].copy().astype(np.float64)
        chunk_precursor_data[~np.isfinite(chunk_precursor_data)] = 0.0

        for i, precursor_id in enumerate(precursor_ids):
            precursor_id = int(precursor_id)
            precursor_pos = int(position_in_order[precursor_id])

            lower_rt = self.rt[precursor_id] - candidate_rt
            upper_rt = self.rt[precursor_id] + candidate_rt

            left = int(np.searchsorted(rt_sorted, lower_rt, side="right"))
            right = int(np.searchsorted(rt_sorted, upper_rt, side="left")) - 1

            if left > right or left >= len(rt_sorted) or right < 0:
                continue

            candidate_positions = np.arange(left, right + 1)

            # RT window + mass-loss filter
            keep = (
                (rt_sorted[candidate_positions] > lower_rt)
                & (rt_sorted[candidate_positions] < upper_rt)
                & (mz_sorted[candidate_positions] <= self.mz[precursor_id] - loss)
            )
            candidate_positions = candidate_positions[keep]

            if len(candidate_positions) == 0:
                continue

            candidate_ids = order_rt[candidate_positions]

            precursor_all = chunk_precursor_data[i, :]  # 内存索引，零 IO
            precursor_positive = precursor_all > 0

            if np.sum(precursor_positive) < self.min_copresent_files:
                continue

            # Process candidates in batches
            candidate_batches = self._split_indices(
                len(candidate_ids), self.candidate_batch_size
            )

            for batch_indices in candidate_batches:
                if len(batch_indices) == 0:
                    continue

                current_candidate_ids = candidate_ids[batch_indices.astype(int)]

                # Read fragment intensities (rows = fragments, cols = files)
                fragment_all = bm[current_candidate_ids.astype(int), :].copy().astype(np.float64)
                fragment_all[~np.isfinite(fragment_all)] = 0.0

                # Co-presence matrix
                common_presence = (fragment_all > 0) & precursor_positive[np.newaxis, :]
                copresent_count = np.sum(common_presence, axis=1)

                eligible = np.where(copresent_count >= self.min_copresent_files)[0]

                if len(eligible) == 0:
                    continue

                current_candidate_ids = current_candidate_ids[eligible]
                fragment_all = fragment_all[eligible, :]
                common_presence = common_presence[eligible, :]
                copresent_count = copresent_count[eligible]

                # Optional prefilter
                sample_cor = np.full(len(current_candidate_ids), np.nan)

                if self.prefilter_cor is not None:
                    sample_cor = _row_cor(
                        fragment_all[:, prefilter_file_ids],
                        precursor_all[prefilter_file_ids],
                    )

                    keep_prefilter = np.where(
                        np.isfinite(sample_cor) & (sample_cor >= self.prefilter_cor)
                    )[0]

                    if len(keep_prefilter) == 0:
                        continue

                    current_candidate_ids = current_candidate_ids[keep_prefilter]
                    fragment_all = fragment_all[keep_prefilter, :]
                    common_presence = common_presence[keep_prefilter, :]
                    copresent_count = copresent_count[keep_prefilter]
                    sample_cor = sample_cor[keep_prefilter]

                n_candidates = len(current_candidate_ids)
                local_pair_ids = np.arange(
                    next_local_pair_id, next_local_pair_id + n_candidates, dtype=np.int64
                )
                next_local_pair_id += n_candidates

                # Build pairs DataFrame
                pair_df = pd.DataFrame(
                    {
                        "pair_id": local_pair_ids,
                        "precursor": np.full(n_candidates, precursor_id, dtype=np.int64),
                        "fragment": current_candidate_ids.astype(np.int64),
                        "n_copresent": copresent_count.astype(np.int64),
                        "prefilter_cor": sample_cor.astype(np.float64),
                    }
                )
                pair_parts.append(pair_df)

                # Build assignments — select representative files per pair
                assignment_rows = []
                for j in range(n_candidates):
                    common_files = np.where(common_presence[j, :])[0]

                    if len(common_files) == 0:
                        continue

                    joint_score = np.sqrt(
                        np.maximum(precursor_all[common_files], 0)
                        * np.maximum(fragment_all[j, common_files], 0)
                    )
                    rep_order = np.argsort(-joint_score)
                    rep_files = common_files[
                        rep_order[: min(self.stage1_files_per_pair, len(rep_order))]
                    ]

                    for f in rep_files:
                        assignment_rows.append(
                            {
                                "file_id": int(f),
                                "pair_id": int(local_pair_ids[j]),
                                "precursor": int(precursor_id),
                                "fragment": int(current_candidate_ids[j]),
                            }
                        )

                if assignment_rows:
                    assignment_parts.append(pd.DataFrame(assignment_rows))

        # Combine
        if pair_parts:
            pairs = pd.concat(pair_parts, ignore_index=True)
        else:
            pairs = pd.DataFrame(
                {
                    "pair_id": pd.Series([], dtype="int64"),
                    "precursor": pd.Series([], dtype="int64"),
                    "fragment": pd.Series([], dtype="int64"),
                    "n_copresent": pd.Series([], dtype="int64"),
                    "prefilter_cor": pd.Series([], dtype="float64"),
                }
            )

        if assignment_parts:
            assignments = pd.concat(assignment_parts, ignore_index=True)
        else:
            assignments = pd.DataFrame(
                {
                    "file_id": pd.Series([], dtype="int64"),
                    "pair_id": pd.Series([], dtype="int64"),
                    "precursor": pd.Series([], dtype="int64"),
                    "fragment": pd.Series([], dtype="int64"),
                }
            )

        return {"pairs": pairs, "assignments": assignments}

    # ----------------------------------------------------------
    # Candidate preparation — all chunks
    # ----------------------------------------------------------
    def _prepare_candidates(self, bm: np.memmap) -> dict:
        """Generate all candidate pairs, chunk by chunk, with checkpointing."""
        combined_pairs_path = os.path.join(self.run_dir, "candidate_pairs.pkl")
        combined_assignments_path = os.path.join(
            self.run_dir, "stage1_representative_assignments.pkl"
        )

        if (
            not self.rebuild_candidates
            and os.path.exists(combined_pairs_path)
            and os.path.exists(combined_assignments_path)
        ):
            print("Reusing combined candidate checkpoints.")
            with open(combined_pairs_path, "rb") as f:
                pairs = pickle.load(f)
            with open(combined_assignments_path, "rb") as f:
                assignments = pickle.load(f)
            return {"pairs": pairs, "assignments": assignments}

        candidate_chunk_dir = self._make_dir(
            os.path.join(self.run_dir, "candidate_chunks")
        )

        if self.rebuild_candidates:
            self._clear_dir(candidate_chunk_dir)

        # Sort features by RT
        order_rt = np.argsort(self.rt)
        rt_sorted = self.rt[order_rt]
        mz_sorted = self.mz[order_rt]
        position_in_order = np.zeros(self.n_features, dtype=np.int64)
        position_in_order[order_rt] = np.arange(self.n_features, dtype=np.int64)

        feature_chunks = self._split_indices(
            self.n_features, self.candidate_feature_chunk
        )

        print("Generating candidate chunks without a top-N truncation...")

        for chunk_id, chunk_indices in enumerate(
            tqdm(feature_chunks, desc="Candidate chunks", unit="chunk"), start=1
        ):
            checkpoint_path = os.path.join(
                candidate_chunk_dir, f"candidate_chunk_{chunk_id:06d}.pkl"
            )

            if not os.path.exists(checkpoint_path):
                current = self._build_candidate_chunk(
                    precursor_ids=chunk_indices,
                    bm=bm,
                    order_rt=order_rt,
                    position_in_order=position_in_order,
                    rt_sorted=rt_sorted,
                    mz_sorted=mz_sorted,
                    prefilter_file_ids=self._prefilter_file_ids,
                )
                self._atomic_save_pkl(current, checkpoint_path)
                del current

        # Combine chunks
        print("Combining candidate chunks...")
        pair_parts = []
        assignment_parts = []
        next_global_pair_id = 0

        for chunk_id in tqdm(
            range(1, len(feature_chunks) + 1), desc="Combining", unit="chunk"
        ):
            checkpoint_path = os.path.join(
                candidate_chunk_dir, f"candidate_chunk_{chunk_id:06d}.pkl"
            )
            with open(checkpoint_path, "rb") as f:
                current = pickle.load(f)

            if len(current["pairs"]) > 0:
                offset = next_global_pair_id
                current["pairs"]["pair_id"] += offset
                current["assignments"]["pair_id"] += offset
                next_global_pair_id += len(current["pairs"])

                pair_parts.append(current["pairs"])
                assignment_parts.append(current["assignments"])

            del current

        if pair_parts:
            pairs = pd.concat(pair_parts, ignore_index=True)
            assignments = pd.concat(assignment_parts, ignore_index=True)
        else:
            pairs = pd.DataFrame(
                {
                    "pair_id": pd.Series([], dtype="int64"),
                    "precursor": pd.Series([], dtype="int64"),
                    "fragment": pd.Series([], dtype="int64"),
                    "n_copresent": pd.Series([], dtype="int64"),
                    "prefilter_cor": pd.Series([], dtype="float64"),
                }
            )
            assignments = pd.DataFrame(
                {
                    "file_id": pd.Series([], dtype="int64"),
                    "pair_id": pd.Series([], dtype="int64"),
                    "precursor": pd.Series([], dtype="int64"),
                    "fragment": pd.Series([], dtype="int64"),
                }
            )

        self._atomic_save_pkl(pairs, combined_pairs_path)
        self._atomic_save_pkl(assignments, combined_assignments_path)

        return {"pairs": pairs, "assignments": assignments}

    # ----------------------------------------------------------
    # Write per-file assignment checkpoints
    # ----------------------------------------------------------
    def _write_assignments_by_file(self, assignments: pd.DataFrame) -> None:
        """Write compact per-file assignment files."""
        assignment_dir = self._make_dir(
            os.path.join(self.run_dir, "stage1_assignments")
        )
        complete_flag = os.path.join(assignment_dir, "_COMPLETE")

        if not self.rebuild_candidates and os.path.exists(complete_flag):
            print("Reusing per-file Stage-1 assignment checkpoints.")
            return

        self._clear_dir(assignment_dir)

        empty_df = pd.DataFrame(
            {
                "pair_id": pd.Series([], dtype="int64"),
                "precursor": pd.Series([], dtype="int64"),
                "fragment": pd.Series([], dtype="int64"),
            }
        )

        for file_id in tqdm(range(self.n_files), desc="Writing assignments", unit="file"):
            assignment_path = os.path.join(assignment_dir, f"file_{file_id:05d}.pkl")

            if len(assignments) > 0:
                mask = assignments["file_id"].values == file_id
                if mask.any():
                    current = assignments.loc[mask, ["pair_id", "precursor", "fragment"]].copy()
                else:
                    current = empty_df.copy()
            else:
                current = empty_df.copy()

            self._atomic_save_pkl(current, assignment_path)

        # Write completion flag
        with open(complete_flag, "w") as f:
            f.write("complete\n")

    # ----------------------------------------------------------
    # Process one file — delegates to module-level function
    # ----------------------------------------------------------
    def _process_file_assignments(
        self, file_id: int, assignments: pd.DataFrame, mzxml_file: str, result_path: str
    ) -> dict:
        """Thin wrapper around module-level _process_one_file_eic."""
        return _process_one_file_eic(
            file_id=file_id, assignments=assignments, mzxml_file=mzxml_file,
            mz=self.mz, rt=self.rt, mz_tol=self.mz_tol, rt_tol=self.rt_tol,
            block_width=self.block_width, smooth_level=self.smooth_level,
            result_path=result_path,
        )

    # ----------------------------------------------------------
    # Stage 1 – run and aggregate
    # ----------------------------------------------------------
    def _run_stage1_files(self, missing_file_ids: List[int]):
        """Run Stage 1 EIC screen on selected files (with parallel support)."""
        stage1_assignment_dir = os.path.join(self.run_dir, "stage1_assignments")
        stage1_result_dir = self._make_dir(os.path.join(self.run_dir, "stage1_results"))

        # Build argument tuples for module-level worker
        arg_tuples = []
        for file_id in missing_file_ids:
            assignment_path = os.path.join(stage1_assignment_dir, f"file_{file_id:05d}.pkl")
            result_path = os.path.join(stage1_result_dir, f"file_{file_id:05d}.pkl")
            arg_tuples.append((
                file_id, assignment_path, self.files[file_id],
                self.mz.copy(), self.rt.copy(),
                self.mz_tol, self.rt_tol, self.block_width, self.smooth_level,
                result_path,
            ))

        if self.workers <= 1:
            for args in tqdm(arg_tuples, desc="Stage 1 EIC", unit="file"):
                _stage1_file_worker(args)
        else:
            with multiprocessing.Pool(processes=self.workers) as pool:
                list(
                    tqdm(
                        pool.imap_unordered(_stage1_file_worker, arg_tuples),
                        total=len(arg_tuples),
                        desc="Stage 1 EIC",
                        unit="file",
                    )
                )

    def _aggregate_stage1(self, pairs: pd.DataFrame) -> pd.DataFrame:
        """Aggregate Stage 1 per-file EIC results into pair-level statistics."""
        stage1_result_dir = os.path.join(self.run_dir, "stage1_results")
        n_pairs = len(pairs)

        cor_sum = np.zeros(n_pairs, dtype=np.float64)
        cor_count = np.zeros(n_pairs, dtype=np.int64)
        cor_max = np.full(n_pairs, -np.inf, dtype=np.float64)
        cor_ge_peak = np.zeros(n_pairs, dtype=np.int64)

        for file_id in tqdm(range(self.n_files), desc="Aggregate S1", unit="file"):
            result_path = os.path.join(stage1_result_dir, f"file_{file_id:05d}.pkl")
            if not os.path.exists(result_path):
                continue

            with open(result_path, "rb") as f:
                current = pickle.load(f)

            if len(current) == 0:
                continue

            valid = (
                (current["pair_id"].values >= 0)
                & (current["pair_id"].values < n_pairs)
                & np.isfinite(current["cor"].values)
            )

            if not np.any(valid):
                continue

            ids = current["pair_id"].values[valid]
            values = current["cor"].values[valid]

            # Accumulate
            np.add.at(cor_sum, ids, values)
            np.add.at(cor_count, ids, 1)
            np.maximum.at(cor_max, ids, values)
            np.add.at(cor_ge_peak, ids, (values >= self.peakCOR).astype(np.int64))

            del current

        pairs = pairs.copy()
        pairs["stage1_valid_files"] = cor_count[pairs["pair_id"].values]
        pairs["stage1_mean_cor"] = np.where(
            pairs["stage1_valid_files"] > 0,
            cor_sum[pairs["pair_id"].values] / pairs["stage1_valid_files"],
            np.nan,
        )
        pairs["stage1_max_cor"] = np.where(
            pairs["stage1_valid_files"] > 0,
            cor_max[pairs["pair_id"].values],
            np.nan,
        )
        pairs["stage1_prop_ge_peak"] = np.where(
            pairs["stage1_valid_files"] > 0,
            cor_ge_peak[pairs["pair_id"].values] / pairs["stage1_valid_files"],
            np.nan,
        )

        enough_stage1 = pairs["stage1_valid_files"] >= self.stage1_min_valid

        if self.screenCOR is None:
            pass_regular = enough_stage1
        else:
            pass_regular = enough_stage1 & (
                (pairs["stage1_mean_cor"] >= self.screenCOR)
                | (pairs["stage1_max_cor"] >= self.peakCOR)
            )

        pass_sparse = np.zeros(n_pairs, dtype=bool)
        if self.stage1_fail_open_sparse:
            pass_sparse = (
                (pairs["n_copresent"] < self.stage1_min_valid)
                & (pairs["stage1_valid_files"] > 0)
            )

        pairs["stage1_pass"] = pass_regular | pass_sparse

        return pairs

    # ----------------------------------------------------------
    # Stage 2 – assignment matrix
    # ----------------------------------------------------------
    def _prepare_stage2_assignments(self, passed_pairs: pd.DataFrame, bm: np.memmap) -> dict:
        """Build file-backed Stage 2 pair-file assignment matrix."""
        cache_dir = self._make_dir(os.path.join(self.run_dir, "stage2_assignments"))
        mmap_path = os.path.join(cache_dir, "stage2_assignments.dat")
        meta_path = os.path.join(cache_dir, "stage2_assignment_metadata.pkl")

        pair_signature = (
            len(passed_pairs),
            int(passed_pairs["pair_id"].sum()) if len(passed_pairs) > 0 else 0,
            int(passed_pairs["precursor"].sum()) if len(passed_pairs) > 0 else 0,
            int(passed_pairs["fragment"].sum()) if len(passed_pairs) > 0 else 0,
        )

        if (
            not self.rebuild_stage2_assignments
            and not self.rebuild_stage1
            and not self.rebuild_candidates
            and os.path.exists(mmap_path)
            and os.path.exists(meta_path)
        ):
            with open(meta_path, "rb") as f:
                metadata = pickle.load(f)
            if metadata["pair_signature"] == pair_signature:
                print("Reusing the Stage-2 memory-mapped assignment matrix.")
                return {
                    "descriptor_path": mmap_path,
                    "counts": metadata["counts"],
                    "starts": metadata["starts"],
                    "total_assignments": metadata["total_assignments"],
                }

        # Clear old
        for p in [mmap_path, meta_path]:
            if os.path.exists(p):
                os.unlink(p)

        if len(passed_pairs) == 0:
            metadata = {
                "pair_signature": pair_signature,
                "counts": np.zeros(self.n_files, dtype=np.int64),
                "starts": np.zeros(self.n_files, dtype=np.int64),
                "total_assignments": 0,
            }
            self._atomic_save_pkl(metadata, meta_path)
            return {
                "descriptor_path": mmap_path,
                "counts": metadata["counts"],
                "starts": metadata["starts"],
                "total_assignments": 0,
            }

        pair_batches = self._split_indices(len(passed_pairs), self.stage2_pair_batch_size)

        print("Stage 2 assignment pass 1/2: counting all co-present pair-file combinations...")

        counts = np.zeros(self.n_files, dtype=np.int64)

        for batch_indices in tqdm(pair_batches, desc="S2 count", unit="batch"):
            rows = batch_indices.astype(int)
            precursor_ids = passed_pairs["precursor"].iloc[rows].values.astype(int)
            fragment_ids = passed_pairs["fragment"].iloc[rows].values.astype(int)

            precursor_mat = bm[precursor_ids, :].copy().astype(np.float64)
            fragment_mat = bm[fragment_ids, :].copy().astype(np.float64)

            common = (precursor_mat > 0) & (fragment_mat > 0)
            counts += common.sum(axis=0).astype(np.int64)

            del precursor_mat, fragment_mat, common

        total_assignments = int(counts.sum())

        if total_assignments <= 0:
            metadata = {
                "pair_signature": pair_signature,
                "counts": counts,
                "starts": np.zeros(self.n_files, dtype=np.int64),
                "total_assignments": 0,
            }
            self._atomic_save_pkl(metadata, meta_path)
            return {
                "descriptor_path": mmap_path,
                "counts": counts,
                "starts": metadata["starts"],
                "total_assignments": 0,
            }

        starts = np.concatenate([[0], np.cumsum(counts[:-1])])

        # Create memory-mapped assignment matrix
        assignment_matrix = np.memmap(
            mmap_path,
            dtype="int64",
            mode="w+",
            shape=(total_assignments, 3),
        )

        write_pointer = starts.copy()

        print("Stage 2 assignment pass 2/2: writing assignments grouped by mzXML file...")

        for batch_indices in tqdm(pair_batches, desc="S2 write", unit="batch"):
            rows = batch_indices.astype(int)
            precursor_ids = passed_pairs["precursor"].iloc[rows].values.astype(int)
            fragment_ids = passed_pairs["fragment"].iloc[rows].values.astype(int)
            pair_ids = passed_pairs["stage2_id"].iloc[rows].values.astype(int)

            precursor_mat = bm[precursor_ids, :].copy().astype(np.float64)
            fragment_mat = bm[fragment_ids, :].copy().astype(np.float64)

            common = (precursor_mat > 0) & (fragment_mat > 0)

            active_files = np.where(common.sum(axis=0) > 0)[0]

            for file_id in active_files:
                selected_rows = np.where(common[:, file_id])[0]
                n_rows = len(selected_rows)

                target_rows = np.arange(
                    write_pointer[file_id], write_pointer[file_id] + n_rows, dtype=np.int64
                )

                assignment_matrix[target_rows, 0] = pair_ids[selected_rows]
                assignment_matrix[target_rows, 1] = precursor_ids[selected_rows]
                assignment_matrix[target_rows, 2] = fragment_ids[selected_rows]

                write_pointer[file_id] += n_rows

            del precursor_mat, fragment_mat, common

        assignment_matrix.flush()

        metadata = {
            "pair_signature": pair_signature,
            "counts": counts,
            "starts": starts,
            "total_assignments": total_assignments,
        }
        self._atomic_save_pkl(metadata, meta_path)

        return {
            "descriptor_path": mmap_path,
            "counts": counts,
            "starts": starts,
            "total_assignments": total_assignments,
        }

    # ----------------------------------------------------------
    # Stage 2 – run and aggregate
    # ----------------------------------------------------------
    def _run_stage2_files(self, missing_file_ids: List[int], stage2_assignment: dict):
        """Run Stage 2 EIC calculation on all co-present files."""
        stage2_result_dir = self._make_dir(os.path.join(self.run_dir, "stage2_results"))

        counts = stage2_assignment["counts"]
        starts = stage2_assignment["starts"]
        descriptor_path = stage2_assignment["descriptor_path"]

        # Build argument tuples for module-level worker
        arg_tuples = []
        for file_id in missing_file_ids:
            result_path = os.path.join(stage2_result_dir, f"file_{file_id:05d}.pkl")
            if os.path.exists(result_path):
                continue  # Already done
            arg_tuples.append((
                file_id, self.files[file_id],
                self.mz.copy(), self.rt.copy(),
                self.mz_tol, self.rt_tol, self.block_width, self.smooth_level,
                result_path, int(counts[file_id]), descriptor_path, starts,
            ))

        if not arg_tuples:
            print("All Stage-2 file checkpoints already exist.")
            return

        if self.workers <= 1:
            for args in tqdm(arg_tuples, desc="Stage 2 EIC", unit="file"):
                _stage2_file_worker(args)
        else:
            with multiprocessing.Pool(processes=self.workers) as pool:
                list(
                    tqdm(
                        pool.imap_unordered(_stage2_file_worker, arg_tuples),
                        total=len(arg_tuples),
                        desc="Stage 2 EIC",
                        unit="file",
                    )
                )

    def _aggregate_stage2(self, passed_pairs: pd.DataFrame) -> pd.DataFrame:
        """Aggregate Stage 2 EIC results into final pair-level statistics."""
        stage2_result_dir = os.path.join(self.run_dir, "stage2_results")
        n_pairs = len(passed_pairs)

        cor_sum = np.zeros(n_pairs, dtype=np.float64)
        cor_count = np.zeros(n_pairs, dtype=np.int64)
        cor_ge_peak = np.zeros(n_pairs, dtype=np.int64)
        cor_min = np.full(n_pairs, np.inf, dtype=np.float64)
        cor_max = np.full(n_pairs, -np.inf, dtype=np.float64)

        for file_id in tqdm(range(self.n_files), desc="Aggregate S2", unit="file"):
            result_path = os.path.join(stage2_result_dir, f"file_{file_id:05d}.pkl")
            if not os.path.exists(result_path):
                continue

            with open(result_path, "rb") as f:
                current = pickle.load(f)

            if len(current) == 0:
                continue

            valid = (
                (current["pair_id"].values >= 0)
                & (current["pair_id"].values < n_pairs)
                & np.isfinite(current["cor"].values)
            )

            if not np.any(valid):
                continue

            ids = current["pair_id"].values[valid]
            values = current["cor"].values[valid]

            np.add.at(cor_sum, ids, values)
            np.add.at(cor_count, ids, 1)
            np.add.at(cor_ge_peak, ids, (values >= self.peakCOR).astype(np.int64))
            np.minimum.at(cor_min, ids, values)
            np.maximum.at(cor_max, ids, values)

            del current

        passed_pairs = passed_pairs.copy()
        s2_ids = passed_pairs["stage2_id"].values

        passed_pairs["final_valid_files"] = cor_count[s2_ids]
        passed_pairs["final_mean_cor"] = np.where(
            passed_pairs["final_valid_files"] > 0,
            cor_sum[s2_ids] / passed_pairs["final_valid_files"],
            np.nan,
        )
        passed_pairs["final_prop_ge_peak"] = np.where(
            passed_pairs["final_valid_files"] > 0,
            cor_ge_peak[s2_ids] / passed_pairs["final_valid_files"],
            np.nan,
        )
        passed_pairs["final_min_cor"] = np.where(
            passed_pairs["final_valid_files"] > 0,
            cor_min[s2_ids],
            np.nan,
        )
        passed_pairs["final_max_cor"] = np.where(
            passed_pairs["final_valid_files"] > 0,
            cor_max[s2_ids],
            np.nan,
        )

        passed_pairs["final_pass"] = (
            (passed_pairs["final_valid_files"] >= self.min_final_valid)
            & np.isfinite(passed_pairs["final_mean_cor"])
            & (passed_pairs["final_mean_cor"] >= self.peakCOR)
            & (passed_pairs["final_prop_ge_peak"] >= self.final_min_proportion)
        )

        return passed_pairs

    # ----------------------------------------------------------
    # Optional group builder
    # ----------------------------------------------------------
    def _build_groups(self, hits: pd.DataFrame) -> dict:
        """Build list-style output groups from final hits."""
        if len(hits) == 0:
            return {}

        ft = self.featureTable
        mz_col = self.mz_col
        rt_col = self.rt_col

        groups = {}
        if self._feature_ids is not None:
            feature_names = self._feature_ids
        else:
            feature_names = [str(i) for i in range(len(ft))]

        for precursor_id, grp in hits.groupby("precursor"):
            precursor_id = int(precursor_id)
            fragment_ids = grp["fragment"].values.astype(int)

            # Build sub-table
            rows = [precursor_id] + list(fragment_ids)
            sub = ft.iloc[rows].copy()

            sub["ppcor"] = [0.0] + list(grp["final_mean_cor"].values)
            sub["valid_files"] = [np.nan] + list(grp["final_valid_files"].values)
            sub["prop_cor_ge_threshold"] = [np.nan] + list(grp["final_prop_ge_peak"].values)
            sub["ISF_level"] = ["Precursor"] + ["Level_3"] * len(fragment_ids)

            # Sort by m/z descending
            sub = sub.sort_values(mz_col, ascending=False)
            sub = sub.reset_index(drop=True)

            group_name = (
                f"{feature_names[precursor_id]}"
                f"_{ft.iloc[precursor_id][mz_col]:.2f}"
                f"_{ft.iloc[precursor_id][rt_col]:.0f}"
            )
            groups[group_name] = sub

        return groups

    # ----------------------------------------------------------
    # Main workflow
    # ----------------------------------------------------------
    def run(self) -> dict:
        """Execute the full ISF Level 3 two-stage workflow.

        Returns
        -------
        dict with keys:
            hits : pd.DataFrame
                Final ISF Level 3 pairs passing all filters.
            groups : dict or None
                Per-precursor group tables (if build_groups=True).
            run_dir : str
                Path to the run checkpoint directory.
            hits_csv : str
                Path to the CSV output file.
            parameters : dict
                All analysis parameters for reproducibility.
        """
        self._check_packages()

        # Validate featureTable columns
        if self.mz_col not in self.featureTable.columns:
            raise ValueError(f"featureTable must contain column: {self.mz_col}")
        if self.rt_col not in self.featureTable.columns:
            raise ValueError(f"featureTable must contain column: {self.rt_col}")

        # Detect feature_id column for output mapping
        self._feature_id_col = None
        self._feature_ids = None
        if "feature_id" in self.featureTable.columns:
            self._feature_id_col = "feature_id"
            self._feature_ids = self.featureTable["feature_id"].astype(str).tolist()
            print(f"检测到 feature_id 列，将在结果中映射真实 feature_id。")

        # Resolve file paths
        self.files = []
        for f in self.MS1_files_raw:
            f = str(f)
            if not os.path.exists(f):
                f2 = os.path.join(self.MS1directory, f)
                if os.path.exists(f2):
                    self.files.append(os.path.normpath(f2))
                    continue
                raise FileNotFoundError(f"mzXML file not found: {f}")
            else:
                self.files.append(os.path.normpath(f))

        self.n_files = len(self.files)
        self.n_features = len(self.featureTable)

        # Resolve intensity columns
        if self.intensity_cols_in is None:
            # Default: columns 5:(4 + n_files) (1-indexed R convention)
            # In Python, DataFrame is 0-indexed; assume columns after mz/rt/others
            # Use the last n_files columns as intensity columns
            self.intensity_cols = list(
                range(max(0, self.featureTable.shape[1] - self.n_files), self.featureTable.shape[1])
            )
        elif isinstance(self.intensity_cols_in[0], str):
            self.intensity_cols = [
                list(self.featureTable.columns).index(c) for c in self.intensity_cols_in
            ]
        else:
            self.intensity_cols = list(self.intensity_cols_in)

        if len(self.intensity_cols) != self.n_files:
            raise ValueError(
                "intensity_cols must contain exactly one intensity column "
                "for every mzXML file, in the same order as MS1_files."
            )

        # Extract m/z and RT
        self.mz = pd.to_numeric(self.featureTable[self.mz_col], errors="coerce").to_numpy(dtype=np.float64)
        self.rt = pd.to_numeric(self.featureTable[self.rt_col], errors="coerce").to_numpy(dtype=np.float64)

        if not np.all(np.isfinite(self.mz)) or not np.all(np.isfinite(self.rt)):
            raise ValueError("The mz and rt columns must contain finite numeric values.")

        # Validate parameters
        if self.min_copresent_files < 1 or self.stage1_files_per_pair < 1 or self.stage1_min_valid < 1 or self.min_final_valid < 1:
            raise ValueError("Presence and valid-file parameters must be at least 1.")
        if self.final_min_proportion < 0 or self.final_min_proportion > 1:
            raise ValueError("final_min_proportion must be between 0 and 1.")

        # Clamp workers
        physical_cores = multiprocessing.cpu_count()
        self.workers = max(1, min(self.workers, physical_cores, self.n_files))

        # Clamp chunk sizes
        self.candidate_feature_chunk = max(50, self.candidate_feature_chunk)
        self.candidate_batch_size = max(20, self.candidate_batch_size)
        self.stage2_pair_batch_size = max(50, self.stage2_pair_batch_size)
        self.prefilter_samples = min(self.n_files, max(2, self.prefilter_samples))

        # Set up directories
        if self.work_dir_in is None:
            self.work_dir = self._make_dir(
                os.path.join(self.MS1directory, "ISFlevel3_two_stage_work")
            )
        else:
            self.work_dir = self._make_dir(self.work_dir_in)

        # Compute signatures
        self.data_signature = self._data_signature()
        self.run_signature = self._run_signature()

        if self.batch_label:
            self.run_dir = self._make_dir(
                os.path.join(self.work_dir, f"run_{self.batch_label}")
            )
        else:
            self.run_dir = self._make_dir(
                os.path.join(self.work_dir, f"run_{self.run_signature}")
            )

        # Prefilter file IDs
        self._prefilter_file_ids = np.unique(
            np.round(
                np.linspace(0, self.n_files - 1, self.prefilter_samples)
            ).astype(np.int64)
        )

        # Paths
        combined_pairs_path = os.path.join(self.run_dir, "candidate_pairs.pkl")
        combined_assignments_path = os.path.join(self.run_dir, "stage1_representative_assignments.pkl")
        stage1_screened_path = os.path.join(self.run_dir, "stage1_screened_pairs.pkl")
        stage2_all_pairs_path = os.path.join(self.run_dir, "stage2_all_pairs.pkl")
        hits_pkl_path = os.path.join(self.run_dir, "ISF_Level3_hits.pkl")
        hits_csv_path = os.path.join(self.run_dir, "ISF_Level3_hits.csv")
        stage1_result_dir = os.path.join(self.run_dir, "stage1_results")

        # Handle rebuild cascades
        if self.rebuild_candidates:
            for d in ["candidate_chunks", "stage1_assignments", "stage1_results",
                       "stage2_assignments", "stage2_results"]:
                p = os.path.join(self.run_dir, d)
                if os.path.exists(p):
                    self._clear_dir(p)
            for p in [combined_pairs_path, combined_assignments_path,
                       stage1_screened_path, stage2_all_pairs_path,
                       hits_pkl_path, hits_csv_path]:
                if os.path.exists(p):
                    os.unlink(p)
        elif self.rebuild_stage1:
            for d in ["stage1_results", "stage2_assignments", "stage2_results"]:
                p = os.path.join(self.run_dir, d)
                if os.path.exists(p):
                    self._clear_dir(p)
            for p in [stage1_screened_path, stage2_all_pairs_path,
                       hits_pkl_path, hits_csv_path]:
                if os.path.exists(p):
                    os.unlink(p)
        elif self.rebuild_stage2_assignments:
            for d in ["stage2_assignments", "stage2_results"]:
                p = os.path.join(self.run_dir, d)
                if os.path.exists(p):
                    self._clear_dir(p)
            for p in [stage2_all_pairs_path, hits_pkl_path, hits_csv_path]:
                if os.path.exists(p):
                    os.unlink(p)
        elif self.rebuild_stage2:
            p = os.path.join(self.run_dir, "stage2_results")
            if os.path.exists(p):
                self._clear_dir(p)
            for p in [stage2_all_pairs_path, hits_pkl_path, hits_csv_path]:
                if os.path.exists(p):
                    os.unlink(p)

        print("")
        print("=" * 60)
        print("ISF Level 3 two-stage analysis (Python)")
        print(f"Features: {self.n_features:,}; mzXML files: {self.n_files:,}; workers: {self.workers}")
        print(f"Run directory: {self.run_dir}")
        print("=" * 60)

        # ------------------------------------------------------
        # Stage 1/7 – Intensity matrix
        # ------------------------------------------------------
        print("\nStage 1/7 - Preparing intensity matrix")
        bm = self._prepare_intensity_matrix()

        # ------------------------------------------------------
        # Stage 2/7 – Candidate generation
        # ------------------------------------------------------
        print("\nStage 2/7 - Generating RT/mass candidates")
        candidate_result = self._prepare_candidates(bm)

        pairs = candidate_result["pairs"]
        stage1_assignments = candidate_result["assignments"]
        del candidate_result

        if len(pairs) == 0:
            print("WARNING: No candidate pairs passed the RT, mass-loss, and co-presence requirements.")
            empty_hits = self._empty_hits()
            empty_hits.to_csv(hits_csv_path, index=False)
            self._atomic_save_pkl(empty_hits, hits_pkl_path)
            return {
                "hits": empty_hits,
                "groups": None,
                "run_dir": self.run_dir,
                "hits_csv": hits_csv_path,
                "hits_pkl": hits_pkl_path,
                "candidate_pairs_file": combined_pairs_path,
                "stage1_pairs_file": stage1_screened_path,
                "stage2_pairs_file": stage2_all_pairs_path,
                "parameters": dict(
                    peakCOR=self.peakCOR, screenCOR=self.screenCOR,
                    loss=self.loss, mz_tol=self.mz_tol, rt_tol=self.rt_tol,
                    candidate_rt=self.candidate_rt,
                    min_copresent_files=self.min_copresent_files,
                    min_final_valid=self.min_final_valid,
                    run_signature=self.run_signature,
                ),
            }

        print(f"Candidate pairs: {len(pairs):,}")

        # ------------------------------------------------------
        # Stage 3/7 – Stage 1 EIC screen
        # ------------------------------------------------------
        print("\nStage 3/7 - Stage-1 representative-file EIC screen")
        self._write_assignments_by_file(stage1_assignments)
        del stage1_assignments

        stage1_result_dir_full = os.path.join(self.run_dir, "stage1_results")
        self._make_dir(stage1_result_dir_full)

        stage1_missing_files = [
            fid for fid in range(self.n_files)
            if not os.path.exists(os.path.join(stage1_result_dir_full, f"file_{fid:05d}.pkl"))
        ]

        if stage1_missing_files:
            self._run_stage1_files(stage1_missing_files)
        else:
            print("All Stage-1 file checkpoints already exist.")

        # ------------------------------------------------------
        # Stage 4/7 – Stage 1 aggregation
        # ------------------------------------------------------
        print("\nStage 4/7 - Aggregating Stage-1 results")

        if not os.path.exists(stage1_screened_path) or self.rebuild_stage1 or self.rebuild_candidates:
            pairs = self._aggregate_stage1(pairs)
            self._atomic_save_pkl(pairs, stage1_screened_path)
        else:
            with open(stage1_screened_path, "rb") as f:
                pairs = pickle.load(f)

        passed_pairs = pairs[pairs["stage1_pass"] == True].copy()  # noqa: E712
        print(f"Pairs passing Stage 1: {len(passed_pairs):,} / {len(pairs):,}")
        del pairs

        if len(passed_pairs) == 0:
            print("WARNING: No candidate pairs passed the Stage-1 EIC screen.")
            empty_hits = self._empty_hits()
            empty_hits.to_csv(hits_csv_path, index=False)
            self._atomic_save_pkl(empty_hits, hits_pkl_path)
            return {
                "hits": empty_hits,
                "groups": None,
                "run_dir": self.run_dir,
                "hits_csv": hits_csv_path,
                "hits_pkl": hits_pkl_path,
                "candidate_pairs_file": combined_pairs_path,
                "stage1_pairs_file": stage1_screened_path,
                "stage2_pairs_file": stage2_all_pairs_path,
                "parameters": dict(
                    peakCOR=self.peakCOR, screenCOR=self.screenCOR,
                    loss=self.loss, mz_tol=self.mz_tol, rt_tol=self.rt_tol,
                    candidate_rt=self.candidate_rt,
                    min_copresent_files=self.min_copresent_files,
                    min_final_valid=self.min_final_valid,
                    run_signature=self.run_signature,
                ),
            }

        passed_pairs["stage2_id"] = np.arange(len(passed_pairs), dtype=np.int64)

        # ------------------------------------------------------
        # Stage 5/7 – Stage 2 assignments
        # ------------------------------------------------------
        print("\nStage 5/7 - Building all-co-present-file assignments")
        stage2_assignment = self._prepare_stage2_assignments(passed_pairs, bm)
        print(f"Stage-2 pair-file assignments: {stage2_assignment['total_assignments']:,}")

        # ------------------------------------------------------
        # Stage 6/7 – Stage 2 EIC calculation
        # ------------------------------------------------------
        print("\nStage 6/7 - Stage-2 EIC calculation in every co-present file")
        stage2_result_dir_full = os.path.join(self.run_dir, "stage2_results")
        self._make_dir(stage2_result_dir_full)

        stage2_missing_files = [
            fid for fid in range(self.n_files)
            if not os.path.exists(os.path.join(stage2_result_dir_full, f"file_{fid:05d}.pkl"))
        ]

        if stage2_missing_files:
            self._run_stage2_files(stage2_missing_files, stage2_assignment)
        else:
            print("All Stage-2 file checkpoints already exist.")

        del stage2_assignment

        # ------------------------------------------------------
        # Stage 7/7 – Final aggregation
        # ------------------------------------------------------
        print("\nStage 7/7 - Final aggregation and peakCOR decision")

        if (
            not os.path.exists(stage2_all_pairs_path)
            or self.rebuild_stage2
            or self.rebuild_stage2_assignments
            or self.rebuild_stage1
            or self.rebuild_candidates
        ):
            passed_pairs = self._aggregate_stage2(passed_pairs)
            self._atomic_save_pkl(passed_pairs, stage2_all_pairs_path)
        else:
            with open(stage2_all_pairs_path, "rb") as f:
                passed_pairs = pickle.load(f)

        hits = passed_pairs[passed_pairs["final_pass"] == True].copy()  # noqa: E712
        hits = hits.sort_values(["precursor", "final_mean_cor", "fragment"],
                                 ascending=[True, False, True]).reset_index(drop=True)

        # 如果检测到 feature_id 列，映射到输出结果中
        if self._feature_ids is not None:
            hits["precursor_feature_id"] = [
                self._feature_ids[int(p)] if 0 <= int(p) < len(self._feature_ids) else str(p)
                for p in hits["precursor"]
            ]
            hits["fragment_feature_id"] = [
                self._feature_ids[int(f)] if 0 <= int(f) < len(self._feature_ids) else str(f)
                for f in hits["fragment"]
            ]

        self._atomic_save_pkl(hits, hits_pkl_path)
        hits.to_csv(hits_csv_path, index=False)

        groups = None
        if self.build_groups:
            groups = self._build_groups(hits)

        print("")
        print("=" * 60)
        print("Finished.")
        print(f"Final Level-3 ISF pairs: {len(hits):,}")
        print(f"Results CSV: {hits_csv_path}")
        print(f"Checkpoint directory: {self.run_dir}")
        print("=" * 60)

        return {
            "hits": hits,
            "groups": groups,
            "run_dir": self.run_dir,
            "hits_csv": hits_csv_path,
            "hits_pkl": hits_pkl_path,
            "candidate_pairs_file": combined_pairs_path,
            "stage1_pairs_file": stage1_screened_path,
            "stage2_pairs_file": stage2_all_pairs_path,
            "parameters": dict(
                peakCOR=self.peakCOR,
                screenCOR=self.screenCOR,
                loss=self.loss,
                mz_tol=self.mz_tol,
                rt_tol=self.rt_tol,
                candidate_rt=self.candidate_rt,
                min_copresent_files=self.min_copresent_files,
                stage1_files_per_pair=self.stage1_files_per_pair,
                stage1_min_valid=self.stage1_min_valid,
                stage1_fail_open_sparse=self.stage1_fail_open_sparse,
                prefilter_cor=self.prefilter_cor,
                prefilter_samples=self.prefilter_samples,
                min_final_valid=self.min_final_valid,
                final_min_proportion=self.final_min_proportion,
                candidate_feature_chunk=self.candidate_feature_chunk,
                candidate_batch_size=self.candidate_batch_size,
                stage2_pair_batch_size=self.stage2_pair_batch_size,
                block_width=self.block_width,
                smooth_level=self.smooth_level,
                workers=self.workers,
                data_signature=self.data_signature,
                run_signature=self.run_signature,
            ),
        }


# ============================================================
# Convenience function (R-style API)
# ============================================================


def ISFlevel3_two_stage(
    MS1directory: str,
    MS1_files: List[str],
    featureTable: pd.DataFrame,
    mz_col: str = "mz",
    rt_col: str = "rt",
    intensity_cols: Optional[Union[List[int], List[str]]] = None,
    peakCOR: float = 0.80,
    loss: float = 10.0,
    mz_tol: float = 0.01,
    rt_tol: float = 30.0,
    candidate_rt: float = 10.0,
    min_copresent_files: int = 3,
    stage1_files_per_pair: int = 5,
    screenCOR: Optional[float] = 0.65,
    stage1_min_valid: int = 2,
    stage1_fail_open_sparse: bool = True,
    prefilter_cor: Optional[float] = None,
    prefilter_samples: int = 64,
    min_final_valid: int = 3,
    final_min_proportion: float = 0.0,
    candidate_feature_chunk: int = 500,
    candidate_batch_size: int = 250,
    stage2_pair_batch_size: int = 1000,
    block_width: float = 60.0,
    smooth_level: int = 2,
    workers: int = 2,
    work_dir: Optional[str] = None,
    run_id: str = "default",
    batch_label: str = "",
    rebuild_intensity_cache: bool = False,
    rebuild_candidates: bool = False,
    rebuild_stage1: bool = False,
    rebuild_stage2_assignments: bool = False,
    rebuild_stage2: bool = False,
    build_groups: bool = False,
) -> dict:
    """R-style convenience function. See ISFLevel3TwoStage for full documentation."""
    analyzer = ISFLevel3TwoStage(
        MS1directory=MS1directory,
        MS1_files=MS1_files,
        featureTable=featureTable,
        mz_col=mz_col,
        rt_col=rt_col,
        intensity_cols=intensity_cols,
        peakCOR=peakCOR,
        loss=loss,
        mz_tol=mz_tol,
        rt_tol=rt_tol,
        candidate_rt=candidate_rt,
        min_copresent_files=min_copresent_files,
        stage1_files_per_pair=stage1_files_per_pair,
        screenCOR=screenCOR,
        stage1_min_valid=stage1_min_valid,
        stage1_fail_open_sparse=stage1_fail_open_sparse,
        prefilter_cor=prefilter_cor,
        prefilter_samples=prefilter_samples,
        min_final_valid=min_final_valid,
        final_min_proportion=final_min_proportion,
        candidate_feature_chunk=candidate_feature_chunk,
        candidate_batch_size=candidate_batch_size,
        stage2_pair_batch_size=stage2_pair_batch_size,
        block_width=block_width,
        smooth_level=smooth_level,
        workers=workers,
        work_dir=work_dir,
        run_id=run_id,
        batch_label=batch_label,
        rebuild_intensity_cache=rebuild_intensity_cache,
        rebuild_candidates=rebuild_candidates,
        rebuild_stage1=rebuild_stage1,
        rebuild_stage2_assignments=rebuild_stage2_assignments,
        rebuild_stage2=rebuild_stage2,
        build_groups=build_groups,
    )
    return analyzer.run()


# ============================================================
# Example usage
# ============================================================
if __name__ == "__main__":
    # Example:
    # MS1directory = "D:/MS1_mzXML"
    # MS1_files = [f for f in os.listdir(MS1directory) if f.endswith(".mzXML")]
    #
    # featureTable = pd.read_csv("feature_table.csv")
    #
    # result = ISFlevel3_two_stage(
    #     MS1directory=MS1directory,
    #     MS1_files=MS1_files,
    #     featureTable=featureTable,
    #     intensity_cols=list(range(4, 4 + len(MS1_files))),
    #     peakCOR=0.80,
    #     loss=10,
    #     mz_tol=0.01,
    #     rt_tol=30,
    #     candidate_rt=10,
    #     min_copresent_files=3,
    #     stage1_files_per_pair=5,
    #     screenCOR=0.65,
    #     stage1_min_valid=2,
    #     stage1_fail_open_sparse=True,
    #     prefilter_cor=None,
    #     prefilter_samples=64,
    #     min_final_valid=3,
    #     final_min_proportion=0,
    #     candidate_feature_chunk=500,
    #     candidate_batch_size=250,
    #     stage2_pair_batch_size=1000,
    #     block_width=60,
    #     smooth_level=2,
    #     workers=2,
    #     work_dir="D:/ISFlevel3_two_stage_work",
    #     run_id="dataset_001",
    #     build_groups=False,
    # )
    #
    # print(result["hits"])
    # print(result["hits_csv"])
    print("ISF Level 3 Python — import and use ISFLevel3TwoStage or ISFlevel3_two_stage().")
    print("See docstrings and example in __main__ for usage.")
