import os
import torch
from models.diabetes_model import Model


def main():
    # 加载模型
    model = Model()
    save_dir = os.path.join(os.path.dirname(__file__), 'checkpoints')
    save_path = os.path.join(save_dir, 'diabetes_model.pth')
    model.load_state_dict(torch.load(save_path))
    model.eval()
    print(f'已加载模型: {save_path}')

    # 加载归一化参数
    norm_path = os.path.join(save_dir, 'norm_params.pth')
    norm = torch.load(norm_path)
    mean = norm['mean']
    std = norm['std']
    print(f'已加载归一化参数: {norm_path}')

    print('\n输入 8 个特征值（空格分隔），输入 q 退出')
    print('特征: 怀孕次数 血糖 血压 皮肤厚度 胰岛素 BMI 糖尿病遗传函数 年龄\n')

    while True:
        user_input = input('>>> ')
        if user_input.lower() == 'q':
            print('再见')
            break

        try:
            values = [float(v) for v in user_input.split()]
            if len(values) != 8:
                print(f'需要 8 个数字，你输入了 {len(values)} 个\n')
                continue
        except ValueError:
            print('请输入 8 个数字，用空格分隔\n')
            continue

        # 转成 tensor 并归一化（用训练时的 mean 和 std）
        x = torch.tensor(values, dtype=torch.float32).view(1, -1)
        x = (x - mean) / std

        # 预测
        prob = model(x).item()
        if prob >= 0.5:
            print(f'预测: 可能患病（概率 {prob:.2%}）\n')
        else:
            print(f'预测: 可能不患病（概率 {prob:.2%}）\n')


if __name__ == '__main__':
    main()
