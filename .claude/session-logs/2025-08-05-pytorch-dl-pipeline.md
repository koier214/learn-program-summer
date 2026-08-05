---
session_date: 2025-08-05
session_time: 15:00-18:30
tags: [pytorch, 线性回归, 逻辑回归, 糖尿病预测, mini-batch, 深度学习基础]
status: continuing
related_sessions: []
---

# 会话摘要 — 2025-08-05 PyTorch 深度学习完整流程学习

## 1. 会话概述

用户（研零新生，编程基础一般）在完成线性回归和逻辑回归两个简单模型后感到迷茫，不知道在学什么、目标是什么。本次对话从"学习目标"和"核心概念"两个维度重新梳理了已学内容，然后扩展到多维度输入的糖尿病预测模型，最终引入了 mini-batch 训练。全程以标准项目目录结构（models/checkpoints/train/predict）组织代码。

## 2. 关键决策

- 使用 Python 3.14（`C:/Users/A/AppData/Local/Programs/Python/Python314/python.exe`）而非默认的 Python 3.12
- pip 安装使用清华镜像源加速（`-i https://pypi.tuna.tsinghua.edu.cn/simple`）
- 三个模型统一采用标准目录结构：`models/`（模型定义）、`checkpoints/`（权重文件）、`train.py`（训练）、`predict.py`（推理）
- 中间层激活函数用 ReLU（防止梯度消失），分类输出层用 Sigmoid
- 糖尿病模型引入 mini-batch 训练（batch_size=32, shuffle=True, reduction='mean'）
- 归一化参数（mean/std）与模型权重一起保存，推理时复用

## 3. 产出物

### 线性回归项目
- [deepLearning/linearModel/models/linear_model.py] — LinearModel 类定义
- [deepLearning/linearModel/train.py] — 训练脚本（1000 epoch + 保存权重）
- [deepLearning/linearModel/predict.py] — 交互式推理脚本

### 逻辑回归项目
- [deepLearning/logisticRegressionModel/models/logistic_regression_model.py] — LogisticRegressionModel（Linear + Sigmoid）
- [deepLearning/logisticRegressionModel/train.py] — 训练脚本（含 `if __name__ == '__main__'` 保护）
- [deepLearning/logisticRegressionModel/predict.py] — 交互式推理（输入小时数→输出及格概率）
- [deepLearning/logisticRegressionModel/visualize.py] — sigmoid 曲线可视化

### 糖尿病预测项目
- [deepLearning/diabetesPredictModel/models/diabetes_model.py] — 三层网络 Model（8→6→4→1，ReLU+Sigmoid）
- [deepLearning/diabetesPredictModel/train.py] — mini-batch 训练（DataLoader, batch_size=32, 归一化在 Dataset 内完成）
- [deepLearning/diabetesPredictModel/predict.py] — 8 特征交互式推理（加载模型 + 归一化参数）
- [deepLearning/diabetesPredictModel/diabetes.py] — 原始脚本（保留不动）

### 依赖安装
- `torch`, `torchvision`, `torchaudio` (2.13.0+cpu)
- `matplotlib` (3.11.1)
- `numpy` (2.5.1)

## 4. 未完成事项

- [ ] 逻辑回归 predict.py 和 visualize.py 尚未实际运行测试
- [ ] 糖尿病模型准确率未评估（只看了 loss）
- [ ] 8 道 quiz 问题只回答了 Q1-Q5，Q6-Q8 未答
- [ ] 分子毒性预测论文只列出了标题，未深入讨论
- [ ] 未引入验证集/测试集的概念，无法判断过拟合
- [ ] Adam 优化器尚未介绍（仍是 SGD）

## 5. 理解纠正

- `__name__ == '__main__'` 中的 `'__main__'` 是 Python 规定的固定字符串，和函数名 `main()` 无关，用户可以写 `def index():` 然后 `if __name__ == '__main__': index()`
- `model.state_dict()` 只返回参数值的字典，不做保存；`torch.save()` 才负责写入磁盘
- `nn.Linear(1,1)` 的两个参数是"输入维度"和"输出维度"，不是"参数维度"
- 过拟合（overfitting）的本质是训练 loss 降但测试 loss 升，不是训练 loss 变大
- 训练 loss 变大通常是因为学习率太大导致跳过最优解（overshooting），不是过拟合
- Sigmoid 不只为了"结果好看"，BCELoss 内用 log(p)，p 必须严格在 (0,1) 内，否则数学崩溃
- `loss.backward()` 只更新梯度存储空间（w.grad, b.grad），不改变参数本身；`optimizer.step()` 才真正更新 w 和 b
- epoch = 所有数据被看过一遍；step = 参数更新一次。全量模式下 1 epoch = 1 step，mini-batch 下 1 epoch = 多个 step

## 6. 重要上下文

- 用户的最终目标：设计环境领域的预测模型（小分子性质预测、有毒物质筛查）
- 8 个核心概念已逐一讲解：模型本质、损失函数、梯度下降、反向传播、训练循环、激活函数、模型保存、推理 vs 训练
- 深度学习 6 步法已总结：准备数据 → 定义模型 → 选损失函数 → 选优化器 → 训练循环 → 保存模型
- 用户对 `__name__ == '__main__'` 的理解仍有困惑但选择暂时跳过
- 用户已掌握的结构模式：models/ 放模型类、checkpoints/ 放权重、train.py 训练保存、predict.py 加载推理
- 教学风格要求：先解释原因再操作、术语用 📚📖🌰🔗 格式、体现因果链、修改前确认、回答后询问理解、总结推理链

## 7. 话题标签

pytorch, 深度学习基础, 线性回归, 逻辑回归, 神经网络, mini-batch, 糖尿病预测, 模型部署, 归一化, 梯度下降
