"""
路由包
VoiceToFile — 小宇宙播客转文字
"""
from flask import Blueprint

from .podcasts import podcasts_bp
from .episodes import episodes_bp
from .queue import queue_bp
from .system import system_bp


def register_routes(app):
    """注册所有蓝图到 Flask app"""
    app.register_blueprint(podcasts_bp)
    app.register_blueprint(episodes_bp)
    app.register_blueprint(queue_bp)
    app.register_blueprint(system_bp)  # system_bp 内部路由已写完整路径（含 /api）
