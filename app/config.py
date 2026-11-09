"""配置读写：程序目录下的 config.json"""
import json
import os
import copy

from .paths import APP_DIR, CONFIG_PATH  # noqa: E402

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_config.json")


def _load_default():
    with open(DEFAULT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = _load_default()
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = _deep_merge(self.data, json.load(f))
            except Exception as e:
                print(f"[config] 读取失败: {e}")
        return self.data

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[config] 保存失败: {e}")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()

    def __getitem__(self, k):
        return self.data[k]
