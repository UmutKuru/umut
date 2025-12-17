import yt_dlp
import os
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import tkinter as tk
import customtkinter as ctk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox, filedialog
import threading
import json
import time 
import subprocess 
import queue 
import io 
import requests 
from PIL import Image, ImageTk 
import webbrowser 
import re 

# CustomTkinter Global Ayarları (Sade ve Modern Görünüm)
ctk.set_appearance_mode("Dark") 
ctk.set_default_color_theme("blue")

# =======================================================
# 1. AYARLAR VE SABİTLER
# =======================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
MATCH_CACHE_FILE = os.path.join(BASE_DIR, 'match_cache.json')
DOWNLOAD_QUEUE = queue.Queue() 
ID_COUNTER = 0 
TEMP_IMAGE_CACHE = {} 
SEARCH_LIMIT = 10 
MANUAL_WINDOW_THUMBNAIL_CACHE = {} 


# yt-dlp için ortak seçenekler
COMMON_OPTS = {
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'concurrent_fragments': 12,
    'postprocessor_args': ['-threads', '6'],
}

def load_config():
    """Yapılandırma dosyasını yükler."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f: return json.load(f)
        except json.JSONDecodeError: return {}
    return {}

def save_config(config):
    """Yapılandırma dosyasını kaydeder."""
    try:
        with open(CONFIG_FILE, 'w') as f: json.dump(config, f, indent=4) 
    except Exception: pass

def load_match_cache():
    """Kalıcı eşleşme önbelleğini yükler."""
    if os.path.exists(MATCH_CACHE_FILE):
        try:
            with open(MATCH_CACHE_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError): return {}
    return {}

def save_match_cache(cache):
    """Kalıcı eşleşme önbelleğini kaydeder."""
    try:
        with open(MATCH_CACHE_FILE, 'w', encoding='utf-8') as f: json.dump(cache, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Eşleşme önbelleği kaydetme hatası: {e}")

def clean_spotify_query(title, artist):
    """
    V68 Agresif Düzeltme: Spotify parça adını AGRESİF bir şekilde temizler.
    (YouTube'da eşleşme oranını artırmak için yalnızca anahtar kelimeleri bırakır.)
    """
    
    # 1. Parantez ve köşeli parantez içindeki her şeyi kaldır (feat, remix, live, vs. dahil)
    cleaned_title = re.sub(r'\s*\([^)]*\)|\s*\[[^\]]*\]', '', title).strip()
    
    # 2. Yaygın etiket ve ayrıştırıcıları kaldır (Örn: - Single, - Remix)
    cleaned_title = re.sub(r'\s*-\s*(Single|Remix|Edit|Mix|Version|Live|Radio|Album|Video|Official)\b.*', '', cleaned_title, flags=re.IGNORECASE).strip()
    
    # 3. Sorguyu birleştir ve gereksiz boşlukları temizle
    query = f"{cleaned_title} {artist}"
    query = re.sub(r'\s+', ' ', query).strip()
    
    return query

# =======================================================
# 2. HARİCİ SERVİSLER VE İNDİRME İŞLEMLERİ
# =======================================================

def is_tv_compatible(filepath, selected_res):
    """FFprobe kullanarak dosyanın çözünürlük ve codec uyumluluğunu kontrol eder."""
    if not filepath.lower().endswith(('.mp4', '.mkv')):
        return True, "MP3 Formatı Seçildi" 
        
    try:
        command = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height',
            '-of', 'json',
            filepath
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        result = subprocess.run(command, capture_output=True, text=True, check=True, creation_flags=creation_flags)
        
        data = json.loads(result.stdout)
        if not data.get('streams'):
            return False, "Video Akışı Bulunamadı"

        stream = data['streams'][0]
        
        codec = stream.get('codec_name', '').lower()
        height = stream.get('height', 0)
        
        is_h264 = codec == 'h264' or codec == 'avc1'
        is_resolution_ok = height <= 1080 

        if is_h264 and is_resolution_ok:
            return True, f"TV Uyumlu (H.264, {height}p)"
        elif is_h264 and not is_resolution_ok:
            return False, f"Codec OK (H.264) ama Çözünürlük YÜKSEK ({height}p)"
        elif not is_h264 and is_resolution_ok:
             return False, f"Çözünürlük OK ({height}p) ama Codec Uyumsuz ({codec.upper()})"
        else:
            return False, f"Codec Uyumsuz ({codec.upper()}) ve Çözünürlük YÜKSEK ({height}p)"

    except FileNotFoundError:
        return True, "FFprobe Bulunamadı (Kontrol Yapılamadı)"
    except Exception as e:
        return False, f"Kontrol Hatası: {str(e)}"

def spotify_listesini_al(playlist_url, sp_client):
    """Spotify çalma listesindeki şarkı bilgilerini toplar."""
    try:
        playlist_id = playlist_url.split('/')[-1].split('?')[0]
        results = sp_client.playlist_items(playlist_id, fields='items.track(name,artists.name,duration_ms,album.images,album.name,album.release_date)') 
        sarki_listesi = []
        
        while results:
            for item in results['items']:
                track = item.get('track')
                if track and track.get('artists'):
                    title = track.get('name', 'Bilinmeyen Şarkı')
                    artists = track.get('artists', [])
                    artist = artists[0].get('name', 'Bilinmeyen Sanatçı') if artists else 'Bilinmeyen Sanatçı'
                    
                    duration_ms = track.get('duration_ms', 0)
                    duration_sec = duration_ms // 1000
                    
                    images = track.get('album', {}).get('images', [])
                    image_url = images[0].get('url', '') if images else ''
                    
                    # V68: Agresif Arama sorgusunu temizle (Öncelikli arama için)
                    clean_query = clean_spotify_query(title, artist)
                    # V72: Temizlenmemiş, sade sorgu (Yedek arama için)
                    simple_query = f"{title} {artist}"
                    
                    sarki_listesi.append({
                        'query': clean_query, 
                        'simple_query': simple_query, 
                        'title': title,
                        'artist': artist,
                        'duration': f"{duration_sec // 60:02d}:{duration_sec % 60:02d}",
                        'image_url': image_url, 
                        'album': track.get('album', {}).get('name', ''),
                        'release_year': track.get('album', {}).get('release_date', '').split('-')[0],
                        'cache_key': f"{title} - {artist}" 
                    })
            if results.get('next'):
                results = sp_client.next(results)
            else:
                results = None
                
        return sarki_listesi
    except Exception as e: 
        print(f"Spotify Listesi Alma Hatası: {e}")
        return None

def yt_arama(query, search_limit=1):
    """YouTube'da arama yapar ve birden fazla sonuç döndürür."""
    results = []
    try:
        ydl_opts = {
            'format': 'best', 
            'extract_flat': 'in_playlist', 
            'quiet': True, 
            'simulate': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Sadece video sonuçlarını zorla
            info = ydl.extract_info(f"ytsearch{search_limit}:{query}", download=False)
            
            if info and info.get('entries'):
                for entry in info['entries']:
                    if not entry: continue 
                    
                    # V73: Thumbnail alımını basitleştir ve en iyi URL'yi dene
                    thumbnail_url = entry.get('thumbnail', '')
                    if not thumbnail_url and entry.get('thumbnails'):
                         thumbnail_url = entry['thumbnails'][-1].get('url', '')

                    
                    results.append({
                        'title': entry.get('title', 'Başlık Bulunamadı'),
                        'webpage_url': entry.get('webpage_url', 'N/A'), 
                        'duration': entry.get('duration', 0), 
                        'image_url': thumbnail_url,
                        'video_id': entry.get('id', 'N/A')
                    })
        # Geçersiz URL'leri filtrele
        return [res for res in results if res['webpage_url'] != 'N/A' and res['duration'] and res['duration'] > 0]
    except Exception as e: 
        # API anahtarının geçersiz olması veya YouTube'un yanıt vermemesi
        print(f"YouTube Arama Hatası: {e}")
        return results

def get_download_opts(format_secim, download_path, progress_hook, pre_hook, resolution_choice, bitrate_choice, image_url, metadata):
    """yt-dlp indirme seçeneklerini (opts) hazırlar."""
    opts = COMMON_OPTS.copy()
    opts['progress_hooks'] = [progress_hook] 
    opts['pre_hooks'] = [pre_hook] 
    
    if not os.path.exists(download_path): os.makedirs(download_path)
    # V69: Dosya adında başlık adını kullan
    opts['outtmpl'] = os.path.join(download_path, '%(title)s.%(ext)s')
    
    postprocessors = []

    if format_secim == 'b': # MP3 (Ses)
        opts['format'] = 'bestaudio/best'
        opts['extract_audio'] = True; opts['audio_format'] = 'mp3'
        opts['audio_quality'] = bitrate_choice 
        
        postprocessors.append({'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': bitrate_choice})
        
        # Kapak Gömme
        if image_url:
            postprocessors.append({
                'key': 'EmbedThumbnail',
                'already_have_thumbnail': False 
            })
            opts['writethumbnail'] = True 
            opts['thumbnailconvertor'] = 'jpg' 

        # ID3 etiketlerini ekle
        postprocessors.append({ 
            'key': 'FFmpegMetadata',
            'add_metadata': True,
            'metadata_from_field': {
                'artist': metadata.get('artist', ''), 
                'album': metadata.get('album', ''), 
                'title': metadata.get('title', ''),
                'year': metadata.get('year', '')
            }
        })
        
    else: # MP4 (Video)
        if 'En Hızlı' in resolution_choice:
             # mp4 dosya formatını zorla ve en iyi video/sesi birleştir
             video_format = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        elif '1080p' in resolution_choice:
            # H.264 codec zorlaması TV uyumluluğu için önemlidir.
            video_format = 'bestvideo[height<=1080][vcodec=h264][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
        elif '720p' in resolution_choice:
             video_format = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
        else:
             video_format = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
             
        opts['format'] = video_format
        # mp4 formatına dönüştürmeyi garantile
        postprocessors.append({'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'})
        
    opts['ignore_errors'] = True 
    opts['skip_download'] = False 

    opts['postprocessors'] = postprocessors

    return opts

def download_task_wrapper(app, item_id, url_to_download, format_secim, download_path, image_url, metadata):
    """İndirme işlemini ayrı bir iş parçacığında yürütür."""
    app.is_downloading = True
    app.master.after(0, app.update_stop_button_state, True)
    
    item_title = app.download_list_tree.item(item_id, 'values')[1]
    app._safe_log(f"⬇️ Şu An İndiriliyor: {item_title}...", 'info')
    app.master.after(0, lambda: app.update_list_status(item_id, f"⬇️ Başlatılıyor...", tags='info'))
    
    res = app.video_resolution_choice.get() 
    bit = app.audio_bitrate_choice.get() 
    
    downloaded_file_path = None
    
    def pre_hook(d):
         nonlocal downloaded_file_path
         if not app.is_downloading: 
             raise yt_dlp.DownloadError("Kullanıcı tarafından durduruldu.")
         if d['status'] == 'finished' and 'filepath' in d:
             downloaded_file_path = d['filepath']
             
    try:
        opts = get_download_opts(format_secim, download_path, lambda d: app.download_progress_hook(d, item_id), pre_hook, res, bit, image_url, metadata) 
        
        with yt_dlp.YoutubeDL(opts) as ydl: 
            info = ydl.extract_info(url_to_download, download=True)
            
            if 'requested_downloads' in info and info['requested_downloads']:
                downloaded_file_path = info['requested_downloads'][-1].get('filepath')
            elif info and 'title' in info:
                final_filename = ydl.prepare_filename(info)
                if format_secim == 'b':
                     final_filename = os.path.splitext(final_filename)[0] + '.mp3'
                else:
                     final_filename = os.path.splitext(final_filename)[0] + '.mp4'
                downloaded_file_path = final_filename
        
        if app.is_downloading:
            if format_secim == 'v':
                if downloaded_file_path and os.path.exists(downloaded_file_path):
                    is_ok, check_detail = is_tv_compatible(downloaded_file_path, res)
                    
                    if is_ok:
                        app.master.after(0, lambda: app.update_list_status(item_id, f"✅ TAMAMLANDI | {check_detail}", tags='success'))
                        app._safe_log(f"[BAŞARILI] -> {item_title} indirme tamamlandı. TV Uyumluluğu: UYUMLU.", 'success')
                    else:
                        app.master.after(0, lambda: app.update_list_status(item_id, f"⚠️ BİTTİ | {check_detail}", tags='warning'))
                        app._safe_log(f"[UYARI] -> {item_title} indirme tamamlandı. TV Uyumluluğu: UYUMSUZ ({check_detail}).", 'warning')
                else:
                    app.master.after(0, lambda: app.update_list_status(item_id, "TAMAMLANDI (Dosya Yolu Hatası)", tags='warning'))
                    app._safe_log(f"[UYARI] -> {item_title} indirme tamamlandı ancak dosya yolu yakalanamadı.", 'warning')
            else:
                 app.master.after(0, lambda: app.update_list_status(item_id, "✅ TAMAMLANDI (MP3)", tags='success'))
                 app._safe_log(f"[BAŞARILI] -> {item_title} indirme tamamlandı (MP3).", 'success')

            
    except yt_dlp.DownloadError as e:
        error_msg = str(e)
        if "Kullanıcı tarafından durduruldu" in error_msg:
            app._safe_log(f"[DURDURULDU] -> {item_title}", 'warning')
            app.master.after(0, lambda: app.update_list_status(item_id, "DURDURULDU", tags='danger'))
        elif "Video unavailable" in error_msg or "private video" in error_msg or "is not available" in error_msg or "Premieres" in error_msg:
            app._safe_log(f"[HATA] -> {item_title}: Video yayından kaldırılmış/kısıtlanmıştır. Atlanıyor...", 'error')
            app.master.after(0, lambda: app.update_list_status(item_id, "ATLANDI (Kısıtlı)", tags='warning'))
        else:
            app._safe_log(f"[HATA] -> İndirme Hatası: {error_msg}", 'error')
            app.master.after(0, lambda: app.update_list_status(item_id, "HATA", tags='danger'))
    except Exception as e:
        app._safe_log(f"[HATA] -> Beklenmedik Hata: {str(e)}", 'error')
        app.master.after(0, lambda: app.update_list_status(item_id, "HATA", tags='danger'))
    finally:
        app.is_downloading = False
        app.master.after(0, app.update_stop_button_state, False)
        app.master.after(0, app.process_next_in_queue) 

# =======================================================
# 3. ARAYÜZ (GUI) - CUSTOMTKINTER YAPISI
# =======================================================
class DownloaderApp:
    def __init__(self, master): # <-- Hatanın Giderildiği Yer
        self.master = master
        master.title("MelodiaSync | Hızlı İndirme Merkezi (V74 - Tip Hatası Giderildi)")
        master.geometry("1000x750") 
        
        self.log_text_bg = '#2A2D2E' 
        self.log_text_fg = '#FFFFFF'
        self.is_downloading = False 
        self.item_data_map = {} 
        self.manual_selection_lock = threading.Lock() 
        
        self.config = load_config()
        self.match_cache = load_match_cache() 
        self.download_queue = [] 
        
        self.url_input = tk.StringVar()
        self.download_dir = tk.StringVar(value=self.config.get('last_dir', os.path.join(os.path.expanduser('~'), 'Desktop', 'melodia_downloads')))
        self.kaynak_secim = tk.StringVar(value='y')
        self.format_secim = tk.StringVar(value='v')
        
        self.video_resolution_choice = tk.StringVar(value=self.config.get('video_res', 'TV Uyumlu (1080p)')) 
        self.audio_bitrate_choice = tk.StringVar(value=self.config.get('audio_bit', '320'))
        
        self.api_id = tk.StringVar(value=self.config.get('client_id', ''))
        self.api_secret = tk.StringVar(value=self.config.get('client_secret', ''))
        
        self.sp_client = self.get_spotify_api_client() 

        self.style = ttk.Style(theme="cyborg") 
        
        self.create_widgets()
        self.update_ui_options()
        
    def get_spotify_api_client(self):
        client_id = self.api_id.get()
        client_secret = self.api_secret.get()
        
        if client_id and client_secret:
            try:
                return spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret))
            except Exception:
                return None
        return None

    def create_widgets(self):
        main_frame = ctk.CTkFrame(self.master, fg_color="transparent")
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        self.setup_header_frame(main_frame)
        self.setup_main_controls(main_frame)
        self.setup_list_and_image_area(main_frame)
        self.setup_log_area(main_frame)

    def setup_header_frame(self, parent):
        header_frame = ctk.CTkFrame(parent, fg_color="transparent")
        header_frame.pack(fill='x', pady=(0, 10))

        ctk.CTkLabel(header_frame, text="MelodiaSync | Hızlı İndirme Merkezi (V74)", font=ctk.CTkFont(size=20, weight="bold")).pack(side='left', anchor='w')
        
        self.btn_settings = ctk.CTkButton(header_frame, text="⚙️ API & Kalite Ayarları", command=self.show_settings_popup, width=150)
        self.btn_settings.pack(side='right', anchor='e')
        
    def show_settings_popup(self):
        if hasattr(self, 'settings_popup') and self.settings_popup.winfo_exists():
            self.settings_popup.focus()
            return
            
        self.settings_popup = ctk.CTkToplevel(self.master)
        self.settings_popup.title("API & Kalite Ayarları")
        self.settings_popup.geometry("450x350")
        self.settings_popup.grab_set() 
        self.settings_popup.focus()

        api_frame = ctk.CTkFrame(self.settings_popup)
        ctk.CTkLabel(api_frame, text="Spotify API (Halka Açık Listeler)", font=ctk.CTkFont(weight="bold")).pack(anchor='w', pady=(5, 5), padx=10)
        api_frame.pack(fill='x', padx=10, pady=(10, 5))

        ctk.CTkLabel(api_frame, text="Client ID:").pack(anchor='w', pady=(0, 0), padx=10)
        ctk.CTkEntry(api_frame, textvariable=self.api_id).pack(fill='x', pady=2, padx=10)

        ctk.CTkLabel(api_frame, text="Client Secret:").pack(anchor='w', pady=(5, 0), padx=10)
        ctk.CTkEntry(api_frame, textvariable=self.api_secret, show="*").pack(fill='x', pady=2, padx=10)

        ctk.CTkButton(api_frame, text="💾 Kaydet ve API Bağlantısını Güncelle", command=self.save_api).pack(anchor='e', pady=(10, 5), padx=10)

        quality_frame = ctk.CTkFrame(self.settings_popup)
        ctk.CTkLabel(quality_frame, text="Kalite Seçimleri", font=ctk.CTkFont(weight="bold")).pack(anchor='w', pady=(5, 5), padx=10)
        quality_frame.pack(fill='x', padx=10, pady=5)
        
        v_frame = ctk.CTkFrame(quality_frame, fg_color="transparent")
        ctk.CTkLabel(v_frame, text="Video Kalitesi:").pack(side='left', padx=(10, 5))
        self.combo_res = ctk.CTkComboBox(v_frame, values=['PC/Orijinal Kalite (En Hızlı)', 'TV Uyumlu (1080p)', 'Mobil (720p)'], state='readonly', width=200, variable=self.video_resolution_choice)
        self.combo_res.pack(side='left')
        v_frame.pack(fill='x', pady=5)

        a_frame = ctk.CTkFrame(quality_frame, fg_color="transparent")
        ctk.CTkLabel(a_frame, text="MP3 Kalitesi:").pack(side='left', padx=(10, 5))
        self.combo_bit = ctk.CTkComboBox(a_frame, values=['320', '256', '192', '128'], state='readonly', width=200, variable=self.audio_bitrate_choice)
        self.combo_bit.pack(side='left')
        a_frame.pack(fill='x', pady=5)


    def save_api(self):
        self.config['client_id'] = self.api_id.get()
        self.config['client_secret'] = self.api_secret.get()
        self.config['video_res'] = self.video_resolution_choice.get()
        self.config['audio_bit'] = self.audio_bitrate_choice.get()
        save_config(self.config)
        
        self.sp_client = self.get_spotify_api_client()
        
        if self.sp_client:
            self._safe_log("✅ Spotify API bağlantısı güncellendi (Halka Açık Listeler için aktif).", 'success')
        else:
            self._safe_log("❌ Spotify API bağlantısı kurulamadı. ID/Secret kontrol edin.", 'error')
        
        if hasattr(self, 'settings_popup'):
             self.settings_popup.destroy()


    def setup_main_controls(self, parent):
        url_frame = ctk.CTkFrame(parent, fg_color="transparent") 
        url_frame.pack(fill='x', pady=(0, 10))
        
        dir_frame = ctk.CTkFrame(url_frame, fg_color="transparent")
        dir_frame.pack(side='right', padx=(10, 0))
        ctk.CTkEntry(dir_frame, textvariable=self.download_dir, width=250).pack(side='left', fill='x', padx=(0, 5))
        ctk.CTkButton(dir_frame, text="📂 Klasör Seç", command=self.select_dir, width=100).pack(side='left')
        
        entry = ctk.CTkEntry(url_frame, textvariable=self.url_input, font=('Segoe UI', 12), placeholder_text="Spotify Linki, YouTube Linki veya Arama Sorgusu Girin")
        entry.pack(fill='x', expand=True, pady=5, side='left')
        entry.bind('<Return>', lambda event: self.start_process())

        controls_frame = ctk.CTkFrame(parent, fg_color="transparent")
        controls_frame.pack(fill='x', pady=5)
        
        format_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        format_frame.pack(side='left', padx=(0, 20))
        ctk.CTkLabel(format_frame, text="Format:", text_color="#ADAEAE").pack(side='left', padx=(0, 5))
        
        ctk.CTkRadioButton(format_frame, text="MP4", variable=self.format_secim, value='v', command=self.update_ui_options, text_color="#FFFFFF").pack(side='left', padx=5)
        ctk.CTkRadioButton(format_frame, text="MP3", variable=self.format_secim, value='b', command=self.update_ui_options, text_color="#FFFFFF").pack(side='left', padx=10)

        source_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        source_frame.pack(side='left', padx=20)
        ctk.CTkLabel(source_frame, text="Kaynak Tipi:", text_color="#ADAEAE").pack(side='left', padx=(0, 5))
        
        ctk.CTkRadioButton(source_frame, text="YouTube/Arama", variable=self.kaynak_secim, value='y', text_color="#FFFFFF").pack(side='left', padx=5)
        ctk.CTkRadioButton(source_frame, text="Spotify Linki", variable=self.kaynak_secim, value='s', text_color="#FFFFFF").pack(side='left', padx=10)

        action_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        action_frame.pack(side='right', fill='x', expand=True)

        self.btn_start = ctk.CTkButton(action_frame, text="⬇️ KUYRUK BAŞLAT / EKLE", fg_color="#2CC65E", hover_color="#24A34F", command=self.start_process, cursor="hand2")
        self.btn_start.pack(side='left', expand=True, fill='x', ipady=5, padx=(0, 5))

        self.btn_stop = ctk.CTkButton(action_frame, text="🛑 DURDUR", fg_color="#FF3A3A", hover_color="#D13030", command=self.stop_download, state='disabled', cursor="hand2")
        self.btn_stop.pack(side='left', fill='x', ipady=5, padx=5)
        
    def setup_list_and_image_area(self, parent):
        list_image_group = ctk.CTkFrame(parent, fg_color="transparent")
        list_image_group.pack(fill='both', expand=True, pady=(10, 0))

        image_frame = ctk.CTkFrame(list_image_group, width=250) 
        image_frame.pack(side='left', fill='y', padx=(0, 10))
        image_frame.pack_propagate(False) 

        ctk.CTkLabel(image_frame, text="Albüm/Video Kapağı (Teaser)", text_color="#ADAEAE").pack(pady=5)
        
        self.album_art_label = ctk.CTkLabel(image_frame, text="Görsel Yok", fg_color="#2A2D2E", text_color="#ADAEAE")
        self.album_art_label.pack(fill='both', expand=True, padx=5, pady=5)
        
        preview_frame = ctk.CTkFrame(image_frame, fg_color="transparent")
        preview_frame.pack(fill='x', padx=5, pady=5)
        
        self.btn_preview = ctk.CTkButton(preview_frame, text="▶️ Önizle", fg_color="#FFD700", hover_color="#CCAC00", command=self.preview_selected_item, cursor="hand2")
        self.btn_preview.pack(fill='x', pady=(0, 5))
        
        self.btn_remove = ctk.CTkButton(preview_frame, text="🗑️ Seçileni Kaldır", fg_color="#3B8ED4", hover_color="#3073B3", command=self.remove_selected_item, cursor="hand2")
        self.btn_remove.pack(fill='x')


        tree_frame = ttk.Frame(list_image_group) 
        tree_frame.pack(side='left', fill='both', expand=True)
        
        columns = ("#", "Şarkı/Video Adı", "Kaynak Sanatçı", "Albüm/Detay", "Süre", "Durum")
        self.download_list_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', bootstyle="cyborg") 
        
        self.download_list_tree.heading("#", text="#", anchor=CENTER)
        self.download_list_tree.heading("Şarkı/Video Adı", text="Şarkı/Video Adı", anchor=W)
        self.download_list_tree.heading("Kaynak Sanatçı", text="Kaynak Sanatçı", anchor=W)
        self.download_list_tree.heading("Albüm/Detay", text="Albüm/Detay", anchor=W)
        self.download_list_tree.heading("Süre", text="Süre", anchor=CENTER)
        self.download_list_tree.heading("Durum", text="Durum", anchor=CENTER)
        
        self.download_list_tree.column("#", width=30, anchor=CENTER, stretch=NO)
        self.download_list_tree.column("Şarkı/Video Adı", width=250, anchor=W)
        self.download_list_tree.column("Kaynak Sanatçı", width=120, anchor=W)
        self.download_list_tree.column("Albüm/Detay", width=120, anchor=W)
        self.download_list_tree.column("Süre", width=80, anchor=CENTER, stretch=NO)
        self.download_list_tree.column("Durum", width=150, anchor=CENTER)

        vsb = tk.Scrollbar(tree_frame, orient="vertical", command=self.download_list_tree.yview)
        vsb.pack(side='right', fill='y')
        self.download_list_tree.configure(yscrollcommand=vsb.set)
        
        self.download_list_tree.pack(fill='both', expand=True)
        self.download_list_tree.bind("<<TreeviewSelect>>", self.show_album_art)
        
    def setup_log_area(self, parent):
        log_frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(log_frame, text="İşlem Kayıtları", text_color="#ADAEAE").pack(anchor='w')
        log_frame.pack(fill='x', pady=(10, 0))

        self.log_text = tk.Text(log_frame, height=5, state='disabled', bg=self.log_text_bg, fg=self.log_text_fg, font=('Consolas', 9), relief='flat')
        self.log_text.pack(fill='x', expand=True)
        
        self.download_list_tree.tag_configure('success', foreground='#2CC65E') 
        self.download_list_tree.tag_configure('info', foreground='#3B8ED4') 
        self.download_list_tree.tag_configure('warning', foreground='#FFA400') 
        self.download_list_tree.tag_configure('danger', foreground='#FF3A3A') 
        
    # =======================================================
    # 4. YARDIMCI METOTLAR
    # =======================================================
    
    def _safe_log(self, msg, mtype='info', clear=False):
        """V69: Thread'den bağımsız güvenli loglama."""
        try:
            self.master.after(0, lambda: self.log_message(msg, mtype, clear))
        except Exception as e:
            if "main thread is not in main loop" in str(e):
                print(f"TKINTER LOGGING FAILED: {msg}")
            else:
                 print(f"Unexpected logging error: {e}")
    
    def get_image_from_url(self, url, size=(240, 240)):
        """URL'den görseli indirir ve PIL/Tkinter PhotoImage nesnesi döndürür."""
        try:
            response = requests.get(url, stream=True, timeout=5)
            response.raise_for_status()
            image_data = io.BytesIO(response.content)
            img = Image.open(image_data)
            img.thumbnail(size, Image.Resampling.LANCZOS) 
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def show_album_art(self, event):
        selected_item = self.download_list_tree.focus()
        if not selected_item: return

        item_data = self.item_data_map.get(selected_item)
        
        if not item_data or not item_data.get('image_url'):
            self.album_art_label.configure(image=None, text="Görsel Yok / Bulunamadı")
            return

        threading.Thread(target=self._load_image_async, args=(selected_item, item_data['image_url'])).start()

    def _load_image_async(self, item_id, url):
        new_photo = self.get_image_from_url(url)
        
        def update_ui():
            if item_id == self.download_list_tree.focus(): 
                if new_photo:
                    self.album_art_label.configure(image=new_photo, text="")
                    TEMP_IMAGE_CACHE[item_id] = new_photo 
                else:
                    self.album_art_label.configure(image=None, text="Görsel Yüklenemedi")

        self.master.after(0, update_ui)

    def preview_selected_item(self):
        selected_item = self.download_list_tree.focus()
        if not selected_item:
            self._safe_log("Önizlemek için listeden bir öğe seçin.", 'warning'); return

        item_data = self.item_data_map.get(selected_item)
        if not item_data or not item_data.get('url'):
            self._safe_log("Seçilen öğe için geçerli bir URL bulunamadı.", 'error'); return

        url = item_data['url']
        threading.Thread(target=lambda: webbrowser.open(url)).start()
        self._safe_log(f"Önizleme başlatıldı: {item_data['title']} tarayıcıda açılıyor.", 'info')

    def remove_selected_item(self):
        selected_items = self.download_list_tree.selection()
        if not selected_items:
            self._safe_log("Kaldırmak için listeden bir veya birden fazla öğe seçin.", 'warning')
            return

        for item_id in selected_items:
            if self.download_list_tree.item(item_id, 'values')[5] == "KUYRUKTA":
                new_queue = queue.Queue()
                removed = False
                while not DOWNLOAD_QUEUE.empty():
                    try:
                        item = DOWNLOAD_QUEUE.get_nowait()
                        if item[0] != item_id:
                            new_queue.put(item)
                        else:
                            removed = True
                    except queue.Empty:
                        break 
                
                while not new_queue.empty():
                    DOWNLOAD_QUEUE.put(new_queue.get_nowait())
                
                if removed:
                    self._safe_log(f"'{self.download_list_tree.item(item_id, 'values')[1]}' kuyruktan kaldırıldı.", 'info')

            self.download_list_tree.delete(item_id)
            if item_id in self.item_data_map:
                del self.item_data_map[item_id]
                
        self.renumber_list_items()
        
    def renumber_list_items(self):
        global ID_COUNTER
        ID_COUNTER = 0
        children = self.download_list_tree.get_children()
        
        for i, item_id in enumerate(children):
            ID_COUNTER = i + 1
            current_values = list(self.download_list_tree.item(item_id, 'values'))
            current_values[0] = ID_COUNTER
            self.download_list_tree.item(item_id, values=current_values)

    def update_list_status(self, item_id, status_text, tags=None):
        current_values = list(self.download_list_tree.item(item_id, 'values'))
        current_values[5] = status_text 
        
        if tags:
            self.download_list_tree.item(item_id, values=current_values, tags=(tags,))
        else:
            self.download_list_tree.item(item_id, values=current_values, tags=())

    def update_ui_options(self):
        pass 

    def update_stop_button_state(self, is_active):
        self.btn_stop.configure(state='normal' if is_active else 'disabled')
        self.btn_start.configure(state='disabled' if is_active else 'normal')
        
    def stop_download(self):
        if self.is_downloading:
            self.is_downloading = False 
            self._safe_log("Aktif indirme için durdurma komutu gönderildi. İşlemin bitmesi bekleniyor...", 'warning')
            
        while not DOWNLOAD_QUEUE.empty():
            try:
                item_id, _, _, _, _, _ = DOWNLOAD_QUEUE.get_nowait()
                self.master.after(0, lambda id=item_id: self.update_list_status(id, "İptal Edildi", tags='danger'))
            except queue.Empty:
                break
        
        self._safe_log("Kuyruktaki bekleme işlemleri iptal edildi.", 'info')

    def open_download_folder(self):
        folder_path = self.download_dir.get()
        if not os.path.exists(folder_path): os.makedirs(folder_path)
        try:
            if os.name == 'nt': 
                subprocess.Popen(['explorer', folder_path])
            else: 
                subprocess.Popen(['xdg-open' if os.name == 'posix' else 'open', folder_path])
        except Exception as e:
                 self._safe_log(f"Klasör açma hatası: {e}", 'error')

    def select_dir(self):
        d = filedialog.askdirectory()
        if d: 
            self.download_dir.set(d)
            self.config['last_dir'] = d 
            save_config(self.config)

    def log_message(self, msg, mtype='info', clear=False):
        self.log_text.config(state='normal')
        if clear: self.log_text.delete('1.0', 'end')
        
        if not hasattr(self.log_text, 'tag_config_done'):
            self.log_text.tag_config('info', foreground='#3B8ED4')
            self.log_text.tag_config('success', foreground='#2CC65E')
            self.log_text.tag_config('warning', foreground='#FFA400')
            self.log_text.tag_config('error', foreground='#FF3A3A')
            setattr(self.log_text, 'tag_config_done', True)
        
        self.log_text.insert('end', f"{msg}\n", mtype)
        
        self.log_text.see('end')
        self.log_text.config(state='disabled')

    def download_progress_hook(self, d, item_id):
        
        if d['status'] == 'downloading':
            
            p = d.get('_percent_str', None) 
            s = d.get('_eta_str', None)      
            
            if p is None and (d.get('total_bytes') is None and d.get('total_bytes_estimate') is None):
                status_text = "⬇️ İndiriliyor... (İlerleme Bekleniyor)"
            else:
                percent_str = p.strip() if p else 'N/A'
                eta_str = s.strip() if s else 'N/A'
                status_text = f"⬇️ %{percent_str} (Kalan: {eta_str})"
            
            self.master.after(0, lambda: self.update_list_status(item_id, status_text))
            
        elif d['status'] == 'postprocessing':
            
            description = d.get('info_dict', {}).get('postprocessor_data', {}).get('postprocessor', 'İşleniyor')
            description = description.split(':')[0] 
            
            if 'EmbedThumbnail' in description:
                 detail = "Kapak Gömülüyor"
            elif 'Metadata' in description:
                 detail = "Etiketler Ekleniyor"
            elif 'ExtractAudio' in description or 'Convertor' in description:
                 detail = "Dönüştürülüyor"
            else:
                 detail = "İşleniyor"
            
            status_text = f"🔄 {detail} (Son Aşama)"
            
            self.master.after(0, lambda: self.update_list_status(item_id, status_text))
            
        elif d['status'] == 'finished':
             self.master.after(0, lambda: self.update_list_status(item_id, "Bitti (Kontrol Ediliyor)", tags='info'))
            
    def process_next_in_queue(self):
        if not DOWNLOAD_QUEUE.empty() and not self.is_downloading:
            try:
                item_id, url, format_t, path, image_url, metadata = DOWNLOAD_QUEUE.get_nowait() 
                threading.Thread(target=download_task_wrapper, args=(self, item_id, url, format_t, path, image_url, metadata)).start()
            except queue.Empty:
                 pass
        elif DOWNLOAD_QUEUE.empty() and not self.is_downloading:
            self._safe_log("Tüm indirmeler tamamlandı. Yeni komut bekliyor...", 'success')
            
    def add_item_to_list_and_queue(self, index, title, artist, detail, duration, url, format_t, path, image_url='', metadata=None):
        global ID_COUNTER
        ID_COUNTER += 1
        
        item_id = self.download_list_tree.insert("", "end", values=(ID_COUNTER, title, artist, detail, duration, "KUYRUKTA"), tags=('info',))
        
        if metadata is None: metadata = {}
        DOWNLOAD_QUEUE.put((item_id, url, format_t, path, image_url, metadata))
        
        self.item_data_map[item_id] = {
            'title': title,
            'url': url,
            'image_url': image_url
        }
        
        return item_id

    def start_process(self):
        """Ana işlemi (arama/indirme) başlatır."""
        url = self.url_input.get().strip()
        if not url: self._safe_log("Lütfen bir bağlantı veya arama sorgusu girin.", 'error', clear=True); return
        
        if self.kaynak_secim.get() == 's' and (not self.sp_client):
            self._safe_log("Spotify seçildi, ancak API bağlantısı yok. Ayarlar (Client ID/Secret) gereklidir.", 'error', clear=True)
            return

        threading.Thread(target=self._run, args=(url,)).start()

    def _run(self, url):
        """Kaynak tipine göre Spotify veya YouTube işlemini başlatır (Ayrı Thread'de çalışır)."""
        kaynak = self.kaynak_secim.get()
        format_t = self.format_secim.get()
        download_path = self.download_dir.get()
        
        self.master.after(0, self.download_list_tree.delete, *self.download_list_tree.get_children())
        self.item_data_map.clear()
        TEMP_IMAGE_CACHE.clear()
        
        self._safe_log("Yeni kaynak taranıyor...", 'info', clear=True)

        if kaynak == 's':
            # --- SPOTIFY LİSTESİ İŞLEME (V72: Güçlü Arama ve Engelleme) ---
            
            items = spotify_listesini_al(url, self.sp_client)
            
            if not items: 
                self._safe_log("Spotify listesi alınamadı (URL hatalı veya liste ÖZEL/Private olabilir).", 'error'); 
                return
            
            self._safe_log(f"Bulunan şarkı sayısı: {len(items)}. İşleniyor...", 'info')
            
            for i, item in enumerate(items):
                
                # 1. Önbellek Kontrolü
                cache_key = item['cache_key']
                if cache_key in self.match_cache:
                    cached_url = self.match_cache[cache_key]['url']
                    self.master.after(0, lambda u=cached_url, itm=item: self.add_item_to_list_and_queue(
                            index=itm['title'], title=itm['title'], artist=itm['artist'],
                            detail=f"{itm.get('album', 'Spotify Listesi')} (Önbellek)", duration="N/A", url=u,
                            format_t=self.format_secim.get(), path=self.download_dir.get(), image_url=itm.get('image_url'), metadata=item))
                    self._safe_log(f"⚡ Önbellek Eşleşmesi: '{item['title']}' bulundu ve kuyruğa eklendi.", 'success')
                    time.sleep(0.1)
                    continue 
                
                # 2. Agresif Temizlenmiş Sorgu (V68)
                search_results = yt_arama(item['query'], search_limit=SEARCH_LIMIT) 
                
                # V72: HİÇBİR SONUÇ DÖNMEZSE (0 sonuç), basit sorguyu dene
                if not search_results:
                    self._safe_log(f"⚠️ İlk sorgu ('{item['query']}') 0 sonuç döndürdü. Daha basit sorgu deneniyor...", 'warning')
                    search_results = yt_arama(item['simple_query'], search_limit=SEARCH_LIMIT)
                    
                    if not search_results:
                        self._safe_log(f"❌ Basit sorgu da sonuç döndüremedi. Manuel seçime yönlendiriliyor...", 'error')
                
                
                if search_results and (len(search_results) > 1 or 'youtube.com' in url or 'youtu.be' in url):
                     pass
                elif search_results:
                    # Sadece 1 sonuç varsa, otomatik seçebiliriz (Spotify'dan gelmiyorsa)
                    selected_result = search_results[0]
                    try:
                        self.master.after(0, lambda sr=selected_result, itm=item: self.add_item_to_list_and_queue(
                            index=itm['title'], title=sr['title'], artist=itm['artist'],
                            detail=itm.get('album', 'Spotify Listesi'), duration=self._format_duration(sr['duration']),
                            url=sr['webpage_url'], format_t=self.format_secim.get(), path=self.download_dir.get(),
                            image_url=itm.get('image_url'), metadata=itm
                        ))
                        self._safe_log(f"✅ Otomatik Seçim: '{item['title']}' için tek ve en iyi sonuç bulundu ve kuyruğa eklendi.", 'success')
                    except RuntimeError as e:
                        if "main thread is not in main loop" in str(e):
                            self._safe_log(f"CRITICAL: Arayüz Hatası (Öğe Ekleme Başarısız)", 'error')
                        else:
                            raise e
                    time.sleep(0.1)
                    continue

