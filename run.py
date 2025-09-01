import json
import time
import asyncio
import os
from datetime import datetime
# 【核心改动】同时导入 undetected_chromedriver 和标准的 selenium
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bilibili_api import favorite_list, search, Credential, video

# --- 配置项 ---
BILI_COOKIE_FILE = 'bili_cookie.json'
NETEASE_CONFIG_FILE = 'playlist_config.json'
PLAYLIST_FILE = 'playlist.json'
FAIL_LOG_FILE = 'fail.json'
SEARCH_SLEEP_SECONDS = 3
RETRY_SLEEP_MINUTES = 5

# --- Part 1: 网易云歌单解析 (已修改为标准 Selenium 连接模式) ---
def parse_netease_playlist():
    config = {}
    if os.path.exists(NETEASE_CONFIG_FILE):
        with open(NETEASE_CONFIG_FILE, 'r', encoding='utf-8') as f: config = json.load(f)
    update_url = input('是否要更改网易云歌单URL？(y/n): ').strip().lower()
    if update_url == 'y' or 'playlist_url' not in config:
        playlist_url = input('请输入网易云歌单页面URL: ').strip()
        config['playlist_url'] = playlist_url
    else:
        playlist_url = config['playlist_url']
        print(f'已加载上次使用的URL: {playlist_url}')
    with open(NETEASE_CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(config, f, ensure_ascii=False, indent=2)
    
    # ##################################################
    # ###         最终核心改动：使用标准 Selenium         ###
    # ##################################################
    
    # 1. 使用标准的 ChromeOptions
    options = ChromeOptions()
    
    print("\n" + "="*50)
    print("【重要】请确保你已经按照指引，手动启动了带有调试端口的Chrome浏览器。")
    print("并且已经在该浏览器中完成了网易云音乐的登录。")
    print("="*50 + "\n")
    input("确认无误后，请按Enter键，脚本将尝试连接到该浏览器...")

    # 2. 设置调试器地址以连接到已打开的浏览器实例
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9527")
    # ##################################################

    driver = None
    songs = []
    try:
        print("\n正在附加到已打开的浏览器...")
        # 3. 使用标准的 webdriver.Chrome 来进行连接
        driver = webdriver.Chrome(options=options)
        
        # 验证连接是否成功 (获取当前窗口的标题)
        print(f"连接成功！当前窗口标题: {driver.title}")
        print(f"正在跳转到目标歌单: {playlist_url}")
        driver.get(playlist_url)

        print("等待页面加载，查找歌单iframe...")
        wait = WebDriverWait(driver, 30)
        iframe = wait.until(EC.presence_of_element_located((By.ID, 'g_iframe')))
        driver.switch_to.frame(iframe)
        print("已切换到iframe，等待歌单表格加载...")
        table = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'm-table')))
        print("歌单表格已加载，开始解析歌曲列表...")
        trs = table.find_elements(By.TAG_NAME, 'tr')
        for tr in trs[1:]:
            try:
                name_b = tr.find_element(By.CSS_SELECTOR, 'td:nth-child(2) span.txt b')
                name = name_b.get_attribute('title').strip()
                artist_span = tr.find_element(By.CSS_SELECTOR, 'td:nth-child(4) span')
                artist = artist_span.get_attribute('title').strip()
                if name and artist: songs.append({'name': name, 'artist': artist})
            except Exception: continue
        print(f"\n成功采集到 {len(songs)} 首歌曲！")
        with open(PLAYLIST_FILE, 'w', encoding='utf-8') as f: json.dump(songs, f, ensure_ascii=False, indent=2)
        print(f"歌单已保存到 {PLAYLIST_FILE}")

    except Exception as e:
        print(f"解析网易云页面时发生严重错误: {e}")
        print("请检查：1. 是否已按要求启动调试模式的Chrome？ 2. 是否已在该Chrome中登录？ 3. 调试端口9527是否被其他程序占用或被防火墙拦截？")
    finally:
        if driver:
            print("\n解析完成。脚本不会关闭你手动打开的浏览器，你可以手动关闭它。")
    return songs


