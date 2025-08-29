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
SEARCH_SLEEP_SECONDS = 3 # 新增：每次搜索操作之间的延时秒数

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
    if not songs:
        print("歌曲列表为空。")
        return
    
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
        print("请检查B站cookie是否正确或已过期。")
        with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(songs, f, ensure_ascii=False, indent=2)
        print(f"\n已将全部 {len(songs)} 首待处理歌曲保存到 {FAIL_LOG_FILE}")
        return

    total = len(original_songs)
    start_offset = total - len(songs)

    try:
        for i, song in enumerate(songs):
            current_index = i + start_offset
            keyword = f"{song['name']} {song['artist']}"
            print(f"--- [{current_index + 1}/{total}] 正在处理: {keyword} ---")

            search_result = await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO)
            videos = search_result.get('result', [])
            
            if not videos:
                print("未找到相关视频，跳过。")
                time.sleep(SEARCH_SLEEP_SECONDS) # 即使没找到也延时
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

            # ##################################################
            # ###          关键改动：增加搜索延时           ###
            # ##################################################
            print(f"暂停 {SEARCH_SLEEP_SECONDS} 秒...")
            time.sleep(SEARCH_SLEEP_SECONDS)
            # ##################################################
        
        print("\n--- 全部任务成功完成！ ---")
        if os.path.exists(FAIL_LOG_FILE):
            os.remove(FAIL_LOG_FILE)

    except Exception as e:
        print(f"\n\n在处理歌曲 '{keyword}' 时发生严重错误，疑似B站风控！")
        print(f"错误详情: {e}")
        
        remaining_songs = songs[i:]
        if remaining_songs:
            with open(FAIL_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(remaining_songs, f, ensure_ascii=False, indent=2)
            print(f"已将剩余的 {len(remaining_songs)} 首歌曲保存到 {FAIL_LOG_FILE} 以便下次继续。")
        return


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

    songs_to_collect = songs_to_process[start_index:]
    print(f"准备处理 {len(songs_to_collect)} 首歌曲 (从第 {start_index + 1} 首开始)。")
    
    bili_cookies = get_bilibili_credential()
    await collect_to_bilibili(songs_to_collect, bili_cookies, songs_to_process)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n程序被用户中断。")