def _parse_iso8601_duration_to_seconds(iso):
    # ISO 8601 duration like PT3M12S
    if not iso or not isinstance(iso, str):
        return 0
    m = re.match(r'^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', iso)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s

def _get_youtube_api_key():
    # Prefer env var; fallback to config.json field "youtube_api_key"
    key = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("youtube_api_key")
    if key:
        return key.strip()
    cfg = load_config()
    key = cfg.get("youtube_api_key") if isinstance(cfg, dict) else None
    return (key or "").strip()

def _yt_api_search(query, search_limit=5):
    """Resmi YouTube Data API v3 ile arar. (Daha stabil)"""
    api_key = _get_youtube_api_key()
    if not api_key:
        return []

    # 1) Search
    search_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": int(search_limit),
        "key": api_key,
        "safeSearch": "none",
    }
    r = requests.get(search_url, params=params, timeout=15)
    if r.status_code != 200:
        return []
    data = r.json() or {}
    items = data.get("items") or []
    video_ids = [it.get("id", {}).get("videoId") for it in items]
    video_ids = [v for v in video_ids if v]

    # 2) Fetch durations (cheap)
    durations = {}
    if video_ids:
        vids_url = "https://www.googleapis.com/youtube/v3/videos"
        params2 = {
            "part": "contentDetails",
            "id": ",".join(video_ids),
            "key": api_key,
        }
        r2 = requests.get(vids_url, params=params2, timeout=15)
        if r2.status_code == 200:
            data2 = r2.json() or {}
            for v in (data2.get("items") or []):
                vid = v.get("id")
                dur = (v.get("contentDetails") or {}).get("duration")
                durations[vid] = _parse_iso8601_duration_to_seconds(dur)

    results = []
    for it in items:
        vid = it.get("id", {}).get("videoId")
        sn = it.get("snippet") or {}
        title = sn.get("title") or "Başlık Yok"
        thumbs = (sn.get("thumbnails") or {})
        thumb_url = ""
        for k in ("high", "medium", "default"):
            if k in thumbs and thumbs[k].get("url"):
                thumb_url = thumbs[k]["url"]
                break
        results.append({
            "title": title,
            "webpage_url": f"https://www.youtube.com/watch?v={vid}" if vid else "N/A",
            "duration": durations.get(vid, 0),
            "image_url": thumb_url,
            "video_id": vid or "",
        })
    return [x for x in results if x.get("webpage_url") not in (None, "", "N/A")]

