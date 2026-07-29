import os
import torch
import numpy as np
from models.diabetes_model import Model


def main():
    # 加载数据
    xy = np.loadtxt('diabetes.csv.gz', delimiter=',', dtype=np.float32)
    x_data = torch.from_numpy(xy[:, :-1])
    y_data = torch.from_numpy(xy[:, [-1]])

    # 归一化：保存 mean 和 std，predict.py 要用
    mean = x_data.mean(dim=0)
    std = x_data.std(dim=0)
    x_data = (x_data - mean) / std

    model = Model()

    criterion = torch.nn.BCELoss(reduction='sum')
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

    for epoch in range(1000):
        y_pred = model(x_data)
        loss = criterion(y_pred, y_data)
        print(epoch, loss.item())

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    print('训练完成')

    # 保存模型权重
    save_dir = os.path.join(os.path.dirname(__file__), 'checkpoints')
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, 'diabetes_model.pth')
    torch.save(model.state_dict(), save_path)
    print(f'模型已保存至 {save_path}')

    # 保存归一化参数，predict.py 要用
    norm_path = os.path.join(save_dir, 'norm_params.pth')
    torch.save({'mean': mean, 'std': std}, norm_path)
    print(f'归一化参数已保存至 {norm_path}')


if __name__ == '__main__':
    main()
