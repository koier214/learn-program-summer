# ISF Level3 R版本对照 Python实现修改说明

## 1. 项目背景

本项目目标：

将原始 R 语言版本的 ISF Level3 两阶段分析流程迁移至 Python。

当前 Python 实现：

```
isf_level3.py
run_test.py
```

主要流程：

```
featureTable
    |
    ↓
候选 ISF pair生成
    |
    ↓
Stage 1 EIC筛选
    |
    ↓
Stage 2 全文件验证
    |
    ↓
输出ISF结果
```

当前需要根据 R 版本逻辑，对 Python 实现进行一致性检查和修正。

---

# 2. 修改目标

Python版本必须保证：

1. 输入数据结构与R版本一致
2. feature编号体系一致
3. 参数含义一致
4. Pearson相关计算方式一致
5. mz/RT筛选逻辑一致
6. 输出结果字段一致

---

# 3. 已发现问题及修改要求

# 问题1：feature_id映射问题（最高优先级）

## 问题描述

Python早期版本：

```python
featureTable = featureTable.set_index("feature_id")
```

会导致：

原始：

```
feature_id
mz
rt
intensity
```

变成：

```
index
mz
rt
intensity
```

feature_id被作为DataFrame索引。

但是ISF流程内部大量使用：

```python
feature index
```

作为：

* precursor编号
* fragment编号

导致：

输出结果可能：

```
precursor = 100
```

实际代表：

```
第100行feature
```

而不是：

```
feature_id=100
```

---

## 修改要求

禁止：

```python
set_index("feature_id")
```

保持：

```python
featureTable["feature_id"]
```

作为普通字段。

增加映射：

```python
feature_id_map = featureTable["feature_id"]
```

输出结果时：

增加：

```python
precursor_feature_id

fragment_feature_id
```

转换：

```python
result["precursor_feature_id"] =
feature_id_map[result["precursor"]]


result["fragment_feature_id"] =
feature_id_map[result["fragment"]]
```

---

# 问题2：feature编号体系需要与R版本一致

## 需要确认R版本

R语言通常：

```
data.frame row index:
1,2,3...
```

Python：

```
DataFrame index:
0,1,2...
```

存在：

```
+1偏移风险
```

---

## 修改要求

确认：

R输出：

```
precursor
fragment
```

是否：

* 从0开始
* 从1开始

Python必须保持一致。

如果R：

```
1-based
```

Python输出：

```python
result["precursor"] += 1
result["fragment"] += 1
```

---

# 问题3：intensity列匹配逻辑

## 当前风险

如果使用：

```python
range()
```

固定列位置：

例如：

```python
range(3,5)
```

依赖CSV格式。

---

## 修改要求

推荐：

通过样本名称匹配。

例如：

mzML:

```
sampleA.mzML
```

寻找：

```
sampleA
```

对应列。

增加检查：

```python
if len(matched)!=1:

    raise ValueError(
    "Intensity column ambiguous"
    )
```

避免：

多个列匹配。

---

# 问题4：相关系数计算必须完全复制R逻辑

重点检查R函数：

```
cor()
```

对应Python：

```
pearsonr()
```

需要确认：

## 4.1 是否log转换

Python当前存在：

```python
log1p()
```

需要确认R：

是否：

```R
cor(log1p(x),log1p(y))
```

如果R没有：

Python不能增加。

如果R有：

Python必须保持。

---

## 4.2 是否取绝对值

确认R：

是否：

```R
abs(cor)
```

如果R：

```
|correlation| > threshold
```

Python必须：

```python
abs(correlation)
```

否则：

负相关ISF会丢失。

---

## 4.3 缺失值处理

确认R：

```R
use="complete.obs"
```

对应Python：

```python
remove NaN
```

---

# 问题5：mz_tol单位确认

当前Python：

```python
mz_tol=0.01
```

需要确认R版本：

是否：

## Da单位

例如：

```
mz ±0.01
```

或者：

## ppm单位

例如：

```
10 ppm
```

---

如果R使用ppm：

Python需要修改：

```python
mz_error =
mz * ppm / 1e6
```

不能直接：

```python
mz_tol=0.01
```

---

# 问题6：RT tolerance单位确认

当前：

```python
rt_tol=30
```

需要确认R：

单位：

* 秒？
* 分钟？
* scan?

featureTable:

```
rt_seconds
```

还是：

```
rt_minutes
```

必须统一。

---

# 问题7：候选筛选逻辑需要和R一致

检查：

## precursor-fragment质量差

Python：

```python
loss=10
```

需要确认R：

是否：

```R
mz_difference
```

还是：

```R
neutral_loss
```

---

检查：

边界条件：

例如：

```
>=
```

还是：

```
>
```

这些会影响结果数量。

---

# 问题8：Stage1 / Stage2阈值一致

Python参数：

```python
peakCOR=0.80

screenCOR=0.65
```

需要确认R：

对应：

Stage1：

```
peakCOR
```

Stage2：

```
screenCOR
```

以及：

判断：

```python
>
```

还是：

```python
>=
```

---

# 问题9：稀疏数据处理逻辑

Python：

```python
stage1_fail_open_sparse=True
```

需要确认R：

是否存在：

```
fail open
```

策略。

如果R没有：

Python需要删除。

否则：

可能产生更多假阳性。

---

# 问题10：缓存可能导致结果污染

Python：

```python
rebuild_intensity_cache=False
```

风险：

修改算法后：

仍读取旧缓存。

测试阶段建议：

第一次运行：

```python
rebuild_intensity_cache=True

rebuild_candidates=True

rebuild_stage1=True

rebuild_stage2=True
```

确认结果。

---

# 4. 参数一致性检查表

| 参数                   | Python | 需要确认R  |
| -------------------- | ------ | ------ |
| peakCOR              | 0.8    | 是否一致   |
| screenCOR            | 0.65   | 是否一致   |
| loss                 | 10     | 单位     |
| mz_tol               | 0.01   | Da/ppm |
| rt_tol               | 30     | 单位     |
| min_copresent_files  | 3      | 是否一致   |
| stage1_min_valid     | 2      | 是否一致   |
| min_final_valid      | 3      | 是否一致   |
| final_min_proportion | 0      | 是否一致   |

---

# 5. 输出结果一致性要求

R与Python运行同一数据：

输入：

```
featureTable

mzML文件

参数
```

比较：

| 指标         | 要求 |
| ---------- | -- |
| 候选pair数量   | 一致 |
| Stage1通过数量 | 接近 |
| Stage2通过数量 | 接近 |
| 最终ISF数量    | 一致 |
| feature编号  | 一致 |

---

# 6. 推荐修改顺序

## 第一阶段：数据一致性

完成：

* feature_id映射
* intensity列匹配
* RT/mz单位确认

---

## 第二阶段：算法一致性

检查：

* correlation计算
* abs处理
* 缺失值处理
* 阈值边界

---

## 第三阶段：结果验证

使用：

同一：

```
featureTable
mzML
```

运行：

R版本

Python版本

比较：

最终ISF结果。

---

# 7. 当前结论

Python版本整体框架已经完成：

* 两阶段流程存在
* 大规模数据处理结构合理
* mzML读取流程正常

当前主要风险不是代码结构问题，而是：

> Python实现是否严格复现R版本的数学逻辑和数据编号体系。

下一步重点：

1. 对照R代码确认feature编号规则
2. 对照R代码确认cor计算方式
3. 对照R代码确认mz/RT tolerance单位
4. 使用同一数据进行R/Python结果比对
