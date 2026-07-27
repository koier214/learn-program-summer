import os
import torch
from models.linear_model import LinearModel


def main():
    # 初始化模型并加载权重.
    model = LinearModel()
    save_path = os.path.join(os.path.dirname(__file__),
                             'checkpoints', 'linear_model.pth')
    model.load_state_dict(torch.load(save_path))
    model.eval()  # 切换为推理模式

    print(f'已加载训练好的模型: {save_path}')
    print('输入学习小时数，输入 q 退出\n')

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
        print(f'预测分数: {y.item():.2f}\n')


if __name__ == '__main__':
    main()
