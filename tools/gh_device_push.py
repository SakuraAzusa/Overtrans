"""用 GitHub 设备码登录并推送仓库（本机有 Steam302 之类的本地代理时会自签证书，
所以全程 verify=False —— 只用于这一次推送，不动系统设置）

用法：
    python tools/gh_device_push.py --new     # 申请新设备码，打印给用户
    python tools/gh_device_push.py --wait    # 轮询等授权，成功后建仓库并推送

设备码存在 %TEMP%\\gh_device.json，避免重复申请。
"""
import argparse
import json
import os
import subprocess
import sys
import time

import requests
import urllib3

urllib3.disable_warnings()

CLIENT_ID = "178c6fc778ccc68e1d6a"      # GitHub CLI 的公开 OAuth client id
OWNER = "SakuraAzusa"
REPO = "Overtrans"
DESC = "用于overfield快速识别翻译与回复小工具，更好的与你的国际小朋友交流"
TOPICS = ["ocr", "translate", "rapidocr", "overfield", "game-tools", "windows", "python"]
STATE = os.path.join(os.environ.get("TEMP", "."), "gh_device.json")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def http():
    s = requests.Session()
    s.trust_env = False          # 别走系统代理
    s.verify = False             # 本地代理自签证书
    return s


def new_code(s=None):
    s = s or http()
    r = s.post("https://github.com/login/device/code",
               data={"client_id": CLIENT_ID, "scope": "repo"},
               headers={"Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    d = r.json()
    json.dump({"device_code": d["device_code"], "user_code": d["user_code"],
               "client_id": CLIENT_ID}, open(STATE, "w"))
    print(f"打开 {d['verification_uri']} 输入代码：{d['user_code']}")
    print(f"（{d['expires_in'] // 60} 分钟内有效，授权后回来告诉我）")
    return d


def poll(s=None, timeout=900):
    s = s or http()
    st = json.load(open(STATE))
    interval = 5
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = s.post("https://github.com/login/oauth/access_token",
                   data={"client_id": st["client_id"], "device_code": st["device_code"],
                         "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                   headers={"Accept": "application/json"}, timeout=20)
        d = r.json()
        err = d.get("error")
        if d.get("access_token"):
            return d["access_token"]
        if err == "authorization_pending":
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        raise SystemExit(f"授权失败: {d}")
    raise SystemExit("等待超时，重新运行 --new 申请新设备码")


def api(s, token, method, path, **kw):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    r = s.request(method, "https://api.github.com" + path, headers=headers, timeout=30, **kw)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", action="store_true", help="申请新的设备码")
    ap.add_argument("--wait", action="store_true", help="等待授权并推送")
    args = ap.parse_args()

    s = http()
    if args.new:
        new_code(s)
        return
    if not args.wait:
        ap.print_help()
        return

    token = poll(s)
    print("授权成功，token 已拿到（不打印）")

    r = api(s, token, "POST", "/user/repos",
            json={"name": REPO, "description": DESC, "private": False,
                  "has_issues": True, "has_wiki": False, "auto_init": False})
    if r.status_code == 201:
        print("仓库已创建:", r.json()["html_url"])
    elif r.status_code == 422:
        print("仓库已存在，继续推送")
    else:
        print("建仓库返回", r.status_code, r.text[:200])

    env = dict(os.environ, GIT_SSL_NO_VERIFY="true", GIT_TERMINAL_PROMPT="0")
    url = f"https://x-access-token:{token}@github.com/{OWNER}/{REPO}.git"

    def git(*a, check=True):
        p = subprocess.run(["git", *a], cwd=ROOT, env=env, capture_output=True, text=True)
        if check and p.returncode != 0:
            print("git", " ".join(a), "失败:", p.stderr[-400:])
        return p

    git("remote", "remove", "origin", check=False)
    git("remote", "add", "origin", url)
    p = git("push", "-u", "origin", "main", check=False)
    print((p.stdout or "").strip()[-300:])
    if p.returncode != 0:
        print("推送失败:", (p.stderr or "").strip()[-400:])
        return
    git("remote", "set-url", "origin", f"https://github.com/{OWNER}/{REPO}.git")

    api(s, token, "PATCH", f"/repos/{OWNER}/{REPO}",
        json={"description": DESC, "has_issues": True})
    api(s, token, "PUT", f"/repos/{OWNER}/{REPO}/topics", json={"names": TOPICS})
    print(f"完成: https://github.com/{OWNER}/{REPO}")
    print("记得手动建 Release 并上传 Overtrans-1.0.0-win64.zip")


if __name__ == "__main__":
    main()
