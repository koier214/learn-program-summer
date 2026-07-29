import torch
import numpy as np

xy = np.loadtxt('diabetes.csv.gz', delimiter=',', dtype=np.float32)
x_data = torch.from_numpy(xy[:, :-1])
y_data = torch.from_numpy(xy[:, [-1]])

# 归一化：每个特征减去均值再除以标准差，让所有特征在同一尺度
mean = x_data.mean(dim=0)
std = x_data.std(dim=0)
x_data = (x_data - mean) / std

class Model(torch.nn.Module):
    def __init__(self):
        super(Model, self).__init__()
        self.linear1 = torch.nn.Linear(8, 6)
        self.linear2 = torch.nn.Linear(6, 4)
        self.linear3 = torch.nn.Linear(4, 1)
        self.relu = torch.nn.ReLU()
        self.sigmoid = torch.nn.Sigmoid()

    def forward(self, x):
        x = self.relu(self.linear1(x))
        x = self.relu(self.linear2(x))
        x = self.sigmoid(self.linear3(x))  # 最后一层用 sigmoid，输出 0~1 概率
        return x

model = Model()

criterion = torch.nn.BCELoss(reduction= 'sum')
optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

for epoch in range(1000):
    # 前向传播
    y_pred = model(x_data)
    loss = criterion(y_pred, y_data)
    print(epoch, loss.item())

    # 反向传播
    optimizer.zero_grad()
    loss.backward()
    
    # 更新参数
    optimizer.step()
