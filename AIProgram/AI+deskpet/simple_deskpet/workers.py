"""动画线程 + 交互线程 —— 精灵帧循环 & 点击/拖拽处理"""
import time
import random

from PySide6.QtCore import QObject, Signal, QTimer, Qt

import settings


class AnimationWorker(QObject):
    """QThread Worker：循环播放待机/随机动画"""

    sig_setimg = Signal()      # 通知 GUI 刷新图片
    sig_move = Signal(int, int)  # 通知 GUI 移动窗口 (dx, dy)

    def __init__(self, pet_conf):
        super().__init__()
        self.pet_conf = pet_conf
        self.is_killed = False
        self.is_paused = False

    def run(self):
        """主循环 —— 在 QThread 中运行"""
        print(f'[Animation] 启动角色: {self.pet_conf.petname}')
        while not self.is_killed:
            self._play_random_act()

            while self.is_paused:
                time.sleep(0.2)
                if self.is_killed:
                    return

    def kill(self):
        self.is_killed = True
        self.is_paused = False

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    # ---------- 动画选择 ----------
    def _play_random_act(self):
        """按概率选择并播放一个随机动画组"""
        # 如果没有额外随机动作，或 85% 概率走 default
        if not self.pet_conf.random_act or random.random() < 0.85:
            acts = [self.pet_conf.default]
        else:
            # 按 act_prob 加权随机选择
            r = random.random()
            cumulative = 0
            chosen = 0
            for i, prob in enumerate(self.pet_conf.act_prob):
                cumulative += prob
                if r <= cumulative:
                    chosen = i
                    break
            acts = self.pet_conf.random_act[chosen]
        self._run_acts(acts)

    # ---------- 帧播放 ----------
    def _run_acts(self, acts):
        for act in acts:
            self._run_act(act)

    def _run_act(self, act):
        """播放单个 Act 的所有帧（act_num 次循环）"""
        for _ in range(act.act_num):
            for img in act.images:
                if self.is_paused or self.is_killed:
                    return

                settings.previous_img = settings.current_img
                settings.current_img = img
                settings.previous_anchor = settings.current_anchor
                settings.current_anchor = [
                    int(i * settings.tunable_scale) for i in act.anchor
                ]
                self.sig_setimg.emit()

                # 方向位移
                if act.direction:
                    px, py = 0, 0
                    d = act.direction
                    if d == 'right':   px = act.frame_move
                    elif d == 'left':  px = -act.frame_move
                    elif d == 'up':    py = -act.frame_move
                    elif d == 'down':  py = act.frame_move
                    if px != 0 or py != 0:
                        self.sig_move.emit(int(px), int(py))

                time.sleep(act.frame_refresh)


class InteractionWorker(QObject):
    """QThread Worker：处理点击 (patpat) 和拖拽 (mousedrag) 交互"""

    sig_setimg = Signal()          # 通知 GUI 刷新图片
    sig_act_finished = Signal()    # 交互动画结束 → GUI 恢复待机动画

    def __init__(self, pet_conf):
        super().__init__()
        self.pet_conf = pet_conf
        self.is_killed = False
        self.interact = None       # None | 'patpat' | 'mousedrag'
        self._playid = 0           # 当前动画的内部帧计数
        self._expanded_frames = [] # 展开后的帧序列（含重复）

        # 定时器驱动 tick
        self._timer = QTimer()
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(self.pet_conf.interact_speed))

    def kill(self):
        self.is_killed = True
        self._timer.stop()

    # ---------- 交互控制 ----------
    def start_interact(self, interact_type):
        self.interact = interact_type
        self._playid = 0

        if interact_type == 'patpat':
            self._build_expanded_frames(self.pet_conf.patpat)

    def stop_interact(self):
        self.interact = None
        self._playid = 0
        self._expanded_frames = []
        self.sig_act_finished.emit()

    # ---------- 帧展开（patpat 用） ----------
    def _build_expanded_frames(self, act):
        """按 frame_refresh 把 Act 的帧展开为等间隔序列"""
        ticks_per_frame = max(1, round(
            act.frame_refresh * 1000 / self.pet_conf.interact_speed
        ))
        self._expanded_frames = []
        for _ in range(act.act_num):
            for img in act.images:
                self._expanded_frames.extend([img] * ticks_per_frame)

    # ---------- 每 tick 分发 ----------
    def _tick(self):
        if self.interact is None:
            return
        elif self.interact == 'patpat':
            self._do_patpat()
        elif self.interact == 'mousedrag':
            self._do_mousedrag()

    def _do_patpat(self):
        """逐帧播放 patpat 动画，播完自动停止"""
        if self._playid < len(self._expanded_frames):
            img = self._expanded_frames[self._playid]
            settings.previous_img = settings.current_img
            settings.current_img = img
            settings.previous_anchor = settings.current_anchor
            act = self.pet_conf.patpat
            settings.current_anchor = [
                int(i * settings.tunable_scale) for i in act.anchor
            ]
            self.sig_setimg.emit()
            self._playid += 1
        else:
            self.stop_interact()

    def _do_mousedrag(self):
        """拖拽时只显示拖拽图片（窗口位移由 mouseMoveEvent 直接处理）"""
        img = self.pet_conf.drag.images[0]
        settings.previous_img = settings.current_img
        settings.current_img = img
        act = self.pet_conf.drag
        settings.previous_anchor = settings.current_anchor
        settings.current_anchor = [
            int(i * settings.tunable_scale) for i in act.anchor
        ]
        self.sig_setimg.emit()
