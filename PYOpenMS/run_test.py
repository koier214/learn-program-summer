#!/usr/bin/env python3
"""
启动脚本：对 test_data 中的两个 mzML 文件运行 ISF Level 3 分析
"""
import os
import sys
import pandas as pd

# 把当前目录加到 path，确保能 import isf_level3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from isf_level3 import ISFlevel3_two_stage

if __name__ == '__main__':
    # ---- 1. 设置路径 ----
    test_dir = os.path.join(os.path.dirname(__file__), "test_data")

    MS1directory = test_dir
    MS1_files = [
        "240902MBSW004_H01_PP.mzML",
        "240902RBSW003_H01_PP.mzML",
    ]

    csv_path = os.path.join(test_dir, "feature_matrix_median_normalized.csv")

    # ---- 2. 读取特征表 ----
    print(f"Reading feature table: {csv_path}")
    featureTable = pd.read_csv(csv_path, encoding="utf-8-sig")

    print(f"Features: {featureTable.shape[0]:,}")
    print(f"Columns: {list(featureTable.columns)}")
    print(f"mzML files: {len(MS1_files)}")

    # ---- 3. 确认列对应关系（根据文件名匹配强度列） ----
    intensity_cols = []
    for fname in MS1_files:
        basename = os.path.splitext(fname)[0]  # 去掉 .mzML 后缀
        matched = [c for c in featureTable.columns if basename in c]
        if matched:
            col_idx = featureTable.columns.get_loc(matched[0])
            intensity_cols.append(col_idx)
        else:
            raise ValueError(f"未找到与 {fname} 匹配的强度列")

    if len(intensity_cols) != len(MS1_files):
        raise ValueError(
            f"intensity_cols 数量 ({len(intensity_cols)}) 与 mzML 文件数 ({len(MS1_files)}) 不匹配"
        )

    print("\n强度列对应关系:")
    for i, (fname, col_idx) in enumerate(zip(MS1_files, intensity_cols)):
        print(f"  [{i}] {fname}  ←  {featureTable.columns[col_idx]}")

    print(f"\n特征表前 5 行（mz, rt, intensity 列）:")
    cols_preview = ["mz", "rt_seconds"] + [featureTable.columns[c] for c in intensity_cols]
    print(featureTable[cols_preview].head().to_string())

    # ---- 4. 运行 ISF 分析 ----
    # 因为只有 2 个文件，降低 presence 要求到 1
    print("\n" + "=" * 60)
    print("开始 ISF Level 3 分析...")
    print("=" * 60)

    result = ISFlevel3_two_stage(
        MS1directory=MS1directory,
        MS1_files=MS1_files,
        featureTable=featureTable,
        mz_col="mz",
        rt_col="rt_seconds",
        intensity_cols=intensity_cols,

        # 科学阈值
        peakCOR=0.80,
        loss=10.0,
        mz_tol=0.01,
        rt_tol=30.0,
        candidate_rt=10.0,

        # 只有 2 个文件，降低要求
        min_copresent_files=1,
        stage1_files_per_pair=2,
        screenCOR=0.65,
        stage1_min_valid=1,
        stage1_fail_open_sparse=True,

        # Stage 2
        min_final_valid=1,
        final_min_proportion=0.0,

        # 性能参数
        candidate_feature_chunk=500,
        candidate_batch_size=250,
        stage2_pair_batch_size=1000,
        block_width=60,
        smooth_level=2,
        workers=1,  # 调试阶段用 1 进程

        # 缓存
        work_dir=os.path.join(test_dir, "ISF_work"),
        run_id="test_2files",
        rebuild_intensity_cache=False,

        # 输出
        build_groups=False,
    )

    # ---- 5. 显示结果 ----
    print("\n" + "=" * 60)
    print("分析完成!")
    print("=" * 60)

    hits = result["hits"]
    print(f"\n最终 ISF Level 3 配对数量: {len(hits):,}")

    if len(hits) > 0:
        print(f"\n前 10 条结果预览:")
        cols_show = [
            "precursor", "fragment",
            "precursor_feature_id", "fragment_feature_id",
            "n_copresent", "final_mean_cor", "final_valid_files",
        ]
        # 只显示存在的列
        cols_show = [c for c in cols_show if c in hits.columns]
        print(hits[cols_show].head(10).to_string())

        print(f"\n完整结果保存在: {result['hits_csv']}")
    else:
        print("\n没有找到满足条件的 ISF 配对。")
        print("可以尝试降低 peakCOR 或 screenCOR 阈值。")

    print(f"\n断点续跑目录: {result['run_dir']}")
