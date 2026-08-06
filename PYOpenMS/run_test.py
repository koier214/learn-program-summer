#!/usr/bin/env python3
"""
启动脚本：对 test_data 中的两个 mzML 文件运行 ISF Level 3 分析
"""
import os
import sys
import numpy as np
import pandas as pd

# 把当前目录加到 path，确保能 import isf_level3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from isf_level3 import ISFlevel3_two_stage

if __name__ == '__main__':
    # ==================================================================
    # 用户配置区域 —— 粘贴你的文件路径到这里
    # ==================================================================

    # --- CSV 特征表路径 ---
    # 特征提取后生成的特征矩阵，包含 m/z、RT、每个样品的强度列
    csv_path = r"D:\learn_program_summer\PYOpenMS\test_data\feature_matrix_median_normalized.csv"

    # --- mzML 文件目录 ---
    # 程序会自动扫描该目录下所有 .mzML 和 .mzXML 文件
    MS1directory = r"D:\learn_program_summer\PYOpenMS\test_data"

    # --- 输出目录 ---
    # ISF 分析结果、memmap 缓存、断点续跑文件都放在这里
    work_dir = r"D:\learn_program_summer\output"

    # --- 运行标识 ---
    # 留空则自动使用递增编号（01, 02, 03...）作为批次目录名
    # 也可手动填写如 "batch_diabetes" 作为自定义目录名
    run_id = ""

    # --- 跑完后自动清理中间文件 ---
    # True：仅保留最终 CSV（hits + 带 spec 的特征表），删除候选对、Stage1/2 缓存等 ~8GB 临时文件
    # False：保留所有文件，方便断点续跑或调试
    cleanup_intermediates = True

    # ==================================================================
    # 配置结束，以下为自动逻辑，一般不需要修改
    # ==================================================================

    import glob
    import re

    # 自动扫描 work_dir 中已有的编号目录，获取下一个编号
    def _next_batch_label(work_dir):
        if not os.path.exists(work_dir):
            return "01"
        existing = set()
        for name in os.listdir(work_dir):
            m = re.match(r'^(?:intensity|run)_(\d{2})$', name)
            if m:
                existing.add(int(m.group(1)))
        if not existing:
            return "01"
        return f"{max(existing) + 1:02d}"

    def _dir_size(path):
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
        return total

    # run_id 为空时自动用递增编号；否则用 run_id 的值
    if run_id:
        batch_label = run_id
    else:
        batch_label = _next_batch_label(work_dir)

    # 自动扫描 mzML 目录下所有质谱文件
    MS1_files = sorted(
        glob.glob(os.path.join(MS1directory, "*.mzML"))
        + glob.glob(os.path.join(MS1directory, "*.mzXML"))
    )
    # 只保留文件名（代码内部会根据 MS1directory 拼出完整路径）
    MS1_files = [os.path.basename(f) for f in MS1_files]

    if len(MS1_files) == 0:
        raise FileNotFoundError(
            f"在 {MS1directory} 中未找到任何 .mzML 或 .mzXML 文件"
        )

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
        # ------------------------------------------------------------------
        # prefilter_cor（跨样品强度预筛选）—— 已注释，多文件批次时取消注释
        # ------------------------------------------------------------------
        # 作用：从当前批次的所有样品中均匀抽样 64 个，计算 precursor 与 fragment
        #       在这 64 个样品中 log1p 强度的 Pearson 相关。相关 < 阈值的配对直接
        #       丢弃，不进 Stage 1 EIC 筛选。
        #
        # 使用场景（需同时满足）：
        #   1. 当前批次 ≥ 50 个 mzML 文件（保证抽样有统计意义）
        #   2. 特征表有对应的 ≥ 50 个强度列
        #
        # 限制/禁用场景：
        #   - 批次文件数 < 10：prefilter_samples 被压缩到文件数，Pearson 相关退化
        #     （2 个样品时只能算 +1/-1/NaN），会大量误杀真实 ISF 对 → 不开
        #   - 10~49 个文件：开了有一定参考价值但不稳定，建议谨慎评估
        #   - 正式批次 50+ 文件：推荐启用，可削减 40-70% 候选对
        #
        # 阈值选择：
        #   - 0.35：保守（仅杀负相关和零相关，几乎不漏真 ISF）
        #   - 0.50：中等（削减更多候选对，可能杀弱共变的边缘 ISF）
        #   - 不设过高（>0.6），否则可能误杀真正的弱共变 ISF
        #
        # 与正式筛选的关系：
        #   - prefilter_cor 是"快速初筛"，只看 64 个样品的强度共变，不碰 mzML
        #   - Stage 1/Stage 2 的 EIC Pearson 相关是"正式筛选"，看色谱峰形状
        #   - 最终 ISF 判决在 Stage 2 peakCOR，prefilter 只负责减少工作量
        # ------------------------------------------------------------------
        # prefilter_cor=0.35,
        screenCOR=0.65,
        stage1_min_valid=1,
        stage1_fail_open_sparse=True,

        # Stage 2
        min_final_valid=1,
        final_min_proportion=0.0,

        # 性能参数
        candidate_feature_chunk=500,
        candidate_batch_size=2000,
        stage2_pair_batch_size=1000,
        block_width=60,
        smooth_level=2,
        workers=2,  # 2 个 mzML 同时处理

        # 缓存
        work_dir=work_dir,
        run_id=run_id,
        batch_label=batch_label,
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
        cols_show = [c for c in cols_show if c in hits.columns]
        print(hits[cols_show].head(10).to_string())

        # ---- 6. 生成带 spec 列的特征表 ----
        print("\n" + "=" * 60)
        print("生成带 spec 列的特征表...")
        print("=" * 60)

        # 识别强度列（排除元数据列）
        meta_cols = ["feature_id", "mz", "rt_seconds", "rt_minutes"]
        intensity_sample_cols = [c for c in featureTable.columns if c not in meta_cols]

        # feature_id → mz 映射
        id_to_mz = dict(zip(featureTable["feature_id"], featureTable["mz"]))
        # feature_id → 平均强度
        id_to_intensity = {}
        for fid, mean_val in zip(
            featureTable["feature_id"],
            featureTable[intensity_sample_cols].mean(axis=1).values
        ):
            id_to_intensity[fid] = mean_val if np.isfinite(mean_val) else 0.0

        # 按 precursor 聚合碎片信息
        spec_map = {}
        for _, row in hits.iterrows():
            p_fid = row["precursor_feature_id"]
            f_fid = row["fragment_feature_id"]
            if f_fid not in id_to_mz:
                continue
            fmz = id_to_mz[f_fid]
            fint = id_to_intensity.get(f_fid, 0.0)
            spec_map.setdefault(p_fid, {})[fmz] = fint

        # 构建 spec 列和 intensity 列
        spec_col = []
        frag_intensity_col = []
        for _, row in featureTable.iterrows():
            fid = row["feature_id"]
            if fid in spec_map:
                frags = spec_map[fid]
                sorted_mz = sorted(frags.keys())
                spec_col.append(", ".join(str(mz) for mz in sorted_mz))
                frag_intensity_col.append(", ".join(str(frags[mz]) for mz in sorted_mz))
            else:
                spec_col.append("")
                frag_intensity_col.append("")

        featureTable["spec"] = spec_col
        mz_idx = featureTable.columns.get_loc("mz")
        featureTable.insert(mz_idx + 1, "intensity", frag_intensity_col)

        # 保存
        feature_csv = os.path.join(result["run_dir"], "feature_matrix_with_spec.csv")
        featureTable.to_csv(feature_csv, index=False, encoding="utf-8-sig")
        print(f"带 spec 的特征表: {feature_csv}")
        print(f"  有碎片特征: {(featureTable['spec'] != '').sum():,} / {len(featureTable):,}")

        print(f"\nISF 配对结果: {result['hits_csv']}")
    else:
        print("\n没有找到满足条件的 ISF 配对。")
        print("可以尝试降低 peakCOR 或 screenCOR 阈值。")

    print(f"\n断点续跑目录: {result['run_dir']}")

    # ---- 7. 清理中间文件 ----
    if cleanup_intermediates:
        import shutil
        run_dir = result["run_dir"]
        keep_suffixes = (".csv",)
        removed_size = 0

        for item in sorted(os.listdir(run_dir)):
            item_path = os.path.join(run_dir, item)
            if os.path.isdir(item_path):
                size = _dir_size(item_path)
                shutil.rmtree(item_path)
                removed_size += size
            elif os.path.isfile(item_path) and not item.lower().endswith(keep_suffixes):
                size = os.path.getsize(item_path)
                os.remove(item_path)
                removed_size += size

        print(f"\n已清理中间文件，释放 {removed_size / 1024**3:.1f} GB")
        print(f"保留文件:")
        for f in sorted(os.listdir(run_dir)):
            print(f"  {f}")
