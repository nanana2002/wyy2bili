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
COLLECT_SLEEP_SECONDS = 5
LONG_BREAK_MINUTES = 10

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

async def collect_to_bilibili(songs: list, cookies: dict):
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
        
        # ##################################################
        # ###               最终的唯一修正               ###
        # ##################################################
        
        # 修正：直接从返回结果中获取 'id'
        folder_id = result['id']
        
        # ##################################################
        
        print(f"收藏夹创建成功! ID: {folder_id}\n")

    except Exception as e:
        print(f"\nB站操作失败: {e}")
        if 'result' in locals():
            print("B站API返回内容:", result)
        print("请检查B站cookie是否正确或已过期。")
        return

    total = len(songs)
    collect_count = 0
    for i, song in enumerate(songs):
        keyword = f"{song['name']} {song['artist']}"
        print(f"--- [{i+1}/{total}] 正在处理: {keyword} ---")
        try:
            search_result = await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO)
            videos = search_result.get('result', [])
            if not videos:
                print("未找到相关视频，跳过。")
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
                    print(f"收藏成功！休眠 {COLLECT_SLEEP_SECONDS} 秒...")
                    time.sleep(COLLECT_SLEEP_SECONDS)
                    collect_count += 1
                    if collect_count > 0 and collect_count % 50 == 0:
                        print(f"已连续收藏50首，为防止风控，将暂停 {LONG_BREAK_MINUTES} 分钟...")
                        time.sleep(LONG_BREAK_MINUTES * 60)
                    found_video = True
                    break
            if not found_video: print('搜索结果中未找到时长合适的视频，跳过此歌曲。')
        except Exception as e:
            print(f"处理歌曲 '{keyword}' 时发生错误: {e}")
            continue
    print("\n--- 全部任务完成！ ---")

# --- 主程序入口 ---
async def main():
    print("====== 网易云 to Bilibili 收藏夹同步工具 ======")
    songs_to_collect = []
    if os.path.exists(PLAYLIST_FILE):
        try:
            with open(PLAYLIST_FILE, 'r', encoding='utf-8') as f:
                local_songs = json.load(f)
                if local_songs:
                     print(f"检测到本地已保存 {len(local_songs)} 首歌曲。")
                     update_choice = input("是否要从网易云重新获取最新歌单？(y/n): ").strip().lower()
                     if update_choice != 'y':
                         print("将使用本地缓存的歌单。")
                         songs_to_collect = local_songs
        except (json.JSONDecodeError, FileNotFoundError): pass
    if not songs_to_collect:
        if os.path.exists(PLAYLIST_FILE): print("将从网易云获取最新歌单...")
        songs_to_collect = parse_netease_playlist()
    if not songs_to_collect:
        print("\n未能获取到任何歌曲，程序退出。")
        return
    bili_cookies = get_bilibili_credential()
    await collect_to_bilibili(songs_to_collect, bili_cookies)

if __name__ == '__main__':
    asyncio.run(main())