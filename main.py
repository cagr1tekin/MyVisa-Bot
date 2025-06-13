#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Randevu Bot - Ana Kontrol Scripti
Background proxy updater ile hızlı randevu kontrolü (5 dakikada bir).
ProxyManager kullanarak proxy testini optimizasyonu.
"""

import logging
import time
import os
import signal
import sys

from sites.usvisa import USVisaChecker
from sites.idata import IdataChecker
from sites.vfsglobal import VFSGlobalChecker
from sites.vfsglobal_main import VFSGlobalMainChecker
from sites.blsspainvisa import BLSSpainChecker
from sites.canadavisa import CanadaVisaChecker
from proxy_manager import ProxyManager
from config.paths import PROXY_LIST_FILE, PROXY_POOL_FILE, ensure_directories

# Logging yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('randevu_bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Console output için UTF-8 encoding ayarla (Windows için)
if sys.platform.startswith('win'):
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

logger = logging.getLogger(__name__)

# Global ProxyManager instance
proxy_manager = None


def signal_handler(signum, frame):
    """Graceful shutdown için signal handler"""
    global proxy_manager
    logger.info("Shutdown sinyali alındı...")
    
    if proxy_manager:
        logger.info("Background proxy updater durduruluyor...")
        proxy_manager.stop_background_proxy_updater()
    
    logger.info("Bot durduruluyor...")
    sys.exit(0)


def setup_legacy_proxy_files():
    """Eski proxy sisteminden yeni sisteme geçiş"""
    try:
        # Eski proxy_list.txt varsa proxies/ klasörüne taşı
        if os.path.exists(PROXY_LIST_FILE) and not os.path.exists(PROXY_POOL_FILE):
            ensure_directories()
            
            # İçeriği oku ve yeni formata çevir
            with open(PROXY_LIST_FILE, 'r', encoding='utf-8') as f:
                old_proxies = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            
            # Yeni proxy pool dosyasına yaz
            with open(PROXY_POOL_FILE, 'w', encoding='utf-8') as f:
                f.write("# Proxy Pool - Ana proxy listesi\n")
                f.write(f"# Toplam: {len(old_proxies)} proxy\n")
                f.write("# Format: IP:PORT veya http://IP:PORT\n\n")
                
                for proxy in old_proxies:
                    f.write(f"{proxy}\n")
            
            logger.info("Eski proxy_list.txt -> proxies/proxy_pool.txt'ye taşındı (%d proxy)", len(old_proxies))
            
            # Eski dosyayı yedekle
            os.rename(PROXY_LIST_FILE, PROXY_LIST_FILE + ".backup")
            
        return True
        
    except Exception as e:
        logger.error("Legacy proxy dosya taşıma hatası: %s", str(e))
        return False


def update_proxies_if_available():
    """update_proxies.py varsa çalıştırarak güncel proxy listesini güncelle"""
    try:
        # update_proxies.py dosyasının varlığını kontrol et
        if not os.path.exists('update_proxies.py'):
            logger.warning("update_proxies.py bulunamadı, mevcut proxy'ler kullanılacak")
            return False
        
        logger.info("Proxy güncelleme başlatılıyor...")
        print("Güncel proxy listesi alınıyor...")
        
        # ProxyUpdater'ı import et ve çalıştır
        from update_proxies import ProxyUpdater
        
        updater = ProxyUpdater()
        success = updater.update_proxy_list(test_proxies=False)  # Test etmeyelim, background halleder
        
        if success:
            # Yeni proxy'leri proxy_pool.txt'ye ekle
            if os.path.exists(PROXY_LIST_FILE):
                with open(PROXY_LIST_FILE, 'r', encoding='utf-8') as f:
                    new_proxies = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                
                # Mevcut proxy pool'a ekle
                if new_proxies:
                    with open(PROXY_POOL_FILE, 'a', encoding='utf-8') as f:
                        f.write(f"\n# Güncellenen proxy'ler - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        for proxy in new_proxies:
                            f.write(f"{proxy}\n")
                    
                    logger.info("Proxy güncelleme başarılı - %d yeni proxy eklendi", len(new_proxies))
                    
                    # Eski dosyayı sil
                    os.remove(PROXY_LIST_FILE)
            
            return True
        else:
            logger.warning("Proxy güncelleme başarısız, mevcut proxy'ler kullanılacak")
            return False
            
    except ImportError as e:
        logger.warning("update_proxies modülü yok: %s", str(e))
        return False
    except Exception as e:
        logger.error("Proxy güncelleme sırasında hata: %s", str(e))
        return False


def send_telegram_notification(message):
    """Telegram bildirimi gönder"""
    try:
        # Telegram.py modülünden fonksiyonu çağır
        from telegram import send_telegram
        return send_telegram(message)
    except ImportError:
        logger.warning("Telegram modülü bulunamadı, konsola yazdırılıyor")
        print(f"TELEGRAM: {message}")
        return True
    except Exception as e:
        logger.error("Telegram gönderim hatası: %s", str(e))
        return False


def check_proxy_system():
    """Proxy sisteminin durumunu kontrol et"""
    global proxy_manager
    
    logger.info("Proxy sistemi kontrol ediliyor...")
    
    # Legacy dosyaları taşı
    setup_legacy_proxy_files()
    
    # Proxy pool dosyası var mı?
    if not os.path.exists(PROXY_POOL_FILE):
        logger.error(f"{PROXY_POOL_FILE} dosyası bulunamadı!")
        print("❌ Proxy pool dosyası bulunamadı!")
        
        # Eski proxy_list.txt var mı?
        if os.path.exists(PROXY_LIST_FILE):
            print("💡 proxy_list.txt bulundu, taşınıyor...")
            setup_legacy_proxy_files()
        else:
            print("Çözüm önerileri:")
            print("1. 'python update_proxies.py' komutunu çalıştırın")
            print("2. Manuel olarak 'proxies/proxy_pool.txt' dosyası oluşturun")
            return False
    
    # ProxyManager'ı başlat
    proxy_manager = ProxyManager()
    
    # İlk proxy yüklemesi
    valid_proxies = proxy_manager.load_valid_proxies()
    
    if not valid_proxies:
        logger.warning("Hiç geçerli proxy bulunamadı! Proxy güncelleme deneniyor...")
        print("⚠️ Hiç geçerli proxy yok, güncelleme deneniyor...")
        
        # Proxy güncellemeyi dene
        update_success = update_proxies_if_available()
        
        # Tekrar dene
        valid_proxies = proxy_manager.load_valid_proxies()
        
        if not valid_proxies:
            logger.error("Proxy güncellemeden sonra hala hiç geçerli proxy yok!")
            print("❌ Hiç çalışan proxy bulunamadı!")
            print("Lütfen proxy kaynaklarınızı kontrol edin.")
            return False
    
    # İstatistikleri göster
    stats = proxy_manager.get_stats()
    logger.info("Proxy Durumu: Pool=%s, Blacklist=%s, Geçerli=%s (%s)", 
               stats.get('pool_total', '?'), 
               stats.get('blacklisted', '?'),
               stats.get('valid_proxies', '?'),
               stats.get('success_rate', '?'))
    
    print(f"✅ Proxy sistemi hazır: {stats.get('valid_proxies', '?')} geçerli proxy")
    
    return True


def run_background_proxy_updater():
    """Background proxy updater'ı başlat"""
    global proxy_manager
    
    if not proxy_manager:
        logger.error("ProxyManager başlatılmamış!")
        return False
    
    # Background updater'ı başlat
    success = proxy_manager.start_background_proxy_updater()
    
    if success:
        logger.info("✅ Background proxy updater başlatıldı")
        print("🔄 Background proxy updater aktif - proxy'ler sürekli güncelleniyor")
        return True
    else:
        logger.warning("Background proxy updater başlatılamadı")
        return False


