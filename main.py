"""AI Рекламный Цензор — точка входа"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.ui.app_window import CensorApp


def main():
    app = CensorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
