"""全局共享状态 —— 所有线程和 GUI 通过此模块读写当前帧数据"""
import os

# 项目根目录
BASEDIR = os.path.dirname(os.path.abspath(__file__))

# 当前显示的帧图片（由 Worker 线程写入，GUI 线程读取）
current_img = None
previous_img = None

# 帧锚点偏移（用于对齐不同尺寸的帧）
current_anchor = [0, 0]
previous_anchor = [0, 0]

# 角色缩放比例（运行时可变）
tunable_scale = 1.0
