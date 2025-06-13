#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proxy Güncelleme Sistemi
Free-proxy-list.net'ten proxy'leri çeker, 
filtreler ve çalışanları proxy_list.txt dosyasına kaydeder.
"""

import requests
import json
import logging
import time
import sys
import random
import concurrent.futures
import urllib3
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# Path helper import et
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.paths import PROXY_LIST_FILE, ensure_directories

# SSL uyarılarını suppress et
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Logging yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


class ProxyUpdater:
    """Proxy güncelleme işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.test_url = "https://httpbin.org/ip"  # Proxy test için kullanılacak URL
        self.max_workers = 15  # Daha fazla paralel test
        self.timeout = 3  # Agresif timeout (7'den 3'e)
        
        # Çoklu proxy kaynakları
        self.proxy_sources = [
            {
                'name': 'free-proxy-list.net',
                'url': 'https://free-proxy-list.net/',
                'parser': 'parse_free_proxy_list'
            },
            {
                'name': 'proxyscrape.com',
                'url': 'https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=elite,anonymous',
                'parser': 'parse_proxyscrape'
            },
            {
                'name': 'sslproxies.org',
                'url': 'https://www.sslproxies.org/',
                'parser': 'parse_sslproxies'
            }
        ]

    def fetch_proxy_list(self):
        """Eski API - free-proxy-list.net'ten proxy listesini çek"""
        return self.fetch_proxy_list_from_url("https://free-proxy-list.net/")

    def parse_proxies(self, html_content):
        """HTML içeriğini parse ederek proxy listesini çıkar"""
        try:
            print("Proxy tablosu parse ediliyor...")
            logger.info("HTML parse işlemi başlatılıyor...")
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Proxy tablosunu bul - İlk tablo class'larına göre
            table = soup.find('table', {'class': 'table table-striped table-bordered'})
            if not table:
                # Alternatif: ilk tabloyu al
                table = soup.find('table')
                if not table:
                    logger.error("Hiç tablo bulunamadı")
                    print("Hata: Hiç tablo bulunamadı")
                    return []
            
            proxies = []
            
            # Header kontrolü
            headers = [th.text.strip() for th in table.find('tr').find_all(['th', 'td'])]
            logger.info("Tablo başlıkları: %s", headers)
            
            # tbody varsa kullan, yoksa tüm tr'leri al
            tbody = table.find('tbody')
            if tbody:
                rows = tbody.find_all('tr')
            else:
                rows = table.find_all('tr')[1:]  # İlk satır header olduğu için atla
            
            logger.info("Toplam %d proxy bulundu, filtreleme başlatılıyor...", len(rows))
            print(f"{len(rows)} proxy bulundu, sıkı filtreleme uygulanıyor...")
            print("Filtre kriterleri: HTTPS=yes AND Anonymity=['elite proxy', 'anonymous']")
            
            filtered_count = 0
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 7:  # En az 7 sütun olmalı
                    ip = cells[0].text.strip()
                    port = cells[1].text.strip()
                    country_code = cells[2].text.strip()
                    country = cells[3].text.strip()
                    anonymity = cells[4].text.strip()
                    google = cells[5].text.strip().lower()
                    https = cells[6].text.strip().lower()
                    
                    # Sıkı filtreleme kriterleri - Playwright uyumluluğu için
                    if (https == "yes" and 
                        anonymity.lower() in ["elite proxy", "anonymous"]):
                        
                        proxy_url = f"http://{ip}:{port}"
                        proxies.append({
                            'url': proxy_url,
                            'ip': ip,
                            'port': port,
                            'country_code': country_code,
                            'country': country,
                            'anonymity': anonymity,
                            'https': https
                        })
                        filtered_count += 1
                        logger.debug("Proxy eklendi: %s (%s, %s)", 
                                   proxy_url, anonymity, country_code)
            
            logger.info("Filtreleme tamamlandı: %d/%d proxy seçildi", filtered_count, len(rows))
            print(f"Sıkı filtreleme tamamlandı: {filtered_count}/{len(rows)} proxy seçildi")
            return proxies
            
        except Exception as e:
            logger.error("Parse hatası: %s", str(e))
            print(f"Parse hatası: {str(e)}")
            return []

    def test_proxy(self, proxy_info, timeout=None):
        """Tek bir proxy'yi test et - HTTPS uyumluluğu için sıkı test"""
        proxy_url = proxy_info['url']
        test_timeout = timeout or self.timeout  # Yeni timeout sistemi
        
        try:
            proxy_dict = {
                'http': proxy_url,
                'https': proxy_url
            }
            
            # HTTPS test isteği gönder (daha sıkı test)
            response = requests.get(
                self.test_url,  # https://httpbin.org/ip
                proxies=proxy_dict,
                timeout=test_timeout,  # Agresif timeout
                headers={'User-Agent': self.session.headers['User-Agent']},
                verify=False  # SSL sertifika doğrulamasını devre dışı bırak
            )
            
            if response.status_code == 200:
                # Yanıtın geçerli olup olmadığını kontrol et
                try:
                    response_data = response.json()
                    if 'origin' in response_data:
                        # Proxy çalışıyor demektir
                        logger.debug("Proxy çalışıyor: %s (%s)", 
                                   proxy_url, proxy_info.get('country_code', 'N/A'))
                        return proxy_info
                except Exception as parse_error:
                    logger.debug("JSON parse hatası: %s", str(parse_error))
            
            return None
            
        except requests.exceptions.SSLError as e:
            logger.debug("SSL hatası: %s - %s", proxy_url, str(e))
            return None
        except requests.exceptions.ConnectTimeout as e:
            logger.debug("Bağlantı timeout: %s - %s", proxy_url, str(e))
            return None
        except requests.exceptions.ConnectionError as e:
            logger.debug("Bağlantı hatası: %s - %s", proxy_url, str(e))
            return None
        except Exception as e:
            logger.debug("Proxy test başarısız: %s - %s", proxy_url, str(e))
            return None

    def test_proxies_parallel(self, proxies):
        """Proxy'leri paralel olarak test et"""
        if not proxies:
            return []
        
        print(f"{len(proxies)} proxy test ediliyor...")
        logger.info("Paralel proxy testi başlatılıyor (max %d worker)", self.max_workers)
        
        working_proxies = []
        
        # ThreadPoolExecutor ile paralel test
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Tüm proxy'leri test için gönder
            future_to_proxy = {
                executor.submit(self.test_proxy, proxy): proxy 
                for proxy in proxies
            }
            
            completed = 0
            total = len(proxies)
            
            for future in concurrent.futures.as_completed(future_to_proxy):
                completed += 1
                
                # İlerleme göster
                if completed % 10 == 0 or completed == total:
                    print(f"Test ilerleme: {completed}/{total}")
                
                try:
                    result = future.result()
                    if result:
                        working_proxies.append(result)
                        print(f"Çalışan proxy: {result['url']} ({result['country_code']})")
                        
                except Exception as e:
                    logger.debug("Test hatası: %s", str(e))
        
        logger.info("Test tamamlandı: %d/%d proxy çalışıyor", len(working_proxies), len(proxies))
        print(f"Test tamamlandı: {len(working_proxies)}/{len(proxies)} proxy çalışıyor")
        
        return working_proxies

    def save_proxies(self, proxies, filename=PROXY_LIST_FILE):
        """Çalışan proxy'leri dosyaya kaydet"""
        try:
            print(f"Proxy listesi {filename} dosyasına kaydediliyor...")
            logger.info("Proxy listesi %s dosyasına kaydediliyor...", filename)
            
            with open(filename, 'w', encoding='utf-8') as f:
                # Başlık yorumu
                f.write("# Güncel Proxy Listesi\n")
                f.write(f"# Toplam {len(proxies)} çalışan proxy\n")
                f.write(f"# Son güncelleme: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# Format: http://ip:port\n\n")
                
                # Proxy'leri normalize et ve yaz
                for proxy in proxies:
                    proxy_url = proxy['url']
                    
                    # Proxy normalizasyonu: sadece protokol prefix'i yoksa http:// ekle
                    if not ("://" in proxy_url):
                        proxy_url = "http://" + proxy_url
                        logger.debug("Proxy normalize edildi: %s", proxy_url)
                    
                    f.write(f"{proxy_url}\n")
            
            logger.info("%d proxy başarıyla %s dosyasına kaydedildi", len(proxies), filename)
            print(f"{len(proxies)} proxy başarıyla {filename} dosyasına kaydedildi")
            
        except Exception as e:
            logger.error("Dosya kaydetme hatası: %s", str(e))
            print(f"Dosya kaydetme hatası: {str(e)}")

    def update_proxy_list(self, test_proxies=True, output_file=PROXY_LIST_FILE):
        """Ana güncelleme fonksiyonu - Çoklu kaynaklardan proxy çeker"""
        start_time = time.time()
        
        print("Proxy güncelleme işlemi başlatılıyor...")
        print("=" * 50)
        
        # 1. Çoklu kaynaklardan proxy listelerini çek
        all_proxies = self.fetch_from_multiple_sources()
        if not all_proxies:
            print("Hiç proxy bulunamadı")
            return False
        
        # 2. Dublicate'leri temizle
        unique_proxies = []
        seen_proxies = set()
        
        for proxy in all_proxies:
            proxy_key = f"{proxy['ip']}:{proxy['port']}"
            if proxy_key not in seen_proxies:
                unique_proxies.append(proxy)
                seen_proxies.add(proxy_key)
        
        print(f"🔄 Dublicate temizlendi: {len(all_proxies)} -> {len(unique_proxies)} proxy")
        
        # 3. Proxy'leri test et (opsiyonel)
        if test_proxies:
            working_proxies = self.test_proxies_parallel(unique_proxies)
            if not working_proxies:
                print("Hiç çalışan proxy bulunamadı")
                return False
        else:
            print("Proxy testi atlandı")
            working_proxies = unique_proxies
        
        # 4. Dosyaya kaydet
        self.save_proxies(working_proxies, output_file)
        
        # Özet bilgi
        elapsed_time = time.time() - start_time
        success_rate = len(working_proxies) / len(unique_proxies) * 100 if unique_proxies else 0
        
        print("\n" + "=" * 50)
        print("Güncelleme tamamlandı!")
        print(f"Toplam süre: {elapsed_time:.1f} saniye")
        print(f"Sonuç: {len(working_proxies)}/{len(unique_proxies)} çalışan proxy (%.1f%% başarı)" % success_rate)
        
        return True

    def fetch_from_multiple_sources(self):
        """Çoklu kaynaktan proxy çek"""
        all_proxies = []
        
        for source in self.proxy_sources:
            try:
                print(f"Proxy kaynağı: {source['name']} kontrol ediliyor...")
                logger.info("Proxy kaynağı: %s", source['name'])
                
                if source['parser'] == 'parse_free_proxy_list':
                    # Mevcut free-proxy-list.net parser'ı
                    html_content = self.fetch_proxy_list_from_url(source['url'])
                    if html_content:
                        proxies = self.parse_proxies(html_content)
                        all_proxies.extend(proxies)
                        print(f"✅ {source['name']}: {len(proxies)} proxy")
                
                elif source['parser'] == 'parse_proxyscrape':
                    # ProxyScrape API parser'ı
                    proxies = self.parse_proxyscrape(source['url'])
                    all_proxies.extend(proxies)
                    print(f"✅ {source['name']}: {len(proxies)} proxy")
                
                elif source['parser'] == 'parse_sslproxies':
                    # SSLProxies.org parser'ı
                    html_content = self.fetch_proxy_list_from_url(source['url'])
                    if html_content:
                        proxies = self.parse_sslproxies(html_content)
                        all_proxies.extend(proxies)
                        print(f"✅ {source['name']}: {len(proxies)} proxy")
                
            except Exception as e:
                logger.warning("Kaynak hatası %s: %s", source['name'], str(e))
                print(f"❌ {source['name']}: Hata - {str(e)}")
        
        print(f"📊 Toplam {len(all_proxies)} proxy çekildi")
        return all_proxies
    
    def fetch_proxy_list_from_url(self, url):
        """Belirtilen URL'den proxy listesini çek"""
        try:
            logger.info("Proxy listesi çekiliyor: %s", url)
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            logger.info("Sayfa başarıyla indirildi: %s", url)
            return response.text
            
        except requests.exceptions.RequestException as e:
            logger.error("Sayfa indirme hatası (%s): %s", url, str(e))
            return None
        except Exception as e:
            logger.error("Beklenmeyen hata (%s): %s", url, str(e))
            return None
    
    def parse_proxyscrape(self, url):
        """ProxyScrape API'dan proxy listesini parse et"""
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # ProxyScrape plain text formatında döner
            proxy_lines = response.text.strip().split('\n')
            proxies = []
            
            for line in proxy_lines:
                line = line.strip()
                if ':' in line and not line.startswith('#'):
                    try:
                        ip, port = line.split(':')
                        proxy_url = f"http://{ip}:{port}"
                        proxies.append({
                            'url': proxy_url,
                            'ip': ip,
                            'port': port,
                            'country_code': 'Unknown',
                            'country': 'Unknown',
                            'anonymity': 'anonymous',
                            'https': 'yes'
                        })
                    except ValueError:
                        continue
            
            logger.info("ProxyScrape: %d proxy parse edildi", len(proxies))
            return proxies
            
        except Exception as e:
            logger.error("ProxyScrape parse hatası: %s", str(e))
            return []
    
    def parse_sslproxies(self, html_content):
        """SSLProxies.org'dan proxy listesini parse et"""
        try:
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Proxy tablosunu bul
            table = soup.find('table', {'class': 'table table-striped table-bordered'})
            if not table:
                table = soup.find('table')
                if not table:
                    logger.error("SSLProxies tablosu bulunamadı")
                    return []
            
            proxies = []
            rows = table.find('tbody')
            if rows:
                rows = rows.find_all('tr')
            else:
                rows = table.find_all('tr')[1:]  # Header'ı atla
            
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 8:  # SSLProxies format
                    ip = cells[0].text.strip()
                    port = cells[1].text.strip()
                    country_code = cells[2].text.strip()
                    country = cells[3].text.strip()
                    anonymity = cells[4].text.strip()
                    google = cells[5].text.strip().lower()
                    https = cells[6].text.strip().lower()
                    last_checked = cells[7].text.strip()
                    
                    # SSL proxy'ler zaten HTTPS desteklemeli
                    if anonymity.lower() in ["elite proxy", "anonymous"]:
                        proxy_url = f"http://{ip}:{port}"
                        proxies.append({
                            'url': proxy_url,
                            'ip': ip,
                            'port': port,
                            'country_code': country_code,
                            'country': country,
                            'anonymity': anonymity,
                            'https': 'yes'
                        })
                        
            logger.info("SSLProxies: %d proxy parse edildi", len(proxies))
            return proxies
            
        except Exception as e:
            logger.error("SSLProxies parse hatası: %s", str(e))
            return []


