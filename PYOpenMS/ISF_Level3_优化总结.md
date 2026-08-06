# ISF Level 3 Python 代码优化总结

## 1. 优化前状态

Python 版 ISF Level3 完整移植自 R 版，2 文件测试通过（65,765 ISF 配对，结果正确）。

**目标规模**：~38,000 特征 × ~6,000 mzML 文件（分批处理）。

**硬件**：16GB/8核机器，Windows 10。

---

## 2. 全部优化汇总

### 2.1 算法/性能优化

| 优化位置 | 优化什么 | 为什么优化 | 如何优化 | 效果 |
|---|---|---|---|---|
| **EIC 提取** `isf_level3.py` Stage 3/6 核心循环 | mzML 扫描中提取特征强度 | 之前逐扫描逐特征布尔掩码 O(M)，Stage 3 耗时 58 分钟，占总时间 ~90% | `searchsorted` 二分查找 O(log M) 定位 m/z 窗口 + 累积和 O(1) 区间求和 + 全部候选特征向量化一次完成 | Stage 3：58 min → ~2 min（~29x） |
| **平滑函数** `isf_level3.py` `_peak_smooth` | EIC 色谱三角加权移动平均 | 之前逐列 Python for 循环，Stage 6 耗时 7 分钟 | 预建归一化三角权重矩阵，改为一次矩阵乘法 `W @ eic_matrix`，利用 numpy BLAS | Stage 6：7 min → ~18 s（~23x） |
| **块内批量读 memmap** `isf_level3.py` `_build_candidate_chunk` | Stage 2 候选生成中逐行读取 precursor 强度 | 每块 500 个 precursor，每次单独 `bm[id, :]` 读一行，500 次 OS 调用；多文件时每行更宽，开销指数增长 | 循环前一次性 `bm[precursor_ids, :]` 读所有 precursor 行（500 行→1 次 IO），循环内 `chunk_data[i, :]` 零开销内存索引 | Stage 2：9 min → ~7 min（-22%） |
| **candidate_batch_size** `run_test.py` | fragment 批处理大小 | 默认 250，batch 循环次数多，Python 循环开销累积 | 250 → 2000，循环次数减少 8 倍 | 叠加在 Stage 2 中 |
| **workers 并行** `run_test.py` | mzML 文件并行处理数 | 之前单 worker，2 个文件串行，CPU 闲置 | 1 → 2，`multiprocessing.Pool` 并行处理 | Stage 3/6 各加速 ~2x |

### 2.2 兼容性/正确性修复

| 优化位置 | 优化什么 | 为什么优化 | 如何优化 |
|---|---|---|---|
| **Windows 兼容性** `isf_level3.py` + `run_test.py` | 多进程 pickle 序列化 | Windows spawn 模式下，类内嵌套函数无法 pickle；缺少 `if __name__` 保护导致无限递归 | 嵌套函数提升为模块级函数（`_stage1_file_worker` 等），run_test.py 加 `if __name__ == '__main__'` 保护 |
| **NumPy 索引语义差异** `isf_level3.py` | R → Python 矩阵索引 | R `matrix[row, col]` 返回笛卡尔积；NumPy 直接索引返回对角线 | 用 `np.ix_(row, col)` 替代直接索引 |
| **变量名错误** `isf_level3.py` | `feature_ids_in_block` | 变量名拼写错误导致 NameError | 改为 `feature_ids_in_eic` |
| **.iloc 索引越界** `isf_level3.py` | DataFrame 行索引 | `group_rows.index` 返回标签索引非位置索引 | 改为 `.loc`（标签索引） |
| **prefilter_cor 处理** `run_test.py` | 跨样品强度预筛选 | 2 文件时退化为 2 样品 Pearson（只能算 +1/-1/NaN），误杀 28% 真实 ISF 对 | 注释掉参数并添加详细使用场景说明和注释文档 |
| **tqdm 未导入** `isf_level3.py` | 进度条依赖 | tqdm 未 import 导致 NameError | 添加 `from tqdm import tqdm` |

