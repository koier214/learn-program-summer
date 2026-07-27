import os
import torch
from models.logistic_regression_model import LogisticRegressionModel

def main():
    model = LogisticRegressionModel()
    save_path = os.path.join(os.path.dirname(__file__),
                             'checkpoints', 'logistic_regression_model.pth')
    model.load_state_dict(torch.load(save_path))
    model.eval()  # 切换为推理模式

    print(f'已加载训练好的模型: {save_path}')
    print('输入每周学习小时数，输入 q 退出\n')

    while True:
        user_input = input('每周学习小时数: ')
        if user_input.lower() == 'q':
            print('再见')
            break
        try:
            hours = float(user_input)
        except ValueError:
            print('请输入数字或 q 退出')
            continue

        x = torch.Tensor([[hours]])
        y = model(x)
        print(f'预测是否通过考试的概率: {y.item():.2f}\n')


main()
