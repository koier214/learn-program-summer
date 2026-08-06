#!/usr/bin/env python3
"""从 ISF hits 结果生成带 spec 和 intensity 列的特征表 CSV（格式对齐示例 xlsx）"""
import os
import numpy as np
import pandas as pd
import glob

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    test_dir = os.path.join(base_dir, "test_data")

    # 1. 读取特征表
    csv_path = os.path.join(test_dir, "feature_matrix_median_normalized.csv")
    print(f"读取特征表: {csv_path}")
    ft = pd.read_csv(csv_path, encoding="utf-8-sig")
    print(f"  特征数: {ft.shape[0]:,}")

    # 识别强度列（样品列）
    meta_cols = ["feature_id", "mz", "rt_seconds", "rt_minutes"]
    intensity_sample_cols = [c for c in ft.columns if c not in meta_cols]

    # 2. feature_id → mz, 以及 feature_id → 平均强度
    id_to_mz = dict(zip(ft["feature_id"], ft["mz"]))
    id_to_intensity = dict(zip(
        ft["feature_id"],
        ft[intensity_sample_cols].mean(axis=1).values
    ))
    # NaN 均值填 0
    for fid in id_to_intensity:
        if np.isnan(id_to_intensity[fid]):
            id_to_intensity[fid] = 0.0

    # 3. 找最新 ISF hits
    work_dir = os.path.join(test_dir, "ISF_work")
    hits_files = glob.glob(os.path.join(work_dir, "run_*", "ISF_Level3_hits.csv"))
    if not hits_files:
        print("错误: 找不到 ISF_Level3_hits.csv")
        exit(1)
    hits_path = max(hits_files, key=os.path.getmtime)
    print(f"读取 hits: {hits_path}")
    hits = pd.read_csv(hits_path)
    print(f"  ISF 配对: {hits.shape[0]:,}")

    # 4. 按 precursor 聚合碎片 (mz, intensity)
    #    spec_map: precursor_fid → {frag_mz: frag_intensity}
    spec_map = {}
    for _, row in hits.iterrows():
        p_fid = row["precursor_feature_id"]
        f_fid = row["fragment_feature_id"]
        if f_fid not in id_to_mz:
            continue
        fmz = id_to_mz[f_fid]
        fint = id_to_intensity.get(f_fid, 0.0)
        spec_map.setdefault(p_fid, {})[fmz] = fint

    print(f"  有碎片的 precursor: {len(spec_map):,}")

    # 5. 添加 spec 和 intensity 列（按 m/z 升序排列，两列一一对应）
    spec_col = []
    intensity_col = []
    for _, row in ft.iterrows():
        fid = row["feature_id"]
        if fid in spec_map:
            frags = spec_map[fid]  # {mz: intensity}
            sorted_mz = sorted(frags.keys())
            spec_col.append(", ".join(str(mz) for mz in sorted_mz))
            intensity_col.append(", ".join(str(frags[mz]) for mz in sorted_mz))
        else:
            spec_col.append("")
            intensity_col.append("")

    ft["spec"] = spec_col
    # intensity 插在 mz 和 rt_seconds 之间
    mz_idx = ft.columns.get_loc("mz")
    ft.insert(mz_idx + 1, "intensity", intensity_col)

    # 6. 保存
    out_path = os.path.join(test_dir, "feature_matrix_with_spec_v2.csv")
    ft.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"已保存: {out_path}")
    print(f"  有碎片特征: {(ft['spec'] != '').sum():,} / {len(ft):,}")
