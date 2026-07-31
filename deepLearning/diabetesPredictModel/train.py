import os
import torch
import numpy as np
from models.diabetes_model import Model
from torch.utils.data import DataLoader, Dataset


def main():
    class DiabetesDataset(Dataset):
        def __init__(self, filepath):
            xy = np.loadtxt(filepath, delimiter=',', dtype=np.float32)
            self.len = xy.shape[0]
            x_data = torch.from_numpy(xy[:, :-1])
            y_data = torch.from_numpy(xy[:, [-1]])

            # 归一化在 Dataset 里做
            self.mean = x_data.mean(dim=0)
            self.std = x_data.std(dim=0)
            self.x_data = (x_data - self.mean) / self.std
            self.y_data = y_data

        def __getitem__(self, index):
            return self.x_data[index], self.y_data[index]

        def __len__(self):
            return self.len

    dataset = DiabetesDataset('diabetes.csv.gz')
    train_loader = DataLoader(dataset=dataset, batch_size=32, shuffle=True)

    model = Model()

    criterion = torch.nn.BCELoss(reduction='mean')
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

    for epoch in range(1000):
        for i, data in enumerate(train_loader, 0):
            inputs, labels = data
            y_pred = model(inputs)
            loss = criterion(y_pred, labels)
            print(f'Epoch {epoch}, Batch {i}, Loss: {loss.item()}')

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

    print('训练完成')

    save_dir = os.path.join(os.path.dirname(__file__), 'checkpoints')
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, 'diabetes_model.pth')
    torch.save(model.state_dict(), save_path)
    print(f'模型已保存至 {save_path}')

    # 归一化参数从 Dataset 对象中取
    norm_path = os.path.join(save_dir, 'norm_params.pth')
    torch.save({'mean': dataset.mean, 'std': dataset.std}, norm_path)
    print(f'归一化参数已保存至 {norm_path}')


if __name__ == '__main__':
    main()