def test_proxy_normalization():
    """Proxy normalizasyon işlemini test et"""
    print("Proxy normalizasyon testi başlatılıyor...")
    
    # Test proxy'leri (çeşitli formatlar)
    test_proxies = [
        {'url': 'http://192.168.1.1:8080'},      # Zaten doğru format
        {'url': 'https://proxy.example.com:3128'}, # HTTPS format
        {'url': '203.0.113.5:8080'},             # Prefix yok
        {'url': 'proxy.test.com:9999'},          # Prefix yok
        {'url': 'ftp://invalid.proxy.com:1080'}, # FTP prefix (değişmeyecek)
        {'url': '127.0.0.1:3128'},               # Prefix yok
    ]
    
    # Test sonuçları
    expected_results = [
        'http://192.168.1.1:8080',
        'https://proxy.example.com:3128',
        'http://203.0.113.5:8080',
        'http://proxy.test.com:9999',
        'ftp://invalid.proxy.com:1080',  # FTP olduğu için değişmeyecek
        'http://127.0.0.1:3128',
    ]
    
    print("Test proxy'leri:")
    for i, proxy in enumerate(test_proxies):
        original_url = proxy['url']
        
        # Güncellenmiş normalizasyon işlemi
        if not ("://" in original_url):
            normalized_url = "http://" + original_url
        else:
            normalized_url = original_url
        
        expected = expected_results[i]
        status = "[PASS]" if normalized_url == expected else "[FAIL]"
        
        print(f"  {original_url:30} -> {normalized_url:35} {status}")
        
        if normalized_url != expected:
            print(f"    Beklenen: {expected}")
    
    print("\nNormalizasyon testi tamamlandı!")