def yt_arama(query, search_limit=1):
    """YouTube'da arama yapar ve birden fazla sonuç döndürür.
    Önce YouTube Data API ile dener, olmazsa yt-dlp ile fallback yapar.
    """
    # 1) API yolu
    api_results = _yt_api_search(query, search_limit=search_limit)
    if api_results:
        return api_results

    # 2) yt-dlp fallback
    results = []
    try:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": True,
            **COMMON_OPTS,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{search_limit}:{query}", download=False) or {}
        for entry in (info.get("entries") or []):
            if not entry:
                continue
            vid = entry.get("id") or ""
            url = entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "N/A")
            results.append({
                "title": entry.get("title") or "Başlık Yok",
                "webpage_url": url,
                "duration": entry.get("duration") or 0,
                "image_url": entry.get("thumbnail") or "",
                "video_id": vid,
            })
        return [x for x in results if x.get("webpage_url") not in (None, "", "N/A")]
    except Exception:
        return results

def load_config():
    """Yapılandırma dosyasını yükler."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f: return json.load(f)
        except json.JSONDecodeError: return {}
    return {}

def save_config(config):
    """Yapılandırma dosyasını kaydeder."""
    try:
        with open(CONFIG_FILE, 'w') as f: json.dump(config, f, indent=4) 
    except Exception: pass

def load_match_cache():
    """Kalıcı eşleşme önbelleğini yükler."""
    if os.path.exists(MATCH_CACHE_FILE):
        try:
            with open(MATCH_CACHE_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError): return {}
    return {}

def save_match_cache(cache):
    """Kalıcı eşleşme önbelleğini kaydeder."""
    try:
        with open(MATCH_CACHE_FILE, 'w', encoding='utf-8') as f: json.dump(cache, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Eşleşme önbelleği kaydetme hatası: {e}")

def clean_spotify_query(title, artist):
    """
    V68 Agresif Düzeltme: Spotify parça adını AGRESİF bir şekilde temizler.
    (YouTube'da eşleşme oranını artırmak için yalnızca anahtar kelimeleri bırakır.)
    """
    
    # 1. Parantez ve köşeli parantez içindeki her şeyi kaldır (feat, remix, live, vs. dahil)
    cleaned_title = re.sub(r'\s*\([^)]*\)|\s*\[[^\]]*\]', '', title).strip()
    
    # 2. Yaygın etiket ve ayrıştırıcıları kaldır (Örn: - Single, - Remix)
    cleaned_title = re.sub(r'\s*-\s*(Single|Remix|Edit|Mix|Version|Live|Radio|Album|Video|Official)\b.*', '', cleaned_title, flags=re.IGNORECASE).strip()
    
    # 3. Sorguyu birleştir ve gereksiz boşlukları temizle
    query = f"{cleaned_title} {artist}"
    query = re.sub(r'\s+', ' ', query).strip()
    
    return query

# =======================================================
# 2. HARİCİ SERVİSLER VE İNDİRME İŞLEMLERİ
# =======================================================

def is_tv_compatible(filepath, selected_res):
    """FFprobe kullanarak dosyanın çözünürlük ve codec uyumluluğunu kontrol eder."""
    if not filepath.lower().endswith(('.mp4', '.mkv')):
        return True, "MP3 Formatı Seçildi" 
        
    try:
        command = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height',
            '-of', 'json',
            filepath
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        result = subprocess.run(command, capture_output=True, text=True, check=True, creation_flags=creation_flags)
        
        data = json.loads(result.stdout)
        if not data.get('streams'):
            return False, "Video Akışı Bulunamadı"

        stream = data['streams'][0]
        
        codec = stream.get('codec_name', '').lower()
        height = stream.get('height', 0)
        
        is_h264 = codec == 'h264' or codec == 'avc1'
        is_resolution_ok = height <= 1080 

        if is_h264 and is_resolution_ok:
            return True, f"TV Uyumlu (H.264, {height}p)"
        elif is_h264 and not is_resolution_ok:
            return False, f"Codec OK (H.264) ama Çözünürlük YÜKSEK ({height}p)"
        elif not is_h264 and is_resolution_ok:
             return False, f"Çözünürlük OK ({height}p) ama Codec Uyumsuz ({codec.upper()})"
        else:
            return False, f"Codec Uyumsuz ({codec.upper()}) ve Çözünürlük YÜKSEK ({height}p)"

    except FileNotFoundError:
        return True, "FFprobe Bulunamadı (Kontrol Yapılamadı)"
    except Exception as e:
        return False, f"Kontrol Hatası: {str(e)}"

def spotify_listesini_al(playlist_url, sp_client):
    """Spotify çalma listesindeki şarkı bilgilerini toplar."""
    try:
        playlist_id = playlist_url.split('/')[-1].split('?')[0]
        results = sp_client.playlist_items(playlist_id, fields='items.track(name,artists.name,duration_ms,album.images,album.name,album.release_date)') 
        sarki_listesi = []
        
        while results:
            for item in results['items']:
                track = item.get('track')
                if track and track.get('artists'):
                    title = track.get('name', 'Bilinmeyen Şarkı')
                    artists = track.get('artists', [])
                    artist = artists[0].get('name', 'Bilinmeyen Sanatçı') if artists else 'Bilinmeyen Sanatçı'
                    
                    duration_ms = track.get('duration_ms', 0)
                    duration_sec = duration_ms // 1000
                    
                    images = track.get('album', {}).get('images', [])
                    image_url = images[0].get('url', '') if images else ''
                    
                    # V68: Agresif Arama sorgusunu temizle (Öncelikli arama için)
                    clean_query = clean_spotify_query(title, artist)
                    # V72: Temizlenmemiş, sade sorgu (Yedek arama için)
                    simple_query = f"{title} {artist}"
                    
                    sarki_listesi.append({
                        'query': clean_query, 
                        'simple_query': simple_query, 
                        'title': title,
                        'artist': artist,
                        'duration': f"{duration_sec // 60:02d}:{duration_sec % 60:02d}",
                        'image_url': image_url, 
                        'album': track.get('album', {}).get('name', ''),
                        'release_year': track.get('album', {}).get('release_date', '').split('-')[0],
                        'cache_key': f"{title} - {artist}" 
                    })
            if results.get('next'):
                results = sp_client.next(results)
            else:
                results = None
                
        return sarki_listesi
    except Exception as e: 
        print(f"Spotify Listesi Alma Hatası: {e}")
        return None

def yt_arama(query, search_limit=1):
    """YouTube'da arama yapar ve birden fazla sonuç döndürür."""
    results = []
    try:
        ydl_opts = {
            'format': 'best', 
            'extract_flat': 'in_playlist', 
            'quiet': True, 
            'simulate': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Sadece video sonuçlarını zorla
            info = ydl.extract_info(f"ytsearch{search_limit}:{query}", download=False)
            
            if info and info.get('entries'):
                for entry in info['entries']:
                    if not entry: continue 
                    
                    # V73: Thumbnail alımını basitleştir ve en iyi URL'yi dene
                    thumbnail_url = entry.get('thumbnail', '')
                    if not thumbnail_url and entry.get('thumbnails'):
                         thumbnail_url = entry['thumbnails'][-1].get('url', '')

                    
                    results.append({
                        'title': entry.get('title', 'Başlık Bulunamadı'),
                        'webpage_url': entry.get('webpage_url', 'N/A'), 
                        'duration': entry.get('duration', 0), 
                        'image_url': thumbnail_url,
                        'video_id': entry.get('id', 'N/A')
                    })
        # Geçersiz URL'leri filtrele
        return [res for res in results if res['webpage_url'] != 'N/A' and res['duration'] and res['duration'] > 0]
    except Exception as e: 
        # API anahtarının geçersiz olması veya YouTube'un yanıt vermemesi
        print(f"YouTube Arama Hatası: {e}")
        return results

def get_download_opts(format_secim, download_path, progress_hook, pre_hook, resolution_choice, bitrate_choice, image_url, metadata):
    """yt-dlp indirme seçeneklerini (opts) hazırlar."""
    opts = COMMON_OPTS.copy()
    opts['progress_hooks'] = [progress_hook] 
    opts['pre_hooks'] = [pre_hook] 
    
    if not os.path.exists(download_path): os.makedirs(download_path)
    # V69: Dosya adında başlık adını kullan
    opts['outtmpl'] = os.path.join(download_path, '%(title)s.%(ext)s')
    
    postprocessors = []

    if format_secim == 'b': # MP3 (Ses)
        opts['format'] = 'bestaudio/best'
        opts['extract_audio'] = True; opts['audio_format'] = 'mp3'
        opts['audio_quality'] = bitrate_choice 
        
        postprocessors.append({'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': bitrate_choice})
        
        # Kapak Gömme
        if image_url:
            postprocessors.append({
                'key': 'EmbedThumbnail',
                'already_have_thumbnail': False 
            })
            opts['writethumbnail'] = True 
            opts['thumbnailconvertor'] = 'jpg' 

        # ID3 etiketlerini ekle
        postprocessors.append({ 
            'key': 'FFmpegMetadata',
            'add_metadata': True,
            'metadata_from_field': {
                'artist': metadata.get('artist', ''), 
                'album': metadata.get('album', ''), 
                'title': metadata.get('title', ''),
                'year': metadata.get('year', '')
            }
        })
        
    else: # MP4 (Video)
        if 'En Hızlı' in resolution_choice:
             # mp4 dosya formatını zorla ve en iyi video/sesi birleştir
             video_format = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        elif '1080p' in resolution_choice:
            # H.264 codec zorlaması TV uyumluluğu için önemlidir.
            video_format = 'bestvideo[height<=1080][vcodec=h264][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
        elif '720p' in resolution_choice:
             video_format = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
        else:
             video_format = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
             
        opts['format'] = video_format
        # mp4 formatına dönüştürmeyi garantile
        postprocessors.append({'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'})
        
    opts['ignore_errors'] = True 
    opts['skip_download'] = False 

    opts['postprocessors'] = postprocessors

    return opts

def download_task_wrapper(app, item_id, url_to_download, format_secim, download_path, image_url, metadata):
    """İndirme işlemini ayrı bir iş parçacığında yürütür."""
    app.is_downloading = True
    app.master.after(0, app.update_stop_button_state, True)
    
    item_title = app.download_list_tree.item(item_id, 'values')[1]
    app._safe_log(f"⬇️ Şu An İndiriliyor: {item_title}...", 'info')
    app.master.after(0, lambda: app.update_list_status(item_id, f"⬇️ Başlatılıyor...", tags='info'))
    
    res = app.video_resolution_choice.get() 
    bit = app.audio_bitrate_choice.get() 
    
    downloaded_file_path = None
    
    def pre_hook(d):
         nonlocal downloaded_file_path
         if not app.is_downloading: 
             raise yt_dlp.DownloadError("Kullanıcı tarafından durduruldu.")
         if d['status'] == 'finished' and 'filepath' in d:
             downloaded_file_path = d['filepath']
             
    try:
        opts = get_download_opts(format_secim, download_path, lambda d: app.download_progress_hook(d, item_id), pre_hook, res, bit, image_url, metadata) 
        
        with yt_dlp.YoutubeDL(opts) as ydl: 
            info = ydl.extract_info(url_to_download, download=True)
            
            if 'requested_downloads' in info and info['requested_downloads']:
                downloaded_file_path = info['requested_downloads'][-1].get('filepath')
            elif info and 'title' in info:
                final_filename = ydl.prepare_filename(info)
                if format_secim == 'b':
                     final_filename = os.path.splitext(final_filename)[0] + '.mp3'
                else:
                     final_filename = os.path.splitext(final_filename)[0] + '.mp4'
                downloaded_file_path = final_filename
        
        if app.is_downloading:
            if format_secim == 'v':
                if downloaded_file_path and os.path.exists(downloaded_file_path):
                    is_ok, check_detail = is_tv_compatible(downloaded_file_path, res)
                    
                    if is_ok:
                        app.master.after(0, lambda: app.update_list_status(item_id, f"✅ TAMAMLANDI | {check_detail}", tags='success'))
                        app._safe_log(f"[BAŞARILI] -> {item_title} indirme tamamlandı. TV Uyumluluğu: UYUMLU.", 'success')
                    else:
                        app.master.after(0, lambda: app.update_list_status(item_id, f"⚠️ BİTTİ | {check_detail}", tags='warning'))
                        app._safe_log(f"[UYARI] -> {item_title} indirme tamamlandı. TV Uyumluluğu: UYUMSUZ ({check_detail}).", 'warning')
                else:
                    app.master.after(0, lambda: app.update_list_status(item_id, "TAMAMLANDI (Dosya Yolu Hatası)", tags='warning'))
                    app._safe_log(f"[UYARI] -> {item_title} indirme tamamlandı ancak dosya yolu yakalanamadı.", 'warning')
            else:
                 app.master.after(0, lambda: app.update_list_status(item_id, "✅ TAMAMLANDI (MP3)", tags='success'))
                 app._safe_log(f"[BAŞARILI] -> {item_title} indirme tamamlandı (MP3).", 'success')

            
    except yt_dlp.DownloadError as e:
        error_msg = str(e)
        if "Kullanıcı tarafından durduruldu" in error_msg:
            app._safe_log(f"[DURDURULDU] -> {item_title}", 'warning')
            app.master.after(0, lambda: app.update_list_status(item_id, "DURDURULDU", tags='danger'))
        elif "Video unavailable" in error_msg or "private video" in error_msg or "is not available" in error_msg or "Premieres" in error_msg:
            app._safe_log(f"[HATA] -> {item_title}: Video yayından kaldırılmış/kısıtlanmıştır. Atlanıyor...", 'error')
            app.master.after(0, lambda: app.update_list_status(item_id, "ATLANDI (Kısıtlı)", tags='warning'))
        else:
            app._safe_log(f"[HATA] -> İndirme Hatası: {error_msg}", 'error')
            app.master.after(0, lambda: app.update_list_status(item_id, "HATA", tags='danger'))
    except Exception as e:
        app._safe_log(f"[HATA] -> Beklenmedik Hata: {str(e)}", 'error')
        app.master.after(0, lambda: app.update_list_status(item_id, "HATA", tags='danger'))
    finally:
        app.is_downloading = False
        app.master.after(0, app.update_stop_button_state, False)
        app.master.after(0, app.process_next_in_queue) 

# =======================================================
# 3. ARAYÜZ (GUI) - CUSTOMTKINTER YAPISI
# =======================================================
class DownloaderApp:
    def __init__(self, master): # <-- Hatanın Giderildiği Yer
        self.master = master
        master.title("MelodiaSync | Hızlı İndirme Merkezi (V74 - Tip Hatası Giderildi)")
        master.geometry("1000x750") 
        
        self.log_text_bg = '#2A2D2E' 
        self.log_text_fg = '#FFFFFF'
        self.is_downloading = False 
        self.item_data_map = {} 
        self.manual_selection_lock = threading.Lock() 
        
        self.config = load_config()
        self.match_cache = load_match_cache() 
        self.download_queue = [] 
        
        self.url_input = tk.StringVar()
        self.download_dir = tk.StringVar(value=self.config.get('last_dir', os.path.join(os.path.expanduser('~'), 'Desktop', 'melodia_downloads')))
        self.kaynak_secim = tk.StringVar(value='y')
        self.format_secim = tk.StringVar(value='v')
        
        self.video_resolution_choice = tk.StringVar(value=self.config.get('video_res', 'TV Uyumlu (1080p)')) 
        self.audio_bitrate_choice = tk.StringVar(value=self.config.get('audio_bit', '320'))
        
        self.api_id = tk.StringVar(value=self.config.get('client_id', ''))
        self.api_secret = tk.StringVar(value=self.config.get('client_secret', ''))
        
        self.sp_client = self.get_spotify_api_client() 

        self.style = ttk.Style(theme="cyborg") 
        
        self.create_widgets()
        self.update_ui_options()
        
    def get_spotify_api_client(self):
        client_id = self.api_id.get()
        client_secret = self.api_secret.get()
        
        if client_id and client_secret:
            try:
                return spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret))
            except Exception:
                return None
        return None

    def create_widgets(self):
        main_frame = ctk.CTkFrame(self.master, fg_color="transparent")
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        self.setup_header_frame(main_frame)
        self.setup_main_controls(main_frame)
        self.setup_list_and_image_area(main_frame)
        self.setup_log_area(main_frame)

    def setup_header_frame(self, parent):
        header_frame = ctk.CTkFrame(parent, fg_color="transparent")
        header_frame.pack(fill='x', pady=(0, 10))

        ctk.CTkLabel(header_frame, text="MelodiaSync | Hızlı İndirme Merkezi (V74)", font=ctk.CTkFont(size=20, weight="bold")).pack(side='left', anchor='w')
        
        self.btn_settings = ctk.CTkButton(header_frame, text="⚙️ API & Kalite Ayarları", command=self.show_settings_popup, width=150)
        self.btn_settings.pack(side='right', anchor='e')
        
    def show_settings_popup(self):
        if hasattr(self, 'settings_popup') and self.settings_popup.winfo_exists():
            self.settings_popup.focus()
            return
            
        self.settings_popup = ctk.CTkToplevel(self.master)
        self.settings_popup.title("API & Kalite Ayarları")
        self.settings_popup.geometry("450x350")
        self.settings_popup.grab_set() 
        self.settings_popup.focus()

        api_frame = ctk.CTkFrame(self.settings_popup)
        ctk.CTkLabel(api_frame, text="Spotify API (Halka Açık Listeler)", font=ctk.CTkFont(weight="bold")).pack(anchor='w', pady=(5, 5), padx=10)
        api_frame.pack(fill='x', padx=10, pady=(10, 5))

        ctk.CTkLabel(api_frame, text="Client ID:").pack(anchor='w', pady=(0, 0), padx=10)
        ctk.CTkEntry(api_frame, textvariable=self.api_id).pack(fill='x', pady=2, padx=10)

        ctk.CTkLabel(api_frame, text="Client Secret:").pack(anchor='w', pady=(5, 0), padx=10)
        ctk.CTkEntry(api_frame, textvariable=self.api_secret, show="*").pack(fill='x', pady=2, padx=10)

        ctk.CTkButton(api_frame, text="💾 Kaydet ve API Bağlantısını Güncelle", command=self.save_api).pack(anchor='e', pady=(10, 5), padx=10)

        quality_frame = ctk.CTkFrame(self.settings_popup)
        ctk.CTkLabel(quality_frame, text="Kalite Seçimleri", font=ctk.CTkFont(weight="bold")).pack(anchor='w', pady=(5, 5), padx=10)
        quality_frame.pack(fill='x', padx=10, pady=5)
        
        v_frame = ctk.CTkFrame(quality_frame, fg_color="transparent")
        ctk.CTkLabel(v_frame, text="Video Kalitesi:").pack(side='left', padx=(10, 5))
        self.combo_res = ctk.CTkComboBox(v_frame, values=['PC/Orijinal Kalite (En Hızlı)', 'TV Uyumlu (1080p)', 'Mobil (720p)'], state='readonly', width=200, variable=self.video_resolution_choice)
        self.combo_res.pack(side='left')
        v_frame.pack(fill='x', pady=5)

        a_frame = ctk.CTkFrame(quality_frame, fg_color="transparent")
        ctk.CTkLabel(a_frame, text="MP3 Kalitesi:").pack(side='left', padx=(10, 5))
        self.combo_bit = ctk.CTkComboBox(a_frame, values=['320', '256', '192', '128'], state='readonly', width=200, variable=self.audio_bitrate_choice)
        self.combo_bit.pack(side='left')
        a_frame.pack(fill='x', pady=5)


    def save_api(self):
        self.config['client_id'] = self.api_id.get()
        self.config['client_secret'] = self.api_secret.get()
        self.config['video_res'] = self.video_resolution_choice.get()
        self.config['audio_bit'] = self.audio_bitrate_choice.get()
        save_config(self.config)
        
        self.sp_client = self.get_spotify_api_client()
        
        if self.sp_client:
            self._safe_log("✅ Spotify API bağlantısı güncellendi (Halka Açık Listeler için aktif).", 'success')
        else:
            self._safe_log("❌ Spotify API bağlantısı kurulamadı. ID/Secret kontrol edin.", 'error')
        
        if hasattr(self, 'settings_popup'):
             self.settings_popup.destroy()


    def setup_main_controls(self, parent):
        url_frame = ctk.CTkFrame(parent, fg_color="transparent") 
        url_frame.pack(fill='x', pady=(0, 10))
        
        dir_frame = ctk.CTkFrame(url_frame, fg_color="transparent")
        dir_frame.pack(side='right', padx=(10, 0))
        ctk.CTkEntry(dir_frame, textvariable=self.download_dir, width=250).pack(side='left', fill='x', padx=(0, 5))
        ctk.CTkButton(dir_frame, text="📂 Klasör Seç", command=self.select_dir, width=100).pack(side='left')
        
        entry = ctk.CTkEntry(url_frame, textvariable=self.url_input, font=('Segoe UI', 12), placeholder_text="Spotify Linki, YouTube Linki veya Arama Sorgusu Girin")
        entry.pack(fill='x', expand=True, pady=5, side='left')
        entry.bind('<Return>', lambda event: self.start_process())

        controls_frame = ctk.CTkFrame(parent, fg_color="transparent")
        controls_frame.pack(fill='x', pady=5)
        
        format_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        format_frame.pack(side='left', padx=(0, 20))
        ctk.CTkLabel(format_frame, text="Format:", text_color="#ADAEAE").pack(side='left', padx=(0, 5))
        
        ctk.CTkRadioButton(format_frame, text="MP4", variable=self.format_secim, value='v', command=self.update_ui_options, text_color="#FFFFFF").pack(side='left', padx=5)
        ctk.CTkRadioButton(format_frame, text="MP3", variable=self.format_secim, value='b', command=self.update_ui_options, text_color="#FFFFFF").pack(side='left', padx=10)

        source_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        source_frame.pack(side='left', padx=20)
        ctk.CTkLabel(source_frame, text="Kaynak Tipi:", text_color="#ADAEAE").pack(side='left', padx=(0, 5))
        
        ctk.CTkRadioButton(source_frame, text="YouTube/Arama", variable=self.kaynak_secim, value='y', text_color="#FFFFFF").pack(side='left', padx=5)
        ctk.CTkRadioButton(source_frame, text="Spotify Linki", variable=self.kaynak_secim, value='s', text_color="#FFFFFF").pack(side='left', padx=10)

        action_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        action_frame.pack(side='right', fill='x', expand=True)

        self.btn_start = ctk.CTkButton(action_frame, text="⬇️ KUYRUK BAŞLAT / EKLE", fg_color="#2CC65E", hover_color="#24A34F", command=self.start_process, cursor="hand2")
        self.btn_start.pack(side='left', expand=True, fill='x', ipady=5, padx=(0, 5))

        self.btn_stop = ctk.CTkButton(action_frame, text="🛑 DURDUR", fg_color="#FF3A3A", hover_color="#D13030", command=self.stop_download, state='disabled', cursor="hand2")
        self.btn_stop.pack(side='left', fill='x', ipady=5, padx=5)
        
    def setup_list_and_image_area(self, parent):
        list_image_group = ctk.CTkFrame(parent, fg_color="transparent")
        list_image_group.pack(fill='both', expand=True, pady=(10, 0))

        image_frame = ctk.CTkFrame(list_image_group, width=250) 
        image_frame.pack(side='left', fill='y', padx=(0, 10))
        image_frame.pack_propagate(False) 

        ctk.CTkLabel(image_frame, text="Albüm/Video Kapağı (Teaser)", text_color="#ADAEAE").pack(pady=5)
        
        self.album_art_label = ctk.CTkLabel(image_frame, text="Görsel Yok", fg_color="#2A2D2E", text_color="#ADAEAE")
        self.album_art_label.pack(fill='both', expand=True, padx=5, pady=5)
        
        preview_frame = ctk.CTkFrame(image_frame, fg_color="transparent")
        preview_frame.pack(fill='x', padx=5, pady=5)
        
        self.btn_preview = ctk.CTkButton(preview_frame, text="▶️ Önizle", fg_color="#FFD700", hover_color="#CCAC00", command=self.preview_selected_item, cursor="hand2")
        self.btn_preview.pack(fill='x', pady=(0, 5))
        
        self.btn_remove = ctk.CTkButton(preview_frame, text="🗑️ Seçileni Kaldır", fg_color="#3B8ED4", hover_color="#3073B3", command=self.remove_selected_item, cursor="hand2")
        self.btn_remove.pack(fill='x')


        tree_frame = ttk.Frame(list_image_group) 
        tree_frame.pack(side='left', fill='both', expand=True)
        
        columns = ("#", "Şarkı/Video Adı", "Kaynak Sanatçı", "Albüm/Detay", "Süre", "Durum")
        self.download_list_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', bootstyle="cyborg") 
        
        self.download_list_tree.heading("#", text="#", anchor=CENTER)
        self.download_list_tree.heading("Şarkı/Video Adı", text="Şarkı/Video Adı", anchor=W)
        self.download_list_tree.heading("Kaynak Sanatçı", text="Kaynak Sanatçı", anchor=W)
        self.download_list_tree.heading("Albüm/Detay", text="Albüm/Detay", anchor=W)
        self.download_list_tree.heading("Süre", text="Süre", anchor=CENTER)
        self.download_list_tree.heading("Durum", text="Durum", anchor=CENTER)
        
        self.download_list_tree.column("#", width=30, anchor=CENTER, stretch=NO)
        self.download_list_tree.column("Şarkı/Video Adı", width=250, anchor=W)
        self.download_list_tree.column("Kaynak Sanatçı", width=120, anchor=W)
        self.download_list_tree.column("Albüm/Detay", width=120, anchor=W)
        self.download_list_tree.column("Süre", width=80, anchor=CENTER, stretch=NO)
        self.download_list_tree.column("Durum", width=150, anchor=CENTER)

        vsb = tk.Scrollbar(tree_frame, orient="vertical", command=self.download_list_tree.yview)
        vsb.pack(side='right', fill='y')
        self.download_list_tree.configure(yscrollcommand=vsb.set)
        
        self.download_list_tree.pack(fill='both', expand=True)
        self.download_list_tree.bind("<<TreeviewSelect>>", self.show_album_art)
        
    def setup_log_area(self, parent):
        log_frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(log_frame, text="İşlem Kayıtları", text_color="#ADAEAE").pack(anchor='w')
        log_frame.pack(fill='x', pady=(10, 0))

        self.log_text = tk.Text(log_frame, height=5, state='disabled', bg=self.log_text_bg, fg=self.log_text_fg, font=('Consolas', 9), relief='flat')
        self.log_text.pack(fill='x', expand=True)
        
        self.download_list_tree.tag_configure('success', foreground='#2CC65E') 
        self.download_list_tree.tag_configure('info', foreground='#3B8ED4') 
        self.download_list_tree.tag_configure('warning', foreground='#FFA400') 
        self.download_list_tree.tag_configure('danger', foreground='#FF3A3A') 
        
    # =======================================================
    # 4. YARDIMCI METOTLAR
    # =======================================================
    
    def _safe_log(self, msg, mtype='info', clear=False):
        """V69: Thread'den bağımsız güvenli loglama."""
        try:
            self.master.after(0, lambda: self.log_message(msg, mtype, clear))
        except Exception as e:
            if "main thread is not in main loop" in str(e):
                print(f"TKINTER LOGGING FAILED: {msg}")
            else:
                 print(f"Unexpected logging error: {e}")
    
    def get_image_from_url(self, url, size=(240, 240)):
        """URL'den görseli indirir ve PIL/Tkinter PhotoImage nesnesi döndürür."""
        try:
            response = requests.get(url, stream=True, timeout=5)
            response.raise_for_status()
            image_data = io.BytesIO(response.content)
            img = Image.open(image_data)
            img.thumbnail(size, Image.Resampling.LANCZOS) 
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def show_album_art(self, event):
        selected_item = self.download_list_tree.focus()
        if not selected_item: return

        item_data = self.item_data_map.get(selected_item)
        
        if not item_data or not item_data.get('image_url'):
            self.album_art_label.configure(image=None, text="Görsel Yok / Bulunamadı")
            return

        threading.Thread(target=self._load_image_async, args=(selected_item, item_data['image_url'])).start()

    def _load_image_async(self, item_id, url):
        new_photo = self.get_image_from_url(url)
        
        def update_ui():
            if item_id == self.download_list_tree.focus(): 
                if new_photo:
                    self.album_art_label.configure(image=new_photo, text="")
                    TEMP_IMAGE_CACHE[item_id] = new_photo 
                else:
                    self.album_art_label.configure(image=None, text="Görsel Yüklenemedi")

        self.master.after(0, update_ui)

    def preview_selected_item(self):
        selected_item = self.download_list_tree.focus()
        if not selected_item:
            self._safe_log("Önizlemek için listeden bir öğe seçin.", 'warning'); return

        item_data = self.item_data_map.get(selected_item)
        if not item_data or not item_data.get('url'):
            self._safe_log("Seçilen öğe için geçerli bir URL bulunamadı.", 'error'); return

        url = item_data['url']
        threading.Thread(target=lambda: webbrowser.open(url)).start()
        self._safe_log(f"Önizleme başlatıldı: {item_data['title']} tarayıcıda açılıyor.", 'info')

    def remove_selected_item(self):
        selected_items = self.download_list_tree.selection()
        if not selected_items:
            self._safe_log("Kaldırmak için listeden bir veya birden fazla öğe seçin.", 'warning')
            return

        for item_id in selected_items:
            if self.download_list_tree.item(item_id, 'values')[5] == "KUYRUKTA":
                new_queue = queue.Queue()
                removed = False
                while not DOWNLOAD_QUEUE.empty():
                    try:
                        item = DOWNLOAD_QUEUE.get_nowait()
                        if item[0] != item_id:
                            new_queue.put(item)
                        else:
                            removed = True
                    except queue.Empty:
                        break 
                
                while not new_queue.empty():
                    DOWNLOAD_QUEUE.put(new_queue.get_nowait())
                
                if removed:
                    self._safe_log(f"'{self.download_list_tree.item(item_id, 'values')[1]}' kuyruktan kaldırıldı.", 'info')

            self.download_list_tree.delete(item_id)
            if item_id in self.item_data_map:
                del self.item_data_map[item_id]
                
        self.renumber_list_items()
        
    def renumber_list_items(self):
        global ID_COUNTER
        ID_COUNTER = 0
        children = self.download_list_tree.get_children()
        
        for i, item_id in enumerate(children):
            ID_COUNTER = i + 1
            current_values = list(self.download_list_tree.item(item_id, 'values'))
            current_values[0] = ID_COUNTER
            self.download_list_tree.item(item_id, values=current_values)

    def update_list_status(self, item_id, status_text, tags=None):
        current_values = list(self.download_list_tree.item(item_id, 'values'))
        current_values[5] = status_text 
        
        if tags:
            self.download_list_tree.item(item_id, values=current_values, tags=(tags,))
        else:
            self.download_list_tree.item(item_id, values=current_values, tags=())

    def update_ui_options(self):
        pass 

    def update_stop_button_state(self, is_active):
        self.btn_stop.configure(state='normal' if is_active else 'disabled')
        self.btn_start.configure(state='disabled' if is_active else 'normal')
        
    def stop_download(self):
        if self.is_downloading:
            self.is_downloading = False 
            self._safe_log("Aktif indirme için durdurma komutu gönderildi. İşlemin bitmesi bekleniyor...", 'warning')
            
        while not DOWNLOAD_QUEUE.empty():
            try:
                item_id, _, _, _, _, _ = DOWNLOAD_QUEUE.get_nowait()
                self.master.after(0, lambda id=item_id: self.update_list_status(id, "İptal Edildi", tags='danger'))
            except queue.Empty:
                break
        
        self._safe_log("Kuyruktaki bekleme işlemleri iptal edildi.", 'info')

    def open_download_folder(self):
        folder_path = self.download_dir.get()
        if not os.path.exists(folder_path): os.makedirs(folder_path)
        try:
            if os.name == 'nt': 
                subprocess.Popen(['explorer', folder_path])
            else: 
                subprocess.Popen(['xdg-open' if os.name == 'posix' else 'open', folder_path])
        except Exception as e:
                 self._safe_log(f"Klasör açma hatası: {e}", 'error')

    def select_dir(self):
        d = filedialog.askdirectory()
        if d: 
            self.download_dir.set(d)
            self.config['last_dir'] = d 
            save_config(self.config)

    def log_message(self, msg, mtype='info', clear=False):
        self.log_text.config(state='normal')
        if clear: self.log_text.delete('1.0', 'end')
        
        if not hasattr(self.log_text, 'tag_config_done'):
            self.log_text.tag_config('info', foreground='#3B8ED4')
            self.log_text.tag_config('success', foreground='#2CC65E')
            self.log_text.tag_config('warning', foreground='#FFA400')
            self.log_text.tag_config('error', foreground='#FF3A3A')
            setattr(self.log_text, 'tag_config_done', True)
        
        self.log_text.insert('end', f"{msg}\n", mtype)
        
        self.log_text.see('end')
        self.log_text.config(state='disabled')

    def download_progress_hook(self, d, item_id):
        
        if d['status'] == 'downloading':
            
            p = d.get('_percent_str', None) 
            s = d.get('_eta_str', None)      
            
            if p is None and (d.get('total_bytes') is None and d.get('total_bytes_estimate') is None):
                status_text = "⬇️ İndiriliyor... (İlerleme Bekleniyor)"
            else:
                percent_str = p.strip() if p else 'N/A'
                eta_str = s.strip() if s else 'N/A'
                status_text = f"⬇️ %{percent_str} (Kalan: {eta_str})"
            
            self.master.after(0, lambda: self.update_list_status(item_id, status_text))
            
        elif d['status'] == 'postprocessing':
            
            description = d.get('info_dict', {}).get('postprocessor_data', {}).get('postprocessor', 'İşleniyor')
            description = description.split(':')[0] 
            
            if 'EmbedThumbnail' in description:
                 detail = "Kapak Gömülüyor"
            elif 'Metadata' in description:
                 detail = "Etiketler Ekleniyor"
            elif 'ExtractAudio' in description or 'Convertor' in description:
                 detail = "Dönüştürülüyor"
            else:
                 detail = "İşleniyor"
            
            status_text = f"🔄 {detail} (Son Aşama)"
            
            self.master.after(0, lambda: self.update_list_status(item_id, status_text))
            
        elif d['status'] == 'finished':
             self.master.after(0, lambda: self.update_list_status(item_id, "Bitti (Kontrol Ediliyor)", tags='info'))
            
    def process_next_in_queue(self):
        if not DOWNLOAD_QUEUE.empty() and not self.is_downloading:
            try:
                item_id, url, format_t, path, image_url, metadata = DOWNLOAD_QUEUE.get_nowait() 
                threading.Thread(target=download_task_wrapper, args=(self, item_id, url, format_t, path, image_url, metadata)).start()
            except queue.Empty:
                 pass
        elif DOWNLOAD_QUEUE.empty() and not self.is_downloading:
            self._safe_log("Tüm indirmeler tamamlandı. Yeni komut bekliyor...", 'success')
            
    def add_item_to_list_and_queue(self, index, title, artist, detail, duration, url, format_t, path, image_url='', metadata=None):
        global ID_COUNTER
        ID_COUNTER += 1
        
        item_id = self.download_list_tree.insert("", "end", values=(ID_COUNTER, title, artist, detail, duration, "KUYRUKTA"), tags=('info',))
        
        if metadata is None: metadata = {}
        DOWNLOAD_QUEUE.put((item_id, url, format_t, path, image_url, metadata))
        
        self.item_data_map[item_id] = {
            'title': title,
            'url': url,
            'image_url': image_url
        }
        
        return item_id

    def start_process(self):
        """Ana işlemi (arama/indirme) başlatır."""
        url = self.url_input.get().strip()
        if not url: self._safe_log("Lütfen bir bağlantı veya arama sorgusu girin.", 'error', clear=True); return
        
        if self.kaynak_secim.get() == 's' and (not self.sp_client):
            self._safe_log("Spotify seçildi, ancak API bağlantısı yok. Ayarlar (Client ID/Secret) gereklidir.", 'error', clear=True)
            return

        threading.Thread(target=self._run, args=(url,)).start()

    def _run(self, url):
        """Kaynak tipine göre Spotify veya YouTube işlemini başlatır (Ayrı Thread'de çalışır)."""
        kaynak = self.kaynak_secim.get()
        format_t = self.format_secim.get()
        download_path = self.download_dir.get()
        
        self.master.after(0, self.download_list_tree.delete, *self.download_list_tree.get_children())
        self.item_data_map.clear()
        TEMP_IMAGE_CACHE.clear()
        
        self._safe_log("Yeni kaynak taranıyor...", 'info', clear=True)

        if kaynak == 's':
            # --- SPOTIFY LİSTESİ İŞLEME (V72: Güçlü Arama ve Engelleme) ---
            
            items = spotify_listesini_al(url, self.sp_client)
            
            if not items: 
                self._safe_log("Spotify listesi alınamadı (URL hatalı veya liste ÖZEL/Private olabilir).", 'error'); 
                return
            
            self._safe_log(f"Bulunan şarkı sayısı: {len(items)}. İşleniyor...", 'info')
            
            for i, item in enumerate(items):
                
                # 1. Önbellek Kontrolü
                cache_key = item['cache_key']
                if cache_key in self.match_cache:
                    cached_url = self.match_cache[cache_key]['url']
                    self.master.after(0, lambda u=cached_url, itm=item: self.add_item_to_list_and_queue(
                            index=itm['title'], title=itm['title'], artist=itm['artist'],
                            detail=f"{itm.get('album', 'Spotify Listesi')} (Önbellek)", duration="N/A", url=u,
                            format_t=self.format_secim.get(), path=self.download_dir.get(), image_url=itm.get('image_url'), metadata=item))
                    self._safe_log(f"⚡ Önbellek Eşleşmesi: '{item['title']}' bulundu ve kuyruğa eklendi.", 'success')
                    time.sleep(0.1)
                    continue 
                
                # 2. Agresif Temizlenmiş Sorgu (V68)
                search_results = yt_arama(item['query'], search_limit=SEARCH_LIMIT) 
                
                # V72: HİÇBİR SONUÇ DÖNMEZSE (0 sonuç), basit sorguyu dene
                if not search_results:
                    self._safe_log(f"⚠️ İlk sorgu ('{item['query']}') 0 sonuç döndürdü. Daha basit sorgu deneniyor...", 'warning')
                    search_results = yt_arama(item['simple_query'], search_limit=SEARCH_LIMIT)
                    
                    if not search_results:
                        self._safe_log(f"❌ Basit sorgu da sonuç döndüremedi. Manuel seçime yönlendiriliyor...", 'error')
                
                
                if search_results and (len(search_results) > 1 or 'youtube.com' in url or 'youtu.be' in url):
                     pass
                elif search_results:
                    # Sadece 1 sonuç varsa, otomatik seçebiliriz (Spotify'dan gelmiyorsa)
                    selected_result = search_results[0]
                    try:
                        self.master.after(0, lambda sr=selected_result, itm=item: self.add_item_to_list_and_queue(
                            index=itm['title'], title=sr['title'], artist=itm['artist'],
                            detail=itm.get('album', 'Spotify Listesi'), duration=self._format_duration(sr['duration']),
                            url=sr['webpage_url'], format_t=self.format_secim.get(), path=self.download_dir.get(),
                            image_url=itm.get('image_url'), metadata=itm
                        ))
                        self._safe_log(f"✅ Otomatik Seçim: '{item['title']}' için tek ve en iyi sonuç bulundu ve kuyruğa eklendi.", 'success')
                    except RuntimeError as e:
                        if "main thread is not in main loop" in str(e):
                            self._safe_log(f"CRITICAL: Arayüz Hatası (Öğe Ekleme Başarısız)", 'error')
                        else:
                            raise e
                    time.sleep(0.1)
                    continue

                
                # --- OTOMATİK SEÇİM (MANUEL KAPALI) ---
                if not search_results:
                    self._safe_log(f"❌ Sonuç bulunamadı: '{item['title']}'", 'error')
                    time.sleep(0.1)
                    continue

                # Spotify süresi ile (varsa) en yakın sonucu seç, yoksa ilk sonucu al
                def _mmss_to_sec(s):
                    try:
                        parts = str(s).split(':')
                        if len(parts) == 2:
                            return int(parts[0]) * 60 + int(parts[1])
                    except Exception:
                        pass
                    return 0

                expected = _mmss_to_sec(item.get('duration', '0:00'))
                selected = search_results[0]
                if expected and search_results:
                    best = min(search_results, key=lambda r: abs((r.get('duration') or 0) - expected))
                    if best.get('duration') and abs((best.get('duration') or 0) - expected) <= 3:
                        selected = best

                self.master.after(
                    0,
                    lambda sr=selected, itm=item: self.add_item_to_list_and_queue(
                        index=itm['title'],
                        title=sr.get('title', itm['title']),
                        artist=itm.get('artist', ''),
                        detail=itm.get('album', 'Spotify Listesi'),
                        duration=self._format_duration(sr.get('duration')),
                        url=sr.get('webpage_url', ''),
                        format_t=self.format_secim.get(),
                        path=self.download_dir.get(),
                        image_url=sr.get('image_url') or itm.get('image_url'),
                        metadata=itm
                    )
                )

                self._safe_log(f"⚡ Otomatik Seçim: '{item['title']}' → {selected.get('title','')}", 'success')
                time.sleep(0.1)
                continue
        else: # YouTube Linki veya Arama Sorgusu
            if not url.startswith("http"): 
                 
                 search_query = url
                 if not any(keyword in url.lower() for keyword in ["official", "audio", "video", "clip", "song", "music", "remix", "cover"]):
                     search_query = f"{url} official video" 
                 
                 self._safe_log(f"'{search_query}' sorgusu için YouTube araması yapılıyor ({SEARCH_LIMIT} sonuç)...", 'info')
                 
                 results = yt_arama(search_query, search_limit=SEARCH_LIMIT) 
                 
                 if results:
                     temp_item = {
                         'title': url, 'artist': "YouTube Arama",
                         'album': "Arama", 'release_year': "", 'image_url': '', 'query': search_query
                     }
                     # Otomatik: ilk sonucu seç ve kuyruğa ekle (manuel kapalı)
                     selected = results[0]
                     self.master.after(0, lambda sr=selected, itm=temp_item: self.add_item_to_list_and_queue(
                         1,
                         sr.get('title', itm.get('title','YouTube')),
                         itm.get('artist','YouTube'),
                         itm.get('album','Arama'),
                         self._format_duration(sr.get('duration')),
                         sr.get('webpage_url',''),
                         format_t,
                         download_path,
                     ))
                     self._safe_log(f"⚡ Otomatik Seçim (YouTube Arama): {selected.get('title','')}", 'success')
                 else:
                     self._safe_log(f"'{url}' sorgusu için geçerli bir video bulunamadı.", 'error')
            
            elif url.startswith("http"):
                 self._safe_log("Doğrudan URL işleniyor. Oynatma listesi ise parçalara ayrılacaktır.", 'info')
                 
                 try:
                     ydl_opts_simulate = {'extract_flat': 'in_playlist', 'quiet': True, 'simulate': True, 'ignore_errors': True}
                     with yt_dlp.YoutubeDL(ydl_opts_simulate) as ydl:
                         info = ydl.extract_info(url, download=False)

                     if info is None:
                         self._safe_log("Girdiğiniz URL'den bilgi alınamadı. Geçersiz veya kısıtlanmış olabilir.", 'error')
                         return
                         
                     if 'entries' in info:
                         self._safe_log(f"Liste içeriği bulundu ({len(info['entries'])} öğe). Kuyruğa ekleniyor...", 'info')
                         
                         items_to_add = 0
                         for entry in info['entries']:
                             if entry is None or 'url' not in entry:
                                 self._safe_log("Kısıtlı/Erişilemez bir liste öğesi atlandı.", 'warning')
                                 continue
                                 
                             self.master.after(0, lambda url=entry.get('webpage_url', entry['url']), title=entry.get('title', 'Başlık Yok'), duration=entry.get('duration', 0), info_title=info.get('title', 'Liste'): 
                                 self.add_item_to_list_and_queue( 
                                     1, 
                                     title, 
                                     "YouTube", 
                                     info_title,
                                     self._format_duration(duration), 
                                     url, 
                                     format_t, 
                                     download_path))
                             items_to_add += 1
                         
                         self._safe_log(f"Kuyruğa {items_to_add} geçerli video eklendi. İndirme başlıyor...", 'success')
                         
                     else:
                         self.master.after(0, lambda: self.add_item_to_list_and_queue( 
                                         1, 
                                         info.get('title', url), 
                                         "YouTube", 
                                         "Tek Video", 
                                         self._format_duration(info.get('duration', 0)), 
                                         url, 
                                         format_t, 
                                         download_path))
                         
                 except yt_dlp.DownloadError as e:
                     self._safe_log(f"Oynatma Listesi/URL Taraması Başarısız: {str(e)}", 'error')
                     return
                 except Exception as e:
                     self._safe_log(f"Beklenmedik Taramama Hatası: {str(e)}", 'error')
                     return

            
        if not self.is_downloading: 
            self.master.after(0, self.process_next_in_queue)
            
    def _format_duration(self, seconds):
        """Saniye cinsinden süreyi MM:SS formatına çevirir."""
        if isinstance(seconds, (int, float)) and seconds >= 0:
            seconds = int(seconds)
            return f"{seconds // 60:02d}:{seconds % 60:02d}"
        return "N/A"

