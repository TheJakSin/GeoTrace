"""Keep a single Qt application alive across independent editor-window tests.

Qt Quick's global controls and signal registrations outlive a QML engine; tearing
its application down between tests leaves callbacks attached to deleted sources.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')

import pytest
from PySide6.QtCore import QCoreApplication,Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle


@pytest.fixture(scope='session',autouse=True)
def qt_application():
    if QGuiApplication.instance() is None:
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    if QQuickStyle.name()!='Basic':QQuickStyle.setStyle('Basic')
    application=QGuiApplication.instance() or QGuiApplication([])
    yield application
