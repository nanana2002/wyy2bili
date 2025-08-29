import json
import time
import asyncio
import os
from datetime import datetime
import undetected_chromedriver as uc
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
RETRY_SLEEP_MINUTES = 5 # 新增：风控后重试的暂停分钟数

# --- Part 1: 网易云歌单解析 (无需改动) ---
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
    options = uc.ChromeOptions()
    options.add_argument('--disable-gpu')
    driver = None
    songs = []
    try:
        print("\n正在启动浏览器...")
        driver = uc.Chrome(options=options)
        driver.get(playlist_url)
        print("浏览器已打开。请在浏览器窗口中手动登录网易云音乐(推荐用手机号/密码)，登录完成后回到这里，按Enter键继续解析...")
        input("按Enter键以继续...")
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
    finally:
        if driver: driver.quit()
    return songs

# --- Part 2: B站收藏 ---
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

async def collect_to_bilibili(songs: list, cookies: dict, original_songs: list):
    """
    执行一轮收藏任务。
    如果成功，返回空的列表。
    如果遇到风控(412)，返回剩余的歌曲列表。
    """
    if not songs:
        return []
    
    try:
        credential = Credential(sessdata=cookies['SESSDATA'], bili_jct=cookies['bili_jct'])
        new_folder_name = datetime.now().strftime('%m%d%H%M')
        print(f"\n将创建新的收藏夹: '{new_folder_name}'")
        result = await favorite_list.create_video_favorite_list(
            title=new_folder_name,
            introduction="网易云歌单自动同步",
            private=False,
            credential=credential
        )
        folder_id = result['id']
        print(f"收藏夹创建成功! ID: {folder_id}\n")

    except Exception as e:
        print(f"\nB站操作失败（可能是创建收藏夹时出错）: {e}")
        # 检查是否是风控错误
        if "状态码：412" in str(e):
            print("创建收藏夹时遭遇风控。")
            with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f: json.dump(songs, f, ensure_ascii=False, indent=2)
            return songs # 返回整个列表以进行重试
        else:
            print("这是一个未知错误，程序将终止。")
            # 对于未知错误，我们还是保存一下进度
            with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f: json.dump(songs, f, ensure_ascii=False, indent=2)
            return None # 返回None表示不可恢复的错误

    total = len(original_songs)
    start_offset = total - len(songs)

    individually_failed_songs = []

    for i, song in enumerate(songs):
        try:
            current_index = i + start_offset
            keyword = f"{song['name']} {song['artist']}"
            print(f"--- [{current_index + 1}/{total}] 正在处理: {keyword} ---")
            search_result = await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO)
            videos = search_result.get('result', [])
            if not videos:
                print("未找到相关视频，跳过。")
                individually_failed_songs.append(song)
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
                    break
            if not found_video: 
                print('搜索结果中未找到时长合适的视频，跳过此歌曲。')
                individually_failed_songs.append(song)
            print(f"暂停 {SEARCH_SLEEP_SECONDS} 秒...")
            time.sleep(SEARCH_SLEEP_SECONDS)
        
        except Exception as e:
            print(f"\n\n在处理歌曲 '{keyword}' 时发生严重错误！")
            print(f"错误详情: {e}")
            # 检查是否是风控错误
            if "状态码：412" in str(e):
                remaining_songs = songs[i:]
                with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(remaining_songs, f, ensure_ascii=False, indent=2)
                print(f"检测到B站风控，已将剩余的 {len(remaining_songs)} 首歌曲保存到 {FAIL_LOG_FILE}")
                return remaining_songs # 返回剩余列表，触发重试
            else:
                # 其他错误，只记录当前失败的这首歌，然后继续尝试下一首
                print("此错误非风控，将记录本首歌曲并继续...")
                individually_failed_songs.append(song)

    # 如果循环正常结束，将那些只是没找到视频的歌曲写入fail.json
    if individually_failed_songs:
        with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(individually_failed_songs, f, ensure_ascii=False, indent=2)
        print(f"\n本轮任务完成！有 {len(individually_failed_songs)} 首歌曲因未找到视频而失败，已记录到 {FAIL_LOG_FILE}")
    else:
        # 仅当所有歌曲都成功时，才清空fail.json
        if os.path.exists(FAIL_LOG_FILE):
            os.remove(FAIL_LOG_FILE)
        print("\n--- 本轮全部任务成功完成！ ---")

    return [] # 返回空列表表示本轮成功

# --- 主程序入口 ---
async def main():
    print("====== 网易云 to Bilibili 收藏夹同步工具 ======")
    songs_to_process = []
    source_choice = ''
    try:
        fail_file_exists = os.path.exists(FAIL_LOG_FILE) and os.path.getsize(FAIL_LOG_FILE) > 2
        playlist_file_exists = os.path.exists(PLAYLIST_FILE) and os.path.getsize(PLAYLIST_FILE) > 2
    except FileNotFoundError:
        fail_file_exists = False
        playlist_file_exists = False
    if playlist_file_exists and fail_file_exists:
        source_choice = input("请选择要处理的歌单: [U]RL歌单(上次缓存) 或 [F]ail.json(上次失败)？ (U/F): ").strip().upper()
    if source_choice == 'F':
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
    
    # ##################################################
    # ###            新增：风控自动重试循环             ###
    # ##################################################
    current_song_list = songs_to_collect_initial
    while True:
        remaining_songs = await collect_to_bilibili(current_song_list, bili_cookies, songs_to_process)
        
        if remaining_songs is None:
            print("发生不可恢复的错误，程序终止。")
            break
        
        if not remaining_songs:
            # 返回空列表，说明全部成功
            print("\n所有歌曲均已成功处理！程序结束。")
            break
        else:
            # 返回非空列表，说明遭遇风控
            print(f"\nB站风控已触发，脚本将暂停 {RETRY_SLEEP_MINUTES} 分钟后自动重试...")
            time.sleep(RETRY_SLEEP_MINUTES * 60)
            print("暂停结束，将从 fail.json 加载剩余任务并继续...")
            # 更新待处理列表为失败的列表，进入下一次循环
            with open(FAIL_LOG_FILE, 'r', encoding='utf-8') as f:
                current_song_list = json.load(f)
    # ##################################################

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n程序被用户中断。")