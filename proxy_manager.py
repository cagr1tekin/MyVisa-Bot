#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proxy Manager - Background Updater ile Optimize Edilmiş
Her 1 dakikada proxy'leri test edip JSON cache'e yazar.
"""

import requests
import threading
import time
import json
import os
import logging
import random
import concurrent.futures
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse

# Path helper import et
from config.paths import get_project_path, ensure_directories

logger = logging.getLogger(__name__)

class ProxyManager:
    """Proxy pool ve blacklist yönetimi - Background updater ile"""
    
    def __init__(self, proxy_pool_file: str = None, blacklist_file: str = None,
                 working_proxies_file: str = None, valid_proxy_pool_file: str = None):
        """ProxyManager başlatıcı"""
        # Path ayarları
        self.proxy_pool_file = proxy_pool_file or get_project_path("proxies", "proxy_pool.txt")
        self.blacklist_file = blacklist_file or get_project_path("proxies", "blacklist.txt")
        self.working_proxies_file = working_proxies_file or get_project_path("proxies", "working_proxies.txt")
        self.valid_proxy_pool_file = valid_proxy_pool_file or get_project_path("proxies", "proxy_pool.json")
        
        # Gerekli dizinleri oluştur
        ensure_directories()
        
        # Timeout ayarları - daha agresif
        self.test_timeout = 3  # 7'den 3'e düşürüldü
        self.max_failures = 1  # Bir başarısızlıkta blacklist'e al
        self.test_batch_size = 15  # Daha fazla proxy paralel test et
        
        # Background updater ayarları
        self._background_thread = None
        self._stop_background = False
        self._update_interval = 60  # 60 saniye
        self._test_cooldown = 300  # 5 dakika cooldown
        self._last_tested_proxies = {}  # proxy_url: last_test_time
        self.last_update_time = 0
        
        # Thread-safe operations için lock
        self._lock = threading.Lock()
        
        logger.info("ProxyManager başlatıldı - Background updater sistemi ile")
    
    def load_valid_proxies(self) -> List[str]:
        """Geçerli proxy'leri yükle (önce JSON cache, sonra blacklist hariç)"""
        try:
            # 1. JSON cache'den dene (hızlı)
            if os.path.exists(self.valid_proxy_pool_file):
                with open(self.valid_proxy_pool_file, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                
                # Cache yaşını kontrol et (5 dakikadan eski değilse kullan)
                cache_time = datetime.fromisoformat(cached_data.get('last_updated', '2000-01-01'))
                if datetime.now() - cache_time < timedelta(minutes=5):
                    valid_proxies = cached_data.get('valid_proxies', [])
                    logger.debug("JSON cache'den proxy'ler yüklendi: %d adet", len(valid_proxies))
                    return valid_proxies
                else:
                    logger.debug("JSON cache eski, yeniden hesaplanacak")
            
            # 2. Fallback: Manual hesaplama (yavaş)
            return self._calculate_valid_proxies()
            
        except Exception as e:
            logger.error("Proxy yükleme hatası: %s", str(e))
            return self._calculate_valid_proxies()
    
    def _calculate_valid_proxies(self) -> List[str]:
        """Proxy pool'dan blacklist'i çıkararak geçerli proxy'leri hesapla"""
        try:
            # Proxy pool'u yükle
            pool = set()
            if os.path.exists(self.proxy_pool_file):
                with open(self.proxy_pool_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            pool.add(line)
            
            # Blacklist'i yükle
            blacklist = set()
            if os.path.exists(self.blacklist_file):
                with open(self.blacklist_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            # Sadece proxy URL kısmını al (comment kısmını çıkar)
                            proxy_url = line.split('#')[0].strip()
                            if proxy_url:
                                blacklist.add(proxy_url)
            
            # Geçerli proxy'leri döndür (pool - blacklist)
            valid_proxies = list(pool - blacklist)
            
            logger.info("Proxy istatistikleri: Pool=%d, Blacklist=%d, Geçerli=%d", 
                       len(pool), len(blacklist), len(valid_proxies))
            
            return valid_proxies
            
        except Exception as e:
            logger.error("Proxy hesaplama hatası: %s", str(e))
            return []
    
    def _save_valid_proxy_cache(self, valid_proxies: List[str], tested_proxies: List[str] = None):
        """Geçerli proxy'leri JSON cache'e kaydet"""
        try:
            cache_data = {
                'last_updated': datetime.now().isoformat(),
                'valid_proxies': valid_proxies,
                'tested_count': len(tested_proxies) if tested_proxies else 0,
                'total_pool_count': len(valid_proxies)
            }
            
            with self._lock:
                with open(self.valid_proxy_pool_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, indent=2, ensure_ascii=False)
            
            logger.info("Proxy cache güncellendi: %d geçerli proxy", len(valid_proxies))
            
        except Exception as e:
            logger.error("Proxy cache kaydetme hatası: %s", str(e))
    
    def add_to_blacklist(self, proxy_url: str, reason: str = "Failed"):
        """Proxy'yi blacklist'e ekle"""
        try:
            # Blacklist'te zaten var mı kontrol et
            existing_blacklist = set()
            if os.path.exists(self.blacklist_file):
                with open(self.blacklist_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            existing_blacklist.add(line.split('#')[0].strip())
            
            # Yeni proxy'yi ekle
            if proxy_url not in existing_blacklist:
                with self._lock:
                    with open(self.blacklist_file, 'a', encoding='utf-8') as f:
                        f.write(f"{proxy_url}  # {reason} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                
                logger.warning("BLACKLIST'E EKLENDİ: %s (Sebep: %s)", proxy_url, reason)
                
                # Cache'i invalidate et
                self._invalidate_cache()
                return True
            else:
                logger.debug("Proxy zaten blacklist'te: %s", proxy_url)
                return False
                
        except Exception as e:
            logger.error("Blacklist ekleme hatası: %s", str(e))
            return False
    
    def _invalidate_cache(self):
        """Proxy cache'ini invalidate et"""
        try:
            if os.path.exists(self.valid_proxy_pool_file):
                os.remove(self.valid_proxy_pool_file)
                logger.debug("Proxy cache invalidate edildi")
        except Exception as e:
            logger.debug("Cache invalidate hatası: %s", str(e))
    
    def remove_from_blacklist(self, proxy_url: str):
        """Proxy'yi blacklist'ten çıkar"""
        try:
            if not os.path.exists(self.blacklist_file):
                return False
            
            # Tüm satırları oku
            lines = []
            removed = False
            
            with open(self.blacklist_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line_stripped = line.strip()
                    # Proxy URL'si bu satırda var mı?
                    if line_stripped and not line_stripped.startswith('#'):
                        if proxy_url in line_stripped:
                            removed = True
                            logger.info("BLACKLIST'TEN ÇIKARILDI: %s", proxy_url)
                            continue
                    lines.append(line)
            
            # Dosyayı yeniden yaz
            if removed:
                with self._lock:
                    with open(self.blacklist_file, 'w', encoding='utf-8') as f:
                        f.writelines(lines)
                
                # Cache'i invalidate et
                self._invalidate_cache()
            
            return removed
            
        except Exception as e:
            logger.error("Blacklist çıkarma hatası: %s", str(e))
            return False
    
    def save_working_proxies(self, working_proxies: List[str]):
        """Çalışan proxy'leri ayrı dosyaya kaydet"""
        try:
            with open(self.working_proxies_file, 'w', encoding='utf-8') as f:
                f.write(f"# Çalışan Proxy'ler - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# Toplam: {len(working_proxies)} adet\n\n")
                
                for proxy in working_proxies:
                    f.write(f"{proxy}\n")
            
            logger.info("Çalışan proxy'ler kaydedildi: %d adet", len(working_proxies))
            
        except Exception as e:
            logger.error("Çalışan proxy kaydetme hatası: %s", str(e))
    
    def test_and_filter_proxies(self, proxies: List[str], max_test: int = 5, respect_cooldown: bool = True) -> List[str]:
        """Proxy'leri test et ve çalışanları döndür"""
        import requests
        
        # Cooldown kontrolü ile proxy'leri filtrele
        if respect_cooldown:
            test_proxies = []
            current_time = time.time()
            
            for proxy_url in proxies:
                last_test = self._last_tested_proxies.get(proxy_url, 0)
                if current_time - last_test >= self._test_cooldown:
                    test_proxies.append(proxy_url)
                    if len(test_proxies) >= max_test:
                        break
        else:
            # Cooldown yok, random seç
            test_proxies = random.sample(proxies, min(len(proxies), max_test))
        
        if not test_proxies:
            logger.debug("Cooldown nedeniyle test edilecek proxy yok")
            return []
        
        working_proxies = []
        test_url = "http://httpbin.org/ip"
        current_time = time.time()
        
        logger.info("Proxy test başlatıldı: %d proxy test edilecek", len(test_proxies))
        
        for proxy_url in test_proxies:
            try:
                # Test zamanını kaydet
                self._last_tested_proxies[proxy_url] = current_time
                
                # Proxy formatını düzenle
                if not proxy_url.startswith('http://'):
                    proxy_dict = {'http': f'http://{proxy_url}', 'https': f'http://{proxy_url}'}
                else:
                    proxy_dict = {'http': proxy_url, 'https': proxy_url}
                
                # Test et
                response = requests.get(test_url, proxies=proxy_dict, timeout=3)
                
                if response.status_code == 200:
                    working_proxies.append(proxy_url)
                    logger.debug("✅ Proxy çalışıyor: %s", proxy_url)
                else:
                    logger.debug("❌ Proxy HTTP error %d: %s", response.status_code, proxy_url)
                    
            except Exception as e:
                logger.debug("❌ Proxy test hatası %s: %s", proxy_url, str(e))
        
        logger.info("Proxy test tamamlandı: %d/%d çalışıyor", len(working_proxies), len(test_proxies))
        return working_proxies
    
    def _background_proxy_updater(self):
        """Background thread'de çalışan proxy updater"""
        logger.info("🔄 Background proxy updater başlatıldı (Her %d saniyede)", self._update_interval)
        
        while not self._stop_background:
            try:
                start_time = time.time()
                
                # Geçerli proxy'leri al
                valid_proxies = self._calculate_valid_proxies()
                
                if valid_proxies:
                    # Proxy'leri test et (cooldown ile)
                    working_proxies = self.test_and_filter_proxies(
                        valid_proxies, 
                        max_test=10,  # Background'da daha fazla test
                        respect_cooldown=True
                    )
                    
                    # Cache'i güncelle
                    self._save_valid_proxy_cache(valid_proxies, working_proxies)
                    
                    if working_proxies:
                        logger.info("🔄 Background update: %d/%d proxy çalışıyor", 
                                   len(working_proxies), len(valid_proxies))
                    else:
                        logger.warning("🔄 Background update: Hiç çalışan proxy bulunamadı")
                else:
                    logger.warning("🔄 Background update: Proxy pool boş")
                
                # Süre hesapla
                elapsed = time.time() - start_time
                sleep_time = max(0, self._update_interval - elapsed)
                
                # Stop signal kontrolü ile bekle
                for _ in range(int(sleep_time)):
                    if self._stop_background:
                        break
                    time.sleep(1)
                
            except Exception as e:
                logger.error("Background proxy updater hatası: %s", str(e))
                time.sleep(self._update_interval)
        
        logger.info("🛑 Background proxy updater durduruldu")
    
    def start_background_proxy_updater(self):
        """Background proxy updater'ı başlat"""
        if self._background_thread and self._background_thread.is_alive():
            logger.warning("Background proxy updater zaten çalışıyor")
            return False
        
        self._stop_background = False
        self._background_thread = threading.Thread(
            target=self._background_proxy_updater,
            name="ProxyUpdater",
            daemon=True
        )
        self._background_thread.start()
        
        logger.info("✅ Background proxy updater başlatıldı")
        return True
    
    def stop_background_proxy_updater(self):
        """Background proxy updater'ı durdur"""
        if not self._background_thread or not self._background_thread.is_alive():
            logger.warning("Background proxy updater zaten durmuş")
            return False
        
        self._stop_background = True
        
        # Thread'in bitmesini bekle (max 5 saniye)
        self._background_thread.join(timeout=5)
        
        if self._background_thread.is_alive():
            logger.warning("Background proxy updater durdurulamadı (timeout)")
            return False
        else:
            logger.info("✅ Background proxy updater durduruldu")
            return True
    
    def get_background_status(self) -> Dict:
        """Background updater durumunu döndür"""
        return {
            'running': self._background_thread and self._background_thread.is_alive(),
            'tested_proxies_count': len(self._last_tested_proxies),
            'update_interval': self._update_interval,
            'test_cooldown': self._test_cooldown,
            'cache_exists': os.path.exists(self.valid_proxy_pool_file)
        }
    
    def get_stats(self) -> dict:
        """Proxy istatistiklerini döndür"""
        try:
            pool_count = 0
            blacklist_count = 0
            
            # Pool sayısı
            if os.path.exists(self.proxy_pool_file):
                with open(self.proxy_pool_file, 'r', encoding='utf-8') as f:
                    pool_count = sum(1 for line in f if line.strip() and not line.startswith('#'))
            
            # Blacklist sayısı
            if os.path.exists(self.blacklist_file):
                with open(self.blacklist_file, 'r', encoding='utf-8') as f:
                    blacklist_count = sum(1 for line in f if line.strip() and not line.startswith('#'))
            
            valid_count = len(self.load_valid_proxies())
            
            # Background status
            bg_status = self.get_background_status()
            
            return {
                'pool_total': pool_count,
                'blacklisted': blacklist_count,
                'valid_proxies': valid_count,
                'success_rate': f"{(valid_count/pool_count*100):.1f}%" if pool_count > 0 else "0%",
                'background_running': bg_status['running'],
                'tested_proxies_cache': bg_status['tested_proxies_count'],
                'cache_available': bg_status['cache_exists']
            }
            
        except Exception as e:
            logger.error("İstatistik hatası: %s", str(e))
            return {}

def main():
    """Test fonksiyonu"""
    import sys
    
    # Logging ayarla
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    pm = ProxyManager()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == '--stats':
            # İstatistikleri göster
            stats = pm.get_stats()
            print("📊 Proxy Manager İstatistikleri:")
            for key, value in stats.items():
                print(f"  {key}: {value}")
                
        elif sys.argv[1] == '--test':
            # Proxy'leri test et
            valid_proxies = pm.load_valid_proxies()
            print(f"Geçerli proxy sayısı: {len(valid_proxies)}")
            
            if valid_proxies:
                working = pm.test_and_filter_proxies(valid_proxies, max_test=5, respect_cooldown=False)
                pm.save_working_proxies(working)
                print(f"Çalışan proxy sayısı: {len(working)}")
                
        elif sys.argv[1] == '--background':
            # Background updater'ı başlat
            pm.start_background_proxy_updater()
            print("Background proxy updater başlatıldı...")
            
            try:
                while True:
                    time.sleep(10)
                    stats = pm.get_stats()
                    print(f"📊 Background Status: {stats['background_running']}, Cache: {stats['cache_available']}")
            except KeyboardInterrupt:
                print("\nBackground updater durduruluyor...")
                pm.stop_background_proxy_updater()
                
        elif sys.argv[1] == '--blacklist' and len(sys.argv) > 2:
            # Proxy'yi blacklist'e ekle
            proxy_url = sys.argv[2]
            reason = sys.argv[3] if len(sys.argv) > 3 else "Manual"
            pm.add_to_blacklist(proxy_url, reason)
            
        else:
            print("Kullanım:")
            print("  python proxy_manager.py --stats           # İstatistikleri göster")
            print("  python proxy_manager.py --test            # Proxy'leri test et")
            print("  python proxy_manager.py --background      # Background updater başlat")
            print("  python proxy_manager.py --blacklist URL   # Proxy'yi blacklist'e ekle")
    else:
        # Varsayılan: geçerli proxy'leri listele
        valid_proxies = pm.load_valid_proxies()
        print(f"Geçerli proxy sayısı: {len(valid_proxies)}")
        for i, proxy in enumerate(valid_proxies[:5], 1):
            print(f"  {i}. {proxy}")
        if len(valid_proxies) > 5:
            print(f"  ... ve {len(valid_proxies) - 5} proxy daha")

if __name__ == "__main__":
    main() 