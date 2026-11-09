"""统一路径：源码运行和打包成 exe 后都能找到同一个"程序目录"

打包后 `__file__` 在临时解包目录里，配置和日志必须写到 exe 旁边，
否则用户重开一次配置就没了。
"""
import os
import sys


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir():
    """打进 exe 的只读资源目录（onefile 时是临时解包目录）"""
    return getattr(sys, "_MEIPASS", None) or app_dir()


APP_DIR = app_dir()
LOG_DIR = os.path.join(APP_DIR, "logs")
CACHE_DIR = os.path.join(APP_DIR, "cache")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
