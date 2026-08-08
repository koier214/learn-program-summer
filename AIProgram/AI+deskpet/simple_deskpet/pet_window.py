"""透明置顶桌宠窗口 —— 精灵显示 + 鼠标交互 + 右键菜单 + 线程管理"""
import sys

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QWidget, QLabel, QMenu, QApplication

import settings
from config import PetConfig
from workers import AnimationWorker, InteractionWorker


class PetWindow(QWidget):
    """桌面宠物主窗口"""

    def __init__(self, pet_name='yier'):
        super().__init__()

        # ---- 加载角色配置 ----
        self.pet_conf = PetConfig.init_config(pet_name)
        settings.tunable_scale = self.pet_conf.scale

        # ---- 窗口属性 ----
        self._init_window()

        # ---- UI ----
        self._init_ui()

        # ---- 初始位置（屏幕右下角） ----
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.left() + screen.width() - self.width() - 50
        y = screen.top() + screen.height() - self.height() - 100
        self.move(x, y)

        # ---- 工作线程 ----
        self._init_workers()

        # ---- 启动动画循环 ----
        self._start_animation()

    # ===================== 窗口设置 =====================
    def _init_window(self):
        """无边框、置顶、透明背景"""
        if sys.platform == 'win32':
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.SubWindow
                | Qt.NoDropShadowWindowHint
            )
        else:
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.Tool
            )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    # ===================== UI =====================
    def _init_ui(self):
        """创建 QLabel 用于显示精灵帧"""
        self.label = QLabel(self)
        self.label.setScaledContents(True)
        self.label.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)

        # 初始帧
        settings.current_img = self.pet_conf.default.images[0]
        settings.current_anchor = [0, 0]
        self._set_img()

        # 窗口尺寸 = 配置宽高
        self.setFixedSize(
            int(self.pet_conf.width),
            int(self.pet_conf.height),
        )

    def _set_img(self):
        """将 settings.current_img 显示到 QLabel 上 (GUI 线程)"""
        if settings.current_img is None:
            return

        w = settings.current_img.width()
        h = settings.current_img.height()
        self.label.setFixedSize(w, h)
        self.label.setPixmap(settings.current_img)

    def _move_window(self, dx, dy):
        """窗口位移 (AnimationWorker 方向位移用)"""
        self.move(self.pos().x() + dx, self.pos().y() + dy)

    # ===================== 线程管理 =====================
    def _init_workers(self):
        """创建 Animation 和 Interaction 两个 QThread + Worker"""
        # Animation
        self._anim_worker = AnimationWorker(self.pet_conf)
        self._anim_thread = QThread(self)
        self._anim_worker.moveToThread(self._anim_thread)

        # Interaction
        self._inter_worker = InteractionWorker(self.pet_conf)
        self._inter_thread = QThread(self)
        self._inter_worker.moveToThread(self._inter_thread)

    def _start_animation(self):
        """连接信号 → 启动线程"""
        # Animation signals → GUI
        self._anim_thread.started.connect(self._anim_worker.run)
        self._anim_worker.sig_setimg.connect(self._set_img)
        self._anim_worker.sig_move.connect(self._move_window)

        # Interaction signals → GUI
        self._inter_worker.sig_setimg.connect(self._set_img)
        self._inter_worker.sig_act_finished.connect(self._resume_animation)

        self._anim_thread.start()
        self._inter_thread.start()

    def _pause_animation(self):
        self._anim_worker.pause()

    def _resume_animation(self):
        """交互结束后恢复待机动画"""
        settings.current_img = self.pet_conf.default.images[0]
        settings.current_anchor = [0, 0]
        self._set_img()
        self._anim_worker.resume()

    # ===================== 鼠标事件 =====================
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._show_context_menu()
        elif event.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_offset = event.globalPos() - self.pos()
            self._has_moved = False
            self._pause_animation()
            self._inter_worker.start_interact('mousedrag')
            event.accept()

    def mouseMoveEvent(self, event):
        if getattr(self, '_is_dragging', False):
            new_pos = event.globalPos() - self._drag_offset
            if new_pos != self.pos():
                self.move(new_pos)
                self._has_moved = True
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_dragging = False
            if not self._has_moved:
                # 点击（没拖动）→ 播放交互动画
                self._inter_worker.start_interact('patpat')
            else:
                # 拖动结束 → 停止交互，恢复待机
                self._inter_worker.stop_interact()

    # ===================== 右键菜单 =====================
    def _show_context_menu(self):
        menu = QMenu(self)
        exit_action = menu.addAction('退出 (Exit)')
        chosen = menu.exec_(QCursor.pos())
        if chosen == exit_action:
            self._quit()

    # ===================== 退出清理 =====================
    def _quit(self):
        self._anim_worker.kill()
        self._anim_thread.quit()
        self._anim_thread.wait(3000)

        self._inter_worker.kill()
        self._inter_thread.quit()
        self._inter_thread.wait(3000)

        self.close()
        QApplication.quit()