### 2.3 易用性改进

| 优化位置 | 优化什么 | 为什么优化 | 如何优化 |
|---|---|---|---|
| **路径配置** `run_test.py` | CSV/mzML/输出路径 | 之前硬编码在 test_data 子目录，换电脑/换数据需多处修改 | 顶部集中配置区，只需改 2 个路径 + 1 个目录 |
| **自动扫描 mzML** `run_test.py` | mzML 文件列表 | 200 个文件手动列举不现实 | 填目录路径，`glob` 自动扫描所有 `.mzML`/`.mzXML` |
| **自动编号目录** `isf_level3.py` + `run_test.py` | 输出目录命名 | 之前用 MD5 哈希值（如 `run_faa8d555c7cb5cfb...`），无法辨认对应哪批 | 加 `batch_label` 参数，run_test.py 自动扫描已有编号递增：`intensity_01/run_01` → `02` → `03`... |
| **自动产出 spec 特征表** `run_test.py` | ISF 结果格式转换 | 之前需单独跑 `add_spec_column.py` 将 hits 转成带 `spec`/`intensity` 列的特征表 | ISF 跑完后自动聚合碎片信息，直接输出 `feature_matrix_with_spec.csv` |
| **自动清理中间文件** `run_test.py` | 断点续跑临时文件 | 每批产出 ~8GB 中间文件（candidate_chunks、stage1_*、pkl 等），多批次磁盘扛不住 | `cleanup_intermediates=True`，跑完自动删除，只留最终 2 个 CSV（~15MB）；False 可保留调试 |

---

## 3. 效果汇总

| 指标 | 优化前 | 优化后 |
|---|---|---|
| Stage 2 候选生成 | 9 分 02 秒 | 7 分 12 秒 |
| Stage 3 Stage-1 EIC | 58 分 00 秒 | 2 分 14 秒 |
| Stage 6 Stage-2 EIC | 7 分 00 秒 | 0 分 18 秒 |
| **总耗时（2 文件）** | **~75 分钟** | **~10 分钟** |
| **加速比** | — | **~7.5x** |
| 最终 ISF 对数 | 65,765 | 65,765（**一致**） |
| 中间文件磁盘占用 | 每批 ~8 GB | 每批 ~15 MB（自动清理后） |

---

## 4. 多文件场景预估

以 8 核 16GB 机器，每批 200 文件，workers=6 为例：

| 阶段 | 单批 200 文件 | 6000 文件（30 批） |
|---|---|---|
| Stage 2 候选生成 | ~15 分钟 | ~7.5 小时 |
| Stage 3 EIC | ~45 分钟 | ~22.5 小时 |
| Stage 6 EIC | ~10 分钟 | ~5 小时 |
| **单批总计** | **~70 分钟** | — |
| **全量总计** | — | **~35 小时** |

实际耗时受特征数、候选对数、文件间共现率等因素影响，以上为保守估计。

---

## 5. 文件说明

| 文件 | 说明 |
|---|---|
| `isf_level3.py` | ISF Level 3 核心算法（~2100 行），所有生产级优化在此文件中 |
| `run_test.py` | 启动脚本，路径配置 + ISF 调用 + 后处理（spec 生成 + 清理） |
| `add_spec_column.py` | 已废弃，功能已集成到 run_test.py 中 |

---

## 6. 使用方式

1. 修改 `run_test.py` 顶部配置区的 3 个路径：
   - `csv_path`：特征表 CSV 路径
   - `MS1directory`：mzML 文件所在目录
   - `work_dir`：输出目录
2. 运行 `python run_test.py`
3. 在 `work_dir/run_XX/` 中获取：
   - `ISF_Level3_hits.csv`：ISF 配对结果
   - `feature_matrix_with_spec.csv`：带 spec/intensity 列的特征表

每跑完一批，编号自动递增（01 → 02 → 03...），无需手动改配置。
