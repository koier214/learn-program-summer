"""一二桌宠 — 极简入口"""
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from pet_window import PetWindow


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

    window = PetWindow(pet_name='yier')
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
