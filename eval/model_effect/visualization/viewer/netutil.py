#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端口自愈（杀本脚本旧实例 / 改用空闲端口）+ 启动后自动打开浏览器。纯标准库，无项目内依赖。"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path


def port_free(port: int) -> bool:
    """能否在 0.0.0.0:port 上 bind（判断端口是否空闲）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def listen_inodes(port: int) -> set[str]:
    """从 /proc/net/tcp{,6} 找到在 port 上 LISTEN 的 socket inode 集合（纯读文件，无需外部命令）。"""
    inodes: set[str] = set()
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(fn).read_text().splitlines()[1:]
        except Exception:  # noqa: BLE001
            continue
        for ln in lines:
            f = ln.split()
            if len(f) < 10 or f[3] != "0A":      # f[3]==0A 即 TCP_LISTEN
                continue
            try:
                if int(f[1].rsplit(":", 1)[1], 16) == port:
                    inodes.add(f[9])             # f[9] = socket inode
            except Exception:  # noqa: BLE001
                continue
    return inodes


def pids_on_port(port: int) -> list[int]:
    """占用 port 的 LISTEN 进程 pid 列表：优先解析 /proc（Linux 原生），回退 lsof/ss。"""
    inodes = listen_inodes(port)
    if inodes:
        pids: list[int] = []
        for pd in Path("/proc").glob("[0-9]*"):
            try:
                for fd in (pd / "fd").iterdir():
                    try:
                        tgt = os.readlink(fd)
                    except OSError:
                        continue
                    if tgt.startswith("socket:[") and tgt[8:-1] in inodes:
                        pids.append(int(pd.name))
                        break
            except OSError:                      # 进程已退出/无权限
                continue
        if pids:
            return sorted(set(pids))
    # 回退：lsof / ss（补全 PATH，非 Linux 或 /proc 不可用时）
    import subprocess
    env = dict(os.environ, PATH=os.environ.get("PATH", "") + ":/usr/sbin:/sbin:/usr/bin:/bin")
    for cmd in (["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                ["ss", "-ltnpH", f"sport = :{port}"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env).stdout
        except Exception:  # noqa: BLE001
            continue
        got = sorted({int(p) for p in (re.findall(r"pid=(\d+)", out)
                                       or re.findall(r"^\s*(\d+)\s*$", out, re.M))})
        if got:
            return got
    return []


def cmd_of(pid: int) -> str:
    """尽量取进程命令行（Linux /proc 优先，回退 ps）。"""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="ignore")
    except Exception:  # noqa: BLE001
        import subprocess
        try:
            return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                  capture_output=True, text=True, timeout=5).stdout
        except Exception:  # noqa: BLE001
            return ""


def ensure_port(port: int) -> int:
    """Use the requested port or report a conflict; never signal its owner."""
    if not 1 <= int(port) <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    if not port_free(port):
        owners = pids_on_port(port)
        raise OSError(f'Port {port} is occupied (PIDs: {owners}). Stop the intended MINT instance explicitly or select another port.')
    return port


def _browser_candidates() -> list[str]:
    """GUI/remote browser openers only; skip terminal browsers that dump HTML."""
    candidates: list[str] = []
    browser = os.environ.get("BROWSER", "")
    if browser:
        candidates.extend(x for x in browser.split(os.pathsep) if x.strip())
    if os.name == "posix":
        candidates.extend(["xdg-open", "gio open", "sensible-browser", "open"])
    return candidates


def _spawn_browser(cmd: str, url: str) -> bool:
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return False
    if not argv:
        return False
    exe = argv[0]
    if Path(exe).name in {"w3m", "lynx", "links", "elinks", "www-browser"}:
        return False
    if os.path.isabs(exe):
        if not Path(exe).exists():
            return False
    elif shutil.which(exe) is None:
        return False

    if any("%s" in part for part in argv):
        argv = [part.replace("%s", url) for part in argv]
    else:
        argv.append(url)

    try:
        with open(os.devnull, "rb") as null_in, open(os.devnull, "wb") as null_out:
            subprocess.Popen(argv, stdin=null_in, stdout=null_out, stderr=null_out,
                             close_fds=True, start_new_session=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def open_browser(port: int) -> None:
    """后台线程里等 Flask 起来再尝试自动打开浏览器，并吞掉 opener 自身输出。"""
    import time as _t
    _t.sleep(1.2)
    url = f"http://localhost:{port}/"
    for cmd in _browser_candidates():
        if _spawn_browser(cmd, url):
            print(f"[open] 已尝试自动打开浏览器：{url}", flush=True)
            return
    print(f"[open] 未找到可用图形/远端浏览器命令，请手动访问：{url}", flush=True)
