#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import requests
import json

def feishu_push(content: str):
    """
    使用 GitHub Actions Secret FEISHU_HOOK 测试飞书机器人推送
    """
    webhook = os.getenv("FEISHU_HOOK")
    if not webhook:
        print("[-] FEISHU_HOOK not set in environment variables.")
        return False

    payload = {
        "msg_type": "text",
        "content": {"text": content}
    }

    try:
        r = requests.post(webhook, headers={"Content-Type": "application/json"}, json=payload, timeout=5)
        if r.status_code == 200:
            print("[+] Feishu message sent successfully.")
            return True
        else:
            print(f"[-] Feishu push failed: {r.status_code}, {r.text}")
            return False
    except Exception as e:
        print(f"[-] Exception when sending Feishu message: {e}")
        return False


if __name__ == "__main__":
    test_message = "🚀 测试消息：GitHub Actions Secret 成功读取！"
    feishu_push(test_message)