# =======================================================
# 5. YENİ PENCERE: SONUÇ SEÇİMİ (MULTI-RESULT - V73 İLE GÖRSELLEŞTİRİLDİ)
# =======================================================
class ResultSelectionWindow(ctk.CTkToplevel):
    def __init__(self, master, app_instance, item, yt_results, allow_manual_search=False):
        super().__init__(master)
        self.app = app_instance
        self.item = item 
        self.yt_results = yt_results
        self.original_query = item['query']
        self.allow_manual_search = allow_manual_search
        self.selected_result_index = -1 
        
        global MANUAL_WINDOW_THUMBNAIL_CACHE 
        MANUAL_WINDOW_THUMBNAIL_CACHE = {} 
        
        title_text = f"🔍 YouTube Sonuçları | {item['title']} - {item['artist']}"

        self.title(title_text)
        self.geometry("1100x750")
        self.resizable(False, False)
        self.grab_set() 
        self.focus()
        
        self.protocol("WM_DELETE_WINDOW", self.on_window_close)

        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        ctk.CTkLabel(main_frame, text=f"Aranan Sorgu: {item['query']}", font=ctk.CTkFont(weight="bold")).pack(pady=(0, 5))
        
        if self.allow_manual_search:
            self.setup_manual_search_controls(main_frame)
            
        self.result_container = ctk.CTkScrollableFrame(main_frame, label_text="YouTube Arama Sonuçları (Resme Tıklayın)", label_font=ctk.CTkFont(size=14))
        self.result_container.pack(fill='both', expand=True, pady=10)

        self.setup_buttons(main_frame)
        self.populate_results(self.yt_results)

    def on_window_close(self):
        """Pencere kapandığında yapılacaklar: Kilidi serbest bırak."""
        if self.app.manual_selection_lock.locked():
             self.app.manual_selection_lock.release()
             self.app._safe_log("Manuel seçim penceresi kapatıldı, kilit serbest bırakıldı.", 'info')
        self.destroy()

    def setup_manual_search_controls(self, parent):
        manual_frame = ctk.CTkFrame(parent, fg_color="transparent")
        manual_frame.pack(fill='x', pady=(0, 10))
        
        ctk.CTkLabel(manual_frame, text="Sorguyu Elle Değiştirin:").pack(anchor='w', pady=(5, 0))
        
        self.manual_query_var = tk.StringVar(value=self.original_query)
        self.query_entry = ctk.CTkEntry(manual_frame, textvariable=self.manual_query_var)
        self.query_entry.pack(side='left', fill='x', expand=True, padx=(0, 5))
        
        ctk.CTkButton(manual_frame, text="🔄 Yeni Sorgu ile Ara", command=self.redo_youtube_search).pack(side='left', width=150)

    def redo_youtube_search(self):
        """Kullanıcının girdiği yeni sorgu ile YouTube'da arama yapar."""
        new_query = self.manual_query_var.get().strip()
        if not new_query:
            messagebox.showerror("Hata", "Lütfen geçerli bir arama sorgusu girin.")
            return
            
        self.item['query'] = new_query

        self.app._safe_log(f"Manuel sorgu ile tekrar aranıyor: '{new_query}'...", 'info')
        
        self.btn_select.configure(state='disabled')
        self.btn_skip.configure(state='disabled')
        
        threading.Thread(target=self._search_and_update, args=(new_query,)).start()

    def _search_and_update(self, query):
        """Arama iş parçacığında çalışır ve sonuçları günceller."""
        self.app.master.after(0, self.app._safe_log, f"YouTube'dan sonuçlar alınıyor...", 'info')
        
        self.yt_results = yt_arama(query, search_limit=SEARCH_LIMIT) 
        
        self.app.master.after(0, self._update_gui_after_search)

    def _update_gui_after_search(self):
        """Yeni arama sonuçlarını arayüzde görselleştirir."""
        self.selected_result_index = -1 

        for widget in self.result_container.winfo_children():
            widget.destroy()
            
        global MANUAL_WINDOW_THUMBNAIL_CACHE 
        MANUAL_WINDOW_THUMBNAIL_CACHE = {} 
            
        self.populate_results(self.yt_results)
        
        self.btn_select.configure(state='normal')
        self.btn_skip.configure(state='normal')
        
        if self.yt_results:
             self.app._safe_log(f"Yeni sorgu ile {len(self.yt_results)} sonuç yüklendi. Lütfen seçin.", 'success')
        else:
            self.app._safe_log("Yeni sorgu ile sonuç bulunamadı. Lütfen sorguyu tekrar deneyin.", 'error')


    def populate_results(self, results):
        """Sonuçları küçük resimli CustomTkinter Frame'lere yükler."""
        if not results:
            ctk.CTkLabel(self.result_container, text="Bu sorgu için sonuç bulunamadı. Lütfen sorguyu değiştirin veya atlayın.", text_color="#FF3A3A").pack(pady=20)
            return

        for i, result in enumerate(results):
            # Her sonuç için ayrı bir çerçeve
            item_frame = ctk.CTkFrame(self.result_container, fg_color=("#F9F9FA", "#3B3B3B"), corner_radius=10, border_color="#3B8ED4", border_width=0)
            item_frame.pack(fill='x', padx=5, pady=5)
            
            # Etkileşim için bağla
            item_frame.bind("<Button-1>", lambda event, index=i, frame=item_frame: self.select_result(index, frame))
            
            # --- 1. Resim Alanı (Sol) ---
            
            # Yer tutucu görsel (120x90)
            placeholder_img = Image.new('RGB', (120, 90), color = '#2A2D2E') 
            placeholder_tk = ImageTk.PhotoImage(placeholder_img)
            MANUAL_WINDOW_THUMBNAIL_CACHE[i] = placeholder_tk
            
            img_label = ctk.CTkLabel(item_frame, image=placeholder_tk, text="")
            img_label.pack(side='left', padx=10, pady=10)
            
            # Resim yükleme iş parçacığını başlat
            threading.Thread(target=self._load_thumbnail_async, args=(i, result['image_url'], img_label)).start()
            
            # --- 2. Metin Alanı (Sağ) ---
            text_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
            text_frame.pack(side='left', fill='both', expand=True, padx=(0, 10))
            
            # Başlık
            ctk.CTkLabel(text_frame, text=result['title'], anchor='w', justify='left', 
                         font=ctk.CTkFont(size=12, weight="bold"), wraplength=550).pack(fill='x', pady=(10, 2))
                         
            # Süre ve URL (Kullanıcı için daha az önemli detaylar)
            ctk.CTkLabel(text_frame, text=f"Süre: {self.app._format_duration(result['duration'])} | URL: {result['webpage_url'][:60]}...", anchor='w', justify='left', 
                         font=ctk.CTkFont(size=10), text_color="#ADAEAE").pack(fill='x', pady=(0, 5))

    def _load_thumbnail_async(self, index, url, label):
        """Küçük resmi indirir ve arayüzde gösterir (Ayrı Thread'de çalışır)."""
        new_photo = self.app.get_image_from_url(url, size=(120, 90))
        
        def update_ui():
            if new_photo:
                # Görseli önbelleğe kaydet
                MANUAL_WINDOW_THUMBNAIL_CACHE[index] = new_photo
                # Arayüzü güncelle
                label.configure(image=new_photo)
            
        self.app.master.after(0, update_ui)

    def select_result(self, index, frame):
        """Bir sonuç çerçevesine tıklandığında seçimi günceller."""
        
        # Önceki seçimi sıfırla
        if self.selected_result_index != -1:
            prev_frame = self.result_container.winfo_children()[self.selected_result_index]
            prev_frame.configure(border_width=0)
            
        # Yeni seçimi ayarla
        self.selected_result_index = index
        frame.configure(border_width=3)
        
        self.app._safe_log(f"Seçim: {self.yt_results[index]['title']}", 'info')
            
    def setup_buttons(self, parent):
        btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
        btn_frame.pack(fill='x', pady=10, padx=10)
        
        self.btn_preview = ctk.CTkButton(btn_frame, text="▶️ Seçileni Önizle", command=self.preview_selected_result, fg_color="#FFD700", hover_color="#CCAC00")
        self.btn_preview.pack(side='left', fill='x', expand=True, padx=5)

        self.btn_select = ctk.CTkButton(btn_frame, text="✅ Seçilen Sonucu Kuyruğa Ekle", command=self.select_and_add, fg_color="#2CC65E", hover_color="#24A34F")
        self.btn_select.pack(side='left', fill='x', expand=True, padx=5)
        
        self.btn_skip = ctk.CTkButton(btn_frame, text="❌ Bu Şarkıyı Atla", command=self.on_window_close, fg_color="#FF3A3A", hover_color="#D13030")
        self.btn_skip.pack(side='right', fill='x', expand=True, padx=5)

    def preview_selected_result(self):
        """Seçili sonucu tarayıcıda açar."""
        if self.selected_result_index == -1:
            messagebox.showerror("Hata", "Lütfen önizlemek için bir sonuç seçin (Resme tıklayın).")
            return

        selected_result = self.yt_results[self.selected_result_index]
        url = selected_result['webpage_url']
        threading.Thread(target=lambda: webbrowser.open(url)).start()
        self.app._safe_log(f"Önizleme başlatıldı: {selected_result['title']} tarayıcıda açılıyor.", 'info')

    def select_and_add(self):
        """Seçilen öğeyi indirme kuyruğuna ekler ve Kalıcı önbelleğe kaydeder."""
        if self.selected_result_index == -1:
            messagebox.showerror("Hata", "Lütfen listeden bir sonuç seçin (Resme tıklayın).")
            return

        selected_result = self.yt_results[self.selected_result_index]
        
        metadata = {
            'title': self.item['title'], 
            'artist': self.item['artist'],
            'album': self.item.get('album', ''),
            'year': self.item.get('release_year', '')
        }
        
        display_artist = self.item['artist']
        detail_info = self.item.get('album', 'Manuel Seçim')

        # Kalıcı Önbelleğe Kaydetme
        if 'cache_key' in self.item and self.item['cache_key']:
             cache_key = self.item['cache_key']
             self.app.match_cache[cache_key] = {
                 'url': selected_result['webpage_url'],
                 'youtube_title': selected_result['title']
             }
             save_match_cache(self.app.match_cache)
             self.app._safe_log(f"✅ '{self.item['title']}' için eşleşme kalıcı önbelleğe kaydedildi.", 'info')
        else:
             self.app._safe_log(f"Seçilen arama sonucu kuyruğa eklendi. (Önbellek kaydı yapılmadı)", 'info')


        # Kuyruğa ekle
        self.app.master.after(0, lambda: self.app.add_item_to_list_and_queue(
            index=self.item['title'], 
            title=selected_result['title'], # YouTube başlığı
            artist=display_artist,
            detail=detail_info, 
            duration=self.app._format_duration(selected_result['duration']),
            url=selected_result['webpage_url'],
            format_t=self.app.format_secim.get(),
            path=self.app.download_dir.get(),
            image_url=self.item.get('image_url', ''), # Spotify'dan gelen kapak resmi
            metadata=metadata
        ))
        
        self.app._safe_log(f"'{self.item['title']}' için manuel seçim kuyruğa eklendi. ({selected_result['title']})", 'success')
        
        # Seçim yapıldıktan sonra pencereyi kapat ve kilidi serbest bırak
        self.on_window_close() 


if __name__ == '__main__':
    root = ctk.CTk() 
    
    if os.name == 'nt': 
        try:
            root.iconbitmap() 
        except Exception:
            pass
        
    app = DownloaderApp(root)  # <-- Buradaki çağrı düzeltildi: root argümanı eklendi.
    
    def on_closing():
        if app.is_downloading:
            app.stop_download() 
            time.sleep(1) 
        # Uygulama kapatılırken kilit serbest bırakılmalı
        if app.manual_selection_lock.locked():
            app.manual_selection_lock.release()
            
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    root.mainloop()
