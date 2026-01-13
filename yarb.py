#!/usr/bin/python3

import os
import re
import json
import time
import asyncio
import schedule
import pyfiglet
import argparse
import datetime
import listparser
import feedparser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from bot import *
from utils import *

import requests
requests.packages.urllib3.disable_warnings()

today = datetime.datetime.now().strftime("%Y-%m-%d")

def update_today(data: list=[]):
    """更新today"""
    root_path = Path(__file__).absolute().parent
    data_path = root_path.joinpath('temp_data.json')
    today_path = root_path.joinpath('today.md')
    archive_path = root_path.joinpath(f'archive/{today.split("-")[0]}/{today}.md')

    if not data and data_path.exists():
        with open(data_path, 'r') as f1:
            data = json.load(f1)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with open(today_path, 'w+') as f1, open(archive_path, 'w+') as f2:
        content = f'# 每日安全资讯（{today}）\n\n'
        for item in data:
            (feed, value), = item.items()
            content += f'- {feed}\n'
            for title, url in value.items():
                content += f'  - [{title}]({url})\n'
        f1.write(content)
        f2.write(content)


def update_rss(rss: dict, proxy_url=''):
    """更新订阅源文件"""
    proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {'http': None, 'https': None}

    (key, value), = rss.items()
    rss_path = root_path.joinpath(f'rss/{value["filename"]}')

    result = None
    if url := value.get('url'):
        r = requests.get(value['url'], proxies=proxy)
        if r.status_code == 200:
            with open(rss_path, 'w+') as f:
                f.write(r.text)
            print(f'[+] 更新完成：{key}')
            result = {key: rss_path}
        elif rss_path.exists():
            print(f'[-] 更新失败，使用旧文件：{key}')
            result = {key: rss_path}
        else:
            print(f'[-] 更新失败，跳过：{key}')
    else:
        print(f'[+] 本地文件：{key}')

    return result


def parseThread(conf: dict, url: str, proxy_url=''):
    """获取文章线程"""
    def filter(title: str):
        for i in conf['exclude']:
            if i in title:
                return False
        return True

    proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {'http': None, 'https': None}
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }

    title = ''
    result = {}
    try:
        r = requests.get(url, timeout=10, headers=headers, verify=False, proxies=proxy)
        r = feedparser.parse(r.content)
        title = r.feed.title
        for entry in r.entries:
            d = entry.get('published_parsed') or entry.get('updated_parsed')
            yesterday = datetime.date.today() + datetime.timedelta(-1)
            pubday = datetime.date(d[0], d[1], d[2])
            if pubday == yesterday and filter(entry.title):
                result[entry.title] = entry.link
        console.print(f'[+] {title}\t{url}\t{len(result.values())}/{len(r.entries)}', style='bold green')
    except Exception as e:
        console.print(f'[-] failed: {url}', style='bold red')
        print(e)
    return title, result


async def init_bot(conf: dict, proxy_url=''):
    bots = []
    for name, v in conf.items():
        if v['enabled']:
            key = os.getenv(v['secrets']) or v['key']
            if name == 'telegram':
                bot = telegramBot(key, v['chat_id'], proxy_url)
                if await bot.test_connect():
                    bots.append(bot)
            else:
                bot = globals()[f'{name}Bot'](key, proxy_url)
                bots.append(bot)
    return bots


def cleanup():
    qqBot.kill_server()


async def job(args):
    print(f'{pyfiglet.figlet_format("yarb")}\n{today}')

    global root_path
    root_path = Path(__file__).absolute().parent

    config_path = Path(args.config).expanduser().absolute() if args.config else root_path / 'config.json'
    with open(config_path) as f:
        conf = json.load(f)

    feeds = init_rss(conf['rss'], args.update)
    results = []

    with ThreadPoolExecutor(100) as executor:
        tasks = [executor.submit(parseThread, conf['keywords'], url) for url in feeds]
        for task in as_completed(tasks):
            title, result = task.result()
            if result:
                results.append({title: result})

    update_today(results)

    bots = await init_bot(conf['bot'])
    for bot in bots:
        await bot.send(bot.parse_results(results))

    cleanup()


# ================= 飞书 post 富文本增强 =================

MAX_BODY_SIZE = 20 * 1024
LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def split_text_by_bytes(text: str, max_bytes: int):
    buf = ""
    for ch in text:
        if len((buf + ch).encode("utf-8")) > max_bytes:
            yield buf
            buf = ch
        else:
            buf += ch
    if buf:
        yield buf


def md_to_post(md_text: str):
    title = ""
    paragraphs = []

    for line in md_text.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("# "):
            title = line[2:].strip()
            continue

        if line.startswith("- "):
            line = line[2:].strip()

        para = []
        pos = 0
        for m in LINK_RE.finditer(line):
            if m.start() > pos:
                para.append({"tag": "text", "text": line[pos:m.start()]})
            para.append({"tag": "a", "text": m.group(1), "href": m.group(2)})
            pos = m.end()
        if pos < len(line):
            para.append({"tag": "text", "text": line[pos:]})

        paragraphs.append(para)

    return title or "每日安全资讯", paragraphs


def build_post_payload(title, content):
    return json.dumps({
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": content
                }
            }
        }
    }, ensure_ascii=False).encode("utf-8")


def feishu_push_from_file(md_path: Path):
    webhook = os.getenv("FEISHU_HOOK")
    if not webhook or not md_path.exists():
        return False

    title, paragraphs = md_to_post(md_path.read_text(encoding="utf-8"))
    headers = {"Content-Type": "application/json"}

    def send(buf):
        payload = build_post_payload(title, buf)
        print(f"[INFO] Sending payload size={len(payload)}")
        r = requests.post(webhook, headers=headers, data=payload, timeout=5)
        print(f"[+] Feishu push status: {r.status_code}, {r.text}")

    buffer = []

    for para in paragraphs:
        if len(build_post_payload(title, buffer + [para])) <= MAX_BODY_SIZE:
            buffer.append(para)
            continue

        if buffer:
            send(buffer)
            buffer = []

        new_para = []
        for node in para:
            if node["tag"] != "text":
                if len(build_post_payload(title, [new_para + [node]])) > MAX_BODY_SIZE:
                    send([new_para])
                    new_para = [node]
                else:
                    new_para.append(node)
            else:
                for part in split_text_by_bytes(node["text"], MAX_BODY_SIZE // 2):
                    tnode = {"tag": "text", "text": part}
                    if len(build_post_payload(title, [new_para + [tnode]])) > MAX_BODY_SIZE:
                        send([new_para])
                        new_para = [tnode]
                    else:
                        new_para.append(tnode)

        if new_para:
            buffer.append(new_para)

    if buffer:
        send(buffer)

    return True


def argument():
    parser = argparse.ArgumentParser()
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--cron', type=str)
    parser.add_argument('--config', type=str)
    return parser.parse_args()


async def main():
    args = argument()
    if args.cron:
        schedule.every().day.at(args.cron).do(job, args)
        while True:
            schedule.run_pending()
            await asyncio.sleep(1)
    else:
        await job(args)


if __name__ == '__main__':
    root_path = Path(__file__).absolute().parent
    asyncio.run(main())
    feishu_push_from_file(root_path / "today.md")
