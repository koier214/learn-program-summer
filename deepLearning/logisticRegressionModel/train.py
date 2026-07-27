import os
import torch
from models.logistic_regression_model import LogisticRegressionModel

def main():
    x_data = torch.Tensor([[1.0], [2.0], [3.0]])
    y_data = torch.Tensor([[0.0], [0.0], [1.0]])

    model = LogisticRegressionModel()

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
    print('w = ', model.linear.weight.item())
    print('b = ', model.linear.bias.item())

    save_dir = os.path.join(os.path.dirname(__file__), 'checkpoints')
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, 'logistic_regression_model.pth')
    torch.save(model.state_dict(), save_path)
    print(f'模型已保存至 {save_path}')


if __name__ == '__main__':
    main()