# --- Part 2 & main function (无需改动) ---
# ... (文件的其余部分保持不变)
def get_bilibili_credential():
    if os.path.exists(BILI_COOKIE_FILE):
        try:
            with open(BILI_COOKIE_FILE, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            if all(k in cookies for k in ['SESSDATA', 'bili_jct']):
                choice = input("\n检测到已保存的B站cookie，是否要更新？(y/n): ").strip().lower()
                if choice != 'y':
                    print('已加载本地B站cookie。')
                    return cookies
        except Exception:
            print("本地B站cookie文件解析失败。")
    print('\n请输入新的B站cookie信息。')
    sessdata = input('请输入你的 B 站 SESSDATA: ').strip()
    bili_jct = input('请输入你的 B 站 bili_jct: ').strip()
    cookies = {'SESSDATA': sessdata, 'bili_jct': bili_jct}
    with open(BILI_COOKIE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cookies, f, ensure_ascii=False)
    print("B站cookie已更新并保存。")
    return cookies

async def collect_to_bilibili(songs: list, credential: Credential, original_songs: list, folder_id: int):
    if not songs:
        return (0, [])
    total = len(original_songs)
    start_offset = total - len(songs)
    this_run_failed_songs = []
    consecutive_not_found_count = 0
    for i, song in enumerate(songs):
        try:
            current_index = i + start_offset
            keyword = f"{song['name']} {song['artist']}"
            print(f"--- [{current_index + 1}/{total}] 正在处理: {keyword} ---")
            search_result = await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO)
            videos = search_result.get('result', [])
            if not videos:
                print("未找到相关视频（可能为B站软风控），记录为失败。")
                this_run_failed_songs.append(song)
                consecutive_not_found_count += 1
                print(f"“未找到视频”连续次数: {consecutive_not_found_count}/2")
                if consecutive_not_found_count >= 2:
                    print("连续2次未找到视频，判定为B站风控！")
                    remaining_songs = this_run_failed_songs + songs[i:]
                    unique_remaining_songs = [dict(t) for t in {tuple(d.items()) for d in remaining_songs}]
                    with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f: json.dump(unique_remaining_songs, f, ensure_ascii=False, indent=2)
                    print(f"已将本轮失败及剩余的 {len(unique_remaining_songs)} 首歌曲保存到 {FAIL_LOG_FILE}")
                    return (1, unique_remaining_songs)
                print(f"暂停 {SEARCH_SLEEP_SECONDS} 秒...")
                time.sleep(SEARCH_SLEEP_SECONDS)
                continue
            found_video = False
            for video_info in videos:
                duration_str = video_info.get('duration', '0:0')
                try:
                    parts = list(map(int, duration_str.split(':')))
                    total_seconds = sum(part * 60**i for i, part in enumerate(reversed(parts)))
                except (ValueError, TypeError): total_seconds = 0
                if 60 < total_seconds < 600 and 'bvid' in video_info:
                    bvid = video_info['bvid']
                    title = video_info.get('title', '').replace('<em class="keyword">', '').replace('</em>', '')
                    print(f"找到合适视频: {title} (BVID: {bvid}, 时长: {duration_str})")
                    v = video.Video(bvid=bvid, credential=credential)
                    await v.set_favorite(add_media_ids=[folder_id])
                    print(f"收藏成功！")
                    found_video = True
                    consecutive_not_found_count = 0
                    break
            if not found_video: 
                print('搜索结果中未找到时长合适的视频，记录为失败。')
                this_run_failed_songs.append(song)
                consecutive_not_found_count += 1
                print(f"“未找到视频”连续次数: {consecutive_not_found_count}/2")
                if consecutive_not_found_count >= 2:
                    print("连续2次未找到视频，判定为B站风控！")
                    remaining_songs = this_run_failed_songs + songs[i:]
                    unique_remaining_songs = [dict(t) for t in {tuple(d.items()) for d in remaining_songs}]
                    with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f: json.dump(unique_remaining_songs, f, ensure_ascii=False, indent=2)
                    print(f"已将本轮失败及剩余的 {len(unique_remaining_songs)} 首歌曲保存到 {FAIL_LOG_FILE}")
                    return (1, unique_remaining_songs)
            print(f"暂停 {SEARCH_SLEEP_SECONDS} 秒...")
            time.sleep(SEARCH_SLEEP_SECONDS)
        except Exception as e:
            print(f"\n\n在处理歌曲 '{keyword}' 时发生严重错误！")
            print(f"错误详情: {e}")
            remaining_songs = this_run_failed_songs + songs[i:]
            unique_remaining_songs = [dict(t) for t in {tuple(d.items()) for d in remaining_songs}]
            with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(unique_remaining_songs, f, ensure_ascii=False, indent=2)
            if "状态码：412" in str(e):
                print(f"检测到B站风控，已将本轮失败及剩余的 {len(unique_remaining_songs)} 首歌曲保存到 {FAIL_LOG_FILE}")
                return (1, unique_remaining_songs)
            else:
                print(f"发生未知严重错误，已将本轮失败及剩余的 {len(unique_remaining_songs)} 首歌曲保存到 {FAIL_LOG_FILE}")
                return (-1, unique_remaining_songs)
    if this_run_failed_songs:
        with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(this_run_failed_songs, f, ensure_ascii=False, indent=2)
        print(f"\n本轮任务完成！有 {len(this_run_failed_songs)} 首歌曲未成功收藏，已记录到 {FAIL_LOG_FILE}")
        return (2, this_run_failed_songs)
    else:
        if os.path.exists(FAIL_LOG_FILE):
            os.remove(FAIL_LOG_FILE)
        print("\n--- 本轮全部任务成功完成！ ---")
        return (0, [])

