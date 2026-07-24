# MS-MINT 增加 Excel 导出功能 — 改动方案

> **目标**：在"Processing → Download Results → All Results"中增加导出 Excel 格式的功能，并补充样本元数据字段（样本类型、组别等）。
> **改动文件**：`ms_mint_app/plugins/processing.py`（1 个文件，3 个改动区域）
> **依赖**：`openpyxl` 已安装（v3.1.5），无需额外安装

---

## 一、改动概览

| 改动编号 | 位置（行号附近） | 改什么 |
|----------|-----------------|--------|
| ① | `DOWNLOAD_ALL_RESULTS_ALLOWED_COLS`（第 48-55 行） | 增加 8 个样本元数据字段 |
| ② | `_generate_csv_from_db()`（第 1268-1296 行） | SQL 列构建时处理新增的样本字段 |
| ③ | `_download_all_results()`（第 1110-1231 行） | 增加 Excel 格式分支，用 pandas 将结果转写为 .xlsx |

> 注：UI 层面不增加格式选择器，直接在"All Results"区域的列选择下拉框末尾追加一个 `[Format: Excel]` 伪选项。选中该项时，下载文件后缀从 `.csv` 切换为 `.xlsx`，数据内容用 `openpyxl` 引擎写入。这样无需改动 Dash 布局即可区分 CSV/Excel 意图。

---

## 二、详细改动

### 改动 ① — 增加可导出字段

**文件**：`processing.py`
**位置**：`DOWNLOAD_ALL_RESULTS_ALLOWED_COLS` 列表（约第 48-55 行）

**现状**：
```python
DOWNLOAD_ALL_RESULTS_ALLOWED_COLS = [
    'rt', 'formula', 'mz_mean',
    'peak_area', 'peak_area_fitted', 'peak_area_top3', 'peak_mean',
    'peak_median', 'peak_n_datapoints', 'peak_min', 'peak_max',
    'peak_rt_of_max', 'peak_sigma', 'peak_tau', 'peak_asymmetry',
    'peak_rt_fitted', 'fit_r_squared', 'fit_success', 'total_intensity',
    'rt_aligned', 'rt_shift', 'peak_mz_of_max', 'scan_time', 'intensity',
    'scalir_conc', 'scalir_in_range', 'scalir_unit',
]
```

**改为**：
```python
DOWNLOAD_ALL_RESULTS_ALLOWED_COLS = [
    'rt', 'formula', 'mz_mean',
    'peak_area', 'peak_area_fitted', 'peak_area_top3', 'peak_mean',
    'peak_median', 'peak_n_datapoints', 'peak_min', 'peak_max',
    'peak_rt_of_max', 'peak_sigma', 'peak_tau', 'peak_asymmetry',
    'peak_rt_fitted', 'fit_r_squared', 'fit_success', 'total_intensity',
    'rt_aligned', 'rt_shift', 'peak_mz_of_max', 'scan_time', 'intensity',
    'scalir_conc', 'scalir_in_range', 'scalir_unit',
    'label', 'sample_type',           # ← 新增：样本标签、样本类型
    'group_1', 'group_2', 'group_3',  # ← 新增：自定义分组字段
    'group_4', 'group_5',             # ← 新增
    '_format_xlsx',                   # ← 新增：伪选项，勾选即导出 Excel
]
```

**新增字段含义**：

| 字段 | 来源表 | 含义 |
|------|--------|------|
| `label` | samples | 样本显示名称 |
| `sample_type` | samples | 样本类型：Sample / QC / Blank / Standard |
| `group_1` ~ `group_5` | samples | 用户自定义分组（如"对照组""实验组"） |
| `_format_xlsx` | — | 特殊标记：勾选时下载 .xlsx 代替 .csv |

---

### 改动 ② — `_generate_csv_from_db` 处理新字段

**文件**：`processing.py`
**位置**：`_generate_csv_from_db()` 函数中列构建循环（约第 1269-1295 行）