def main():
    """Ana kontrol döngüsü - Optimized 5 dakikada bir çalışır"""
    global proxy_manager
    
    # Signal handler'ları kur
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("🚀 Randevu Bot başlatılıyor (Optimized Proxy System)...")
    print("🚀 Randevu Bot - Hızlı proxy sistemi ile başlatılıyor...")
    
    # Proxy sistemini kontrol et ve kur
    if not check_proxy_system():
        logger.error("Proxy sistemi kurulum hatası!")
        return 1
    
    # İlk proxy güncelleme
    logger.info("İlk proxy güncelleme yapılıyor...")
    update_proxies_if_available()
    
    # Background proxy updater'ı başlat
    if not run_background_proxy_updater():
        logger.warning("Background updater başlatılamadı, manual proxy yönetimi kullanılacak")
    
    # Checker nesnelerini oluştur
    logger.info("Site checker'ları başlatılıyor...")
    checkers = {
        'ABD Vize': USVisaChecker(),
        'Almanya Vize': IdataChecker(),
        'İtalya Vize': VFSGlobalChecker(),
        'VFS Global': VFSGlobalMainChecker(),
        'İspanya BLS': BLSSpainChecker(),
        'Kanada Vize': CanadaVisaChecker()
    }
    
    print(f"✅ {len(checkers)} site checker hazır")
    print("🔄 Ana kontrol döngüsü başlatılıyor...")
    
    cycle_count = 0
    
    while True:
        try:
            cycle_count += 1
            cycle_start = time.time()
            
            logger.info("=" * 50)
            logger.info("RANDEVU KONTROLÜ #%d başlatılıyor...", cycle_count)
            print(f"\n🔍 Kontrol Döngüsü #{cycle_count} - {time.strftime('%H:%M:%S')}")
            
            # Hızlı proxy durumu
            valid_proxies = proxy_manager.load_valid_proxies()
            bg_status = proxy_manager.get_background_status()
            
            print(f"📊 Proxy: {len(valid_proxies)} geçerli, Background: {'🟢' if bg_status['running'] else '🔴'}")
            
            if not valid_proxies:
                logger.warning("Hiç geçerli proxy yok! 30 saniye beklenecek...")
                print("⚠️ Hiç geçerli proxy yok, 30s bekle...")
                time.sleep(30)
                continue
            
            # Her checker'ı çalıştır
            results = {}
            
            for site_name, checker in checkers.items():
                try:
                    check_start = time.time()
                    logger.info("%s kontrol ediliyor...", site_name)
                    
                    # Site tipine göre farklı kontrol fonksiyonları
                    if site_name == 'ABD Vize':
                        result = checker.check()
                    elif site_name in ['Almanya Vize', 'İtalya Vize']:
                        result = checker.check_appointments()
                    else:
                        result = checker.check_appointments()
                    
                    check_duration = time.time() - check_start
                    
                    if result:
                        logger.info("🎉 %s randevu bulundu! (%.1fs)", site_name, check_duration)
                        print(f"🎉 {site_name} - RANDEVU BULUNDU!")
                        
                        # Telegram bildirimi
                        message = f"🎉 {site_name} randevu bulundu!"
                        if isinstance(result, str) and result != True:
                            message += f"\n{result}"
                        
                        send_telegram_notification(message)
                        results[site_name] = result
                    else:
                        logger.info("❌ %s randevu yok (%.1fs)", site_name, check_duration)
                        print(f"❌ {site_name} - randevu yok (%.1fs)" % check_duration)
                        
                except Exception as e:
                    logger.error("%s kontrolünde hata: %s", site_name, str(e))
                    print(f"❌ {site_name} - HATA: {str(e)}")
            
            # Döngü süresi
            cycle_duration = time.time() - cycle_start
            
            logger.info("Kontrol tamamlandı - Süre: %.1f saniye", cycle_duration)
            print(f"✅ Kontrol #{cycle_count} tamamlandı - {cycle_duration:.1f}s")
            
            # Randevu bulunmuşsa özel mesaj
            if results:
                success_msg = f"🎉 #{cycle_count} döngüsünde {len(results)} randevu bulundu!"
                logger.info(success_msg)
                print(success_msg)
                send_telegram_notification(success_msg)
            
            # Proxy istatistikleri (her 10 döngüde bir)
            if cycle_count % 10 == 0:
                stats = proxy_manager.get_stats()
                stats_msg = f"📊 Döngü #{cycle_count} - Proxy Stats: {stats.get('valid_proxies', '?')} geçerli, {stats.get('success_rate', '?')} başarı"
                logger.info(stats_msg)
                print(stats_msg)
            
            # 5 dakika bekle (300 saniye)
            logger.info("5 dakika bekleniyor...")
            
            # Güzel countdown
            remaining = 300
            while remaining > 0:
                if remaining % 60 == 0:
                    print(f"⏳ {remaining//60} dakika kaldı...")
                time.sleep(60)
                remaining -= 60
            
        except KeyboardInterrupt:
            logger.info("Kullanıcı tarafından durduruldu")
            break
        except Exception as e:
            logger.error("Ana döngüde hata oluştu: %s", str(e))
            send_telegram_notification(f"🚨 Bot genel hatası: {str(e)}")
            
            # Hata durumunda kısa bekle
            print("❌ Hata nedeniyle 60s bekle...")
            time.sleep(60)
    
    # Cleanup
    if proxy_manager:
        logger.info("Background proxy updater durduruluyor...")
        proxy_manager.stop_background_proxy_updater()
    
    logger.info("🛑 Randevu bot durduruldu")
    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