async def main():
    print("====== 网易云 to Bilibili 收藏夹同步工具 ======")
    songs_to_process = []
    source_choice = ''
    is_retry_from_fail_json = False
    try:
        fail_file_exists = os.path.exists(FAIL_LOG_FILE) and os.path.getsize(FAIL_LOG_FILE) > 2
        playlist_file_exists = os.path.exists(PLAYLIST_FILE) and os.path.getsize(PLAYLIST_FILE) > 2
    except FileNotFoundError:
        fail_file_exists = False
        playlist_file_exists = False
    if playlist_file_exists and fail_file_exists:
        source_choice = input("请选择要处理的歌单: [U]RL歌单(上次缓存) 或 [F]ail.json(上次失败)？ (U/F): ").strip().upper()
    if source_choice == 'F':
        is_retry_from_fail_json = True
        print(f"将从 {FAIL_LOG_FILE} 加载失败列表。")
        with open(FAIL_LOG_FILE, 'r', encoding='utf-8') as f:
            songs_to_process = json.load(f)
    else:
        if playlist_file_exists:
            print(f"检测到本地已缓存歌单 {PLAYLIST_FILE}。")
            update_choice = input("是否要从网易云重新获取最新歌单？(y/n): ").strip().lower()
            if update_choice == 'y':
                songs_to_process = parse_netease_playlist()
            else:
                with open(PLAYLIST_FILE, 'r', encoding='utf-8') as f:
                    songs_to_process = json.load(f)
        else:
            print("未找到本地缓存歌单，将从网易云获取。")
            songs_to_process = parse_netease_playlist()
    if not songs_to_process:
        print("\n未能获取到任何歌曲，程序退出。")
        return
    print(f"\n当前歌单总共有 {len(songs_to_process)} 首歌曲。")
    start_index = 0
    start_choice = input("是否要从特定歌曲开始？(y/n): ").strip().lower()
    if start_choice == 'y':
        while True:
            start_name = input("请输入要开始的歌曲名 (输入部分名称即可, 直接回车则从头开始): ").strip()
            if not start_name:
                start_index = 0
                print("将从头开始处理。")
                break
            found_index = -1
            for i, song in enumerate(songs_to_process):
                if start_name.lower() in song['name'].lower():
                    found_index = i
                    full_name = f"{song['name']} - {song['artist']}"
                    print(f"找到歌曲: [{i+1}] {full_name}，将从此首歌曲开始。")
                    break
            if found_index != -1:
                start_index = found_index
                break
            else:
                print("未在歌单中找到包含该名称的歌曲，请重新输入。")
    songs_to_collect_initial = songs_to_process[start_index:]
    print(f"准备处理 {len(songs_to_collect_initial)} 首歌曲 (从第 {start_index + 1} 首开始)。")
    bili_cookies = get_bilibili_credential()
    credential = Credential(sessdata=bili_cookies['SESSDATA'], bili_jct=bili_cookies['bili_jct'])
    folder_id = None
    try:
        new_folder_name = datetime.now().strftime('%m%d%H%M')
        print(f"\n正在创建本次任务的唯一收藏夹: '{new_folder_name}'")
        result = await favorite_list.create_video_favorite_list(
            title=new_folder_name,
            introduction="网易云歌单自动同步",
            private=False,
            credential=credential
        )
        folder_id = result['id']
        print(f"收藏夹创建成功! ID: {folder_id}\n")
    except Exception as e:
        print(f"\n创建初始收藏夹失败，程序无法继续: {e}")
        return
    current_song_list = songs_to_collect_initial
    while True:
        status, returned_songs = await collect_to_bilibili(current_song_list, credential, songs_to_process, folder_id)
        if status == 0:
            print("\n所有歌曲均已成功处理！程序结束。")
            break
        elif status == 1:
            is_retry_from_fail_json = True
            print(f"\nB站风控已触发，脚本将暂停 {RETRY_SLEEP_MINUTES} 分钟后自动重试...")
            time.sleep(RETRY_SLEEP_MINUTES * 60)
            print("暂停结束，将从 fail.json 加载剩余任务并继续...")
            with open(FAIL_LOG_FILE, 'r', encoding='utf-8') as f:
                current_song_list = json.load(f)
        elif status == 2:
            if is_retry_from_fail_json:
                print("\n已完成对 'fail.json' 的重试，但仍有歌曲无法成功收藏。")
                print("这些可能是永久性失败（例如B站确实没有相关视频），程序将不再自动重试。")
                print(f"请检查最终的 {FAIL_LOG_FILE} 文件查看详情。")
                break
            else:
                print("\n首次任务运行完成，但有部分歌曲无法成功收藏。")
                print(f"你可以稍后重新运行脚本，并选择从 {FAIL_LOG_FILE} 开始，以重试这些失败的歌曲。")
                break
        elif status == -1:
            print("发生未知严重错误，程序终止。请检查 fail.json 文件以恢复进度。")
            break

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n程序被用户中断。")