**现状**（列到 SQL 的映射逻辑）：
```python
for c in safe_cols:
    if c in ('scan_time', 'intensity'):
        col_list.append(f"array_to_string(r.{c}, ',') AS {c}")
    elif c == 'rt':
        col_list.append("t.rt")
    elif c == 'formula':
        col_list.append("t.formula")
    elif c == 'mz_mean':
        col_list.append("t.mz_mean")
    elif c == 'scalir_conc':
        ...
    # ... 其他 SCALiR 分支 ...
    elif c not in ('peak_label', 'ms_file_label', 'ms_type'):
        col_list.append(f"r.{c}")
```

**需要增加的分支**（在 `elif c == 'mz_mean'` 之后插入）：
```python
    elif c == 'label':
        col_list.append("s.label")
    elif c == 'sample_type':
        col_list.append("s.sample_type")
    elif c in ('group_1', 'group_2', 'group_3', 'group_4', 'group_5'):
        col_list.append(f"s.{c}")
```

**原因**：这些字段存在 `samples` 表（别名 `s`），需要加 `s.` 前缀，否则 DuckDB 会在 `results` 表中找不到这些列而报错。

---

### 改动 ③ — `_download_all_results` 支持 Excel 输出

**文件**：`processing.py`
**位置**：`_download_all_results()` 函数（约第 1110-1231 行）

**修改逻辑**：

在生成临时 CSV 文件之后、返回下载数据之前，插入格式判断：

```python
def _download_all_results(wdir: str, ws_name: str, selected_columns: list) -> tuple:
    # ... 前面的校验和列筛选保持不变 ...
    
    # ★ 新增：检测是否选了 Excel 格式
    _export_xlsx = '_format_xlsx' in selected_columns
    safe_cols = [c for c in selected_columns if c in allowed_cols and c != '_format_xlsx']
    
    # ... 生成 CSV 临时文件（保持不变）...
    
    # ★ 新增：如果选了 Excel，转写为 xlsx
    if _export_xlsx:
        import pandas as pd
        df = pd.read_csv(tmp_path)
        xlsx_path = tmp_path.replace('.csv', '.xlsx')
        df.to_excel(xlsx_path, index=False, engine='openpyxl')
        # 清理原 CSV 临时文件
        os.unlink(tmp_path)
        tmp_path = xlsx_path
        filename = filename.replace('.csv', '.xlsx')
    
    # ... 大小判断与返回下载（保持不变）...
```

**说明**：
- 复用现有的 CSV 生成流程（`_generate_csv_from_db` 或 Polars 筛选），生成临时 CSV 后立即用 pandas 转写为 `.xlsx`
- `openpyxl` 已在虚拟环境中安装（v3.1.5），无需新增依赖
- 大文件（>50MB）的 Flask 直链下载路径同样适用，因为只是文件后缀变了

---

## 三、导出结果示例

用户勾选 `peak_label, mz_mean, rt, peak_area, sample_type, group_1, _format_xlsx`，点击 Download，得到 `2026-07-22-MINT__MyWorkspace-all_results.xlsx`：

| peak_label | mz_mean | rt | peak_area | sample_type | group_1 |
|------------|---------|-----|-----------|-------------|---------|
| 琥珀酸 | 118.03 | 480.2 | 5230000 | Sample | 对照组 |
| 琥珀酸 | 118.03 | 481.1 | 4870000 | Sample | 实验组 |
| 谷氨酸 | 145.06 | 606.5 | 11200000 | Sample | 对照组 |
| 谷氨酸 | 145.06 | 607.0 | 8200000 | Sample | 实验组 |
| ... | ... | ... | ... | ... | ... |

---

## 四、影响范围与风险评估

| 维度 | 评估 |
|------|------|
| 改动文件数 | 1 个 |
| 改动行数 | 约 25 行 |
| 破坏现有功能？ | 否，CSV 导出完全不受影响，`_format_xlsx` 不勾选时行为与原来一致 |
| 依赖 | `openpyxl` 已安装，`pandas` 已安装 |
| 大文件兼容 | 通过，大文件 CSV 转 Excel 均在服务器端完成 |

---

## 五、改动顺序

```
改动 ① → 改动 ② → 改动 ③
  │          │          │
  │          │          └─ 前端不感知格式切换，完全在后端判断
  │          └─ 确保新字段能正确从数据库 JOIN 出来
  └─ 先把字段声明为"允许导出"，否则后续逻辑会过滤掉
```
