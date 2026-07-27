import os
import torch
import numpy as np
import matplotlib.pyplot as plt
from models.logistic_regression_model import LogisticRegressionModel


def main():
    # 加载模型
    model = LogisticRegressionModel()
    save_path = os.path.join(os.path.dirname(__file__),
                             'checkpoints', 'logistic_regression_model.pth')
    model.load_state_dict(torch.load(save_path))
    model.eval()

    # 生成 0~10 小时的连续数据
    x = np.linspace(0, 10, 200)
    x_t = torch.Tensor(x).view((200, 1))
    y_t = model(x_t)
    y = y_t.data.numpy()

    # 画 sigmoid 曲线
    plt.plot(x, y)
    # 画 0.5 分界线
    plt.plot([0, 10], [0.5, 0.5], c='r')
    plt.xlabel('Hours')
    plt.ylabel('Probability of Pass')
    plt.grid()
    plt.show()


main()
