#!/usr/bin/env python3
"""Collect a local A1 connection profile without echoing the access code.

This only writes a private configuration file. It neither connects to nor
changes the printer, and it does not prove that live dispatch is supported.
"""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default="", help="IP shown on the printer screen")
    args = parser.parse_args()
    host = input(f"A1 屏幕上的 IP [{args.ip}]: ").strip() or args.ip
    ipaddress.ip_address(host)
    serial = input("A1 设备序列号（设置 → 设备）: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", serial):
        raise SystemExit("序列号格式无效，未写入。")
    code = getpass.getpass("LAN Access Code（输入隐藏）: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{8}", code):
        raise SystemExit("访问码应为 8 位字母或数字，未写入。")
    firmware = input("固件版本（可留空）: ").strip()
    directory = Path.home() / ".config" / "justbuild" / "printers"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / "a1.json"
    profile = {"schema_version": "0.1", "transport": "lan", "model": "Bambu Lab A1",
               "host": host, "serial": serial, "access_code": code,
               "firmware": firmware, "mqtt_port": 8883, "ftp_port": 990}
    descriptor, temporary = tempfile.mkstemp(prefix=".a1-", suffix=".json", dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(profile, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"已保存：{target}（仅当前用户可读写）。")
    print("未连接打印机，也未发送打印指令。回到对话告诉我“配置已录入”即可。")


if __name__ == "__main__":
    main()