def main():
    """Ana fonksiyon"""
    # Komut satırı argümanlarını kontrol et
    test_count = None
    if len(sys.argv) > 1:
        # Normalizasyon testi için özel parametre
        if sys.argv[1].lower() == "test-normalize":
            test_proxy_normalization()
            return 0
        
        try:
            test_count = int(sys.argv[1])
            print(f"Test modu: sadece {test_count} proxy test edilecek")
        except ValueError:
            print("Hata: Test sayısı geçerli bir sayı olmalı")
            print("Kullanım: python update_proxies.py [test_sayısı]")
            print("         python update_proxies.py test-normalize")
            return 1
    
    updater = ProxyUpdater()
    
    try:
        # Test modu için updater'ı modifiye et
        if test_count:
            original_test_method = updater.test_proxies_parallel
            
            def limited_test(proxies):
                return original_test_method(proxies[:test_count])
            
            updater.test_proxies_parallel = limited_test
        
        # Proxy listesini güncelle (test ile)
        success = updater.update_proxy_list(test_proxies=True)
        
        if success:
            print("\nKullanım: load_proxies('proxy_list.txt') ile yükleyebilirsiniz")
        else:
            print("\nGüncelleme başarısız!")
            return 1
        
        return 0
        
    except KeyboardInterrupt:
        print("\nİşlem kullanıcı tarafından durduruldu")
        return 1
    except Exception as e:
        logger.error("Beklenmeyen hata: %s", str(e))
        print(f"\nBeklenmeyen hata: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main()) 