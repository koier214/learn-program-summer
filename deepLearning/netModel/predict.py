import torch
from PIL import Image
from torchvision import transforms
from models.netModel import Net
import os

# 重建模型结构（必须和训练时完全一致）
model = Net()

# 加载训练好的权重
checkpoint_dir = os.path.dirname(__file__)
checkpoint_path = os.path.join(checkpoint_dir, 'checkpoints', 'mnist_model.pth')
model.load_state_dict(torch.load(checkpoint_path))
model.eval()  # 推理模式：关闭 Dropout/BatchNorm 的训练行为

def build_transform(invert=False):
    """构建预处理流水线，invert=True 时把白底黑字转成黑底白字"""
    layers = [
        transforms.Grayscale(num_output_channels=1),  # 转单通道灰度
        transforms.Resize((28, 28)),                   # 缩放到 28×28
        transforms.ToTensor(),                         # 转张量，值范围 [0, 1]
    ]
    if invert:
        layers.append(transforms.Lambda(lambda x: 1.0 - x))  # 反色
    layers.append(transforms.Normalize((0.1307,), (0.3081,))) # 和 MNIST 训练一致的归一化
    return transforms.Compose(layers)

def predict(image_path, invert=False):
    """输入图片路径和是否反色，返回预测的数字 (0-9)"""
    transform = build_transform(invert)
    img = Image.open(image_path)
    img_tensor = transform(img)          # shape: (1, 28, 28)
    img_tensor = img_tensor.unsqueeze(0) # 加 batch 维度 → (1, 1, 28, 28)

    with torch.no_grad():                # 不计算梯度
        output = model(img_tensor)       # shape: (1, 10)
        _, predicted = torch.max(output, dim=1)
    return predicted.item()

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: python predict.py <image_path> [--invert]')
        print('  --invert  白底黑字图片需要此参数，黑底白字不需要')
        sys.exit(1)

    image_path = sys.argv[1]
    invert = '--invert' in sys.argv      # 命令行里有 --invert 就反色
    result = predict(image_path, invert=invert)
    print(f'Predicted digit: {result}')
