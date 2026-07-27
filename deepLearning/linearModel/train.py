import os
import torch
from models.linear_model import LinearModel


def main():
    # 数据集
    x_data = torch.Tensor([[1.0], [2.0], [3.0]])
    y_data = torch.Tensor([[2.0], [4.0], [6.0]])

    # 初始化模型
    model = LinearModel()

    # 损失计算 & 优化器
    criterion = torch.nn.MSELoss(reduction='sum')
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

    # 训练
    for epoch in range(1000):
        y_pred = model(x_data)
        loss = criterion(y_pred, y_data)
        print(epoch, loss.item())

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    print('训练完成')
    print('w = ', model.linear.weight.item())
    print('b = ', model.linear.bias.item())

    # 保存模型权重
    save_dir = os.path.join(os.path.dirname(__file__), 'checkpoints')
    save_path = os.path.join(save_dir, 'linear_model.pth')
    torch.save(model.state_dict(), save_path)
    print(f'模型已保存至 {save_path}')


if __name__ == '__main__':
    main()
