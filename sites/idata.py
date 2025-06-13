#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Almanya Vize Randevu Kontrolü (iDATA)
Almanya vize randevularını iDATA sistemi üzerinden kontrol eder.
ProxyManager entegrasyonu ile optimizasyon.
"""

import requests
import json
import logging
import time
import random
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# ProxyManager import et
import sys
sys.path.append('.')
from proxy_manager import ProxyManager
from config.browser_headers import BrowserHeaders, get_anti_bot_headers

logger = logging.getLogger(__name__)


class IdataChecker:
    """Almanya vize randevu kontrol işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://service2.diplo.de"
        
        # Gelişmiş anti-bot header sistemi
        self.headers = get_anti_bot_headers(self.base_url, 'de')
        self.session.headers.update(self.headers)

        # ProxyManager'ı başlat ve entegre et
        self.proxy_manager = ProxyManager()
        self.proxies = self.proxy_manager.load_valid_proxies()
        self.timeout = 3  # Agresif timeout (7'den 3'e)
        
        # Hatalı proxy'leri blacklist'te tut
        self.blacklisted_proxies = set()
        # Başarısız proxy denemelerini takip et
        self.failed_proxy_attempts = {}  # proxy_url: fail_count
        self.max_proxy_failures = 1  # Maksimum başarısızlık sayısı (daha katı)
        # Bağlantı timeout'u (saniye)
        self.proxy_timeout = 3
        
        # Türkiye konsolosluk bilgileri
        self.locations = {
            'ankara': {
                'url': ('https://service2.diplo.de/rktermin/extern/appointment_showForm.do'
                       '?locationCode=anka&realmId=108&categoryId=1600'),
                'name': 'Ankara Büyükelçiliği'
            },
            'istanbul': {
                'url': ('https://service2.diplo.de/rktermin/extern/appointment_showForm.do'
                       '?locationCode=ista&realmId=108&categoryId=1600'),
                'name': 'İstanbul Başkonsolosluğu'
            }
        }
        
        logger.info("iDATA Checker başlatıldı - ProxyManager entegrasyonu ile")
        logger.info("Geçerli proxy sayısı: %d", len(self.proxies))

    def _get_random_proxy(self) -> Optional[Dict]:
        """
        Requests için proxy dict formatında döndür - ProxyManager'dan çek
        """
        # Proxy'leri ProxyManager'dan yenile
        self.proxies = self.proxy_manager.load_valid_proxies()
        
        if not self.proxies:
            logger.warning("ProxyManager'dan hiç geçerli proxy alınamadı")
            return None

        # Blacklist'te olmayan proxy'ler arasından seç
        available_proxies = [p for p in self.proxies if p not in self.blacklisted_proxies]
        
        if not available_proxies:
            logger.warning("Tüm proxy'ler blacklist'te, proxy olmadan devam ediliyor")
            return None

        proxy_url = random.choice(available_proxies)

        try:
            # Proxy URL'sinin geçerli olduğunu son kez kontrol et
            parsed = urlparse(proxy_url)
            if not (parsed.hostname and parsed.port):
                logger.warning("_get_random_proxy: Geçersiz proxy URL")
                self.blacklisted_proxies.add(proxy_url)
                return None
            
            logger.debug("Seçilen proxy: %s", proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url)
            
            return {
                'http': proxy_url,
                'https': proxy_url
            }
            
        except Exception as e:
            logger.warning("Proxy hazırlama hatası: %s", str(e))
            self.blacklisted_proxies.add(proxy_url)
            return None

    def _make_request(self, url: str, method: str = 'GET', **kwargs) -> Optional[requests.Response]:
        """Proxy ile güvenli istek gönder - performans tabanlı blacklisting ile"""
        proxy = self._get_random_proxy()
        
        try:
            # Her istek için yeni anti-bot header'lar al
            dynamic_headers = get_anti_bot_headers(url, 'de', referer=self.base_url)
            
            # Mevcut header'ları güncelle
            combined_headers = {**self.headers, **dynamic_headers}
            if 'headers' in kwargs:
                combined_headers.update(kwargs['headers'])
            kwargs['headers'] = combined_headers
            
            # Performans ölçümü başlat
            start_time = time.time()
            
            # Timeout'u kwargs'ta yoksa ekle
            if 'timeout' not in kwargs:
                kwargs['timeout'] = self.proxy_timeout
            
            if method.upper() == 'GET':
                response = self.session.get(url, proxies=proxy, **kwargs)
            else:
                response = self.session.post(url, proxies=proxy, **kwargs)

            # Performans ölçümü bitir
            delay = time.time() - start_time
            
            # Yavaş proxy kontrolü (2.0 saniyeden fazla)
            if delay > 2.0:
                if proxy and 'http' in proxy:
                    proxy_url = proxy['http']
                    logger.warning("YAVAŞ PROXY: %s (%.2f saniye) - blacklist'e ekleniyor", 
                                 proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url, delay)
                    self._handle_proxy_failure(proxy_url, f"SlowResponse ({delay:.2f}s)")
            else:
                # Hızlı ve başarılı istek - proxy'yi başarılı listesinden çıkar
                if proxy and 'http' in proxy:
                    proxy_url = proxy['http']
                    if proxy_url in self.failed_proxy_attempts:
                        del self.failed_proxy_attempts[proxy_url]
                        logger.debug("Proxy başarılı ve hızlı: %s (%.2f saniye)", 
                                   proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url, delay)
            
            # Rate limiting için bekle
            time.sleep(random.uniform(4, 8))
            return response
            
        except requests.exceptions.ProxyError as e:
            logger.error("Proxy hatası: %s", str(e))
            if proxy and 'http' in proxy:
                self._handle_proxy_failure(proxy['http'], "ProxyError")
            return None
        except requests.exceptions.SSLError as e:
            logger.error("SSL protokol hatası: %s", str(e))
            if proxy and 'http' in proxy:
                self._handle_proxy_failure(proxy['http'], "SSLError")
            return None
        except requests.exceptions.ConnectionError as e:
            error_msg = str(e).lower()
            if "getaddrinfo failed" in error_msg:
                logger.error("DNS çözümleme hatası (getaddrinfo failed): %s", str(e))
                if proxy and 'http' in proxy:
                    self._handle_proxy_failure(proxy['http'], "getaddrinfo failed")
            elif "unable to connect to proxy" in error_msg:
                logger.error("Proxy bağlantı hatası (unable to connect): %s", str(e))
                if proxy and 'http' in proxy:
                    self._handle_proxy_failure(proxy['http'], "unable to connect")
            elif "httpsconnectionpool" in error_msg:
                logger.error("HTTPS bağlantı havuz hatası: %s", str(e))
                if proxy and 'http' in proxy:
                    self._handle_proxy_failure(proxy['http'], "HTTPSConnectionPool")
            else:
                logger.error("Bağlantı hatası: %s", str(e))
                if proxy and 'http' in proxy:
                    self._handle_proxy_failure(proxy['http'], "ConnectionError")
            return None
        except requests.exceptions.Timeout as e:
            logger.error("Proxy timeout hatası (%ds): %s", self.proxy_timeout, str(e))
            if proxy and 'http' in proxy:
                self._handle_proxy_failure(proxy['http'], "Timeout")
            return None
        except requests.exceptions.RequestException as e:
            logger.error("HTTP istek hatası: %s", str(e))
            if proxy and 'http' in proxy:
                self._handle_proxy_failure(proxy['http'], "RequestException")
            return None
        except Exception as e:
            logger.error("İstek hatası: %s", str(e))
            if proxy and 'http' in proxy:
                self._handle_proxy_failure(proxy['http'], "Unknown")
            return None

    def _handle_proxy_failure(self, proxy_url: str, error_type: str):
        """
        Proxy başarısızlıklarını yönet ve ProxyManager blacklist'e ekle
        
        Args:
            proxy_url (str): Başarısız proxy URL'si
            error_type (str): Hata türü
        """
        try:
            # Başarısızlık sayısını artır
            self.failed_proxy_attempts[proxy_url] = self.failed_proxy_attempts.get(proxy_url, 0) + 1
            fail_count = self.failed_proxy_attempts[proxy_url]
            
            display_proxy = proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url
            logger.warning("Proxy başarısızlık kaydedildi: %s (Hata: %s, Sayı: %d/%d)", 
                         display_proxy, error_type, fail_count, self.max_proxy_failures)
            
            # Maksimum başarısızlık sayısına ulaştıysa kalıcı blacklist'e ekle
            if fail_count >= self.max_proxy_failures:
                # Local blacklist'e ekle
                self.blacklisted_proxies.add(proxy_url)
                
                # ProxyManager'ın global blacklist'ine de ekle
                reason = f"iDATA-{error_type}-{fail_count}x"
                success = self.proxy_manager.add_to_blacklist(proxy_url, reason)
                
                if success:
                    logger.warning("GLOBAL BLACKLIST: %s (Sebep: %s)", display_proxy, reason)
                else:
                    logger.warning("LOCAL BLACKLIST: %s (Global eklenemedi)", display_proxy)
                
                # Proxy listesinden de çıkar
                if proxy_url in self.proxies:
                    self.proxies.remove(proxy_url)
                    logger.debug("Proxy ana listeden çıkarıldı: %s", display_proxy)
                
                # Başarısızlık sayacını temizle
                if proxy_url in self.failed_proxy_attempts:
                    del self.failed_proxy_attempts[proxy_url]
            
        except Exception as e:
            logger.error("Proxy başarısızlık yönetim hatası: %s", str(e))

    def get_proxy_stats(self) -> Dict[str, int]:
        """
        Proxy istatistiklerini döndür (test/debug amaçlı)
        
        Returns:
            dict: Proxy istatistikleri
        """
        return {
            'total_proxies': len(self.proxies),
            'blacklisted_proxies': len(self.blacklisted_proxies),
            'failed_attempts': len(self.failed_proxy_attempts),
            'available_proxies': len([p for p in self.proxies if p not in self.blacklisted_proxies])
        }

    def check_appointments(self) -> Optional[str]:
        """Almanya vize randevularını kontrol et"""
        try:
            available_appointments = []

            for city, location_info in self.locations.items():
                logger.info("%s kontrol ediliyor...", location_info['name'])

                # Önce HTTP request kontrolü dene
                http_appointments = self._check_with_requests(city, location_info)
                if http_appointments:
                    available_appointments.extend(http_appointments)
                else:
                    # HTTP başarısız olursa browser kontrolü yap
                    browser_appointments = self._check_with_browser(city, location_info)
                    if browser_appointments:
                        available_appointments.extend(browser_appointments)

            if available_appointments:
                return "\n".join(available_appointments)

            return None

        except Exception as e:
            logger.error("Almanya vize kontrolünde hata: %s", str(e))
            raise

    def _check_with_requests(self, city: str, location_info: Dict) -> List[str]:
        """HTTP requests ile kontrol (mevcut sistem)"""
        try:
            response = self._make_request(location_info['url'])
            if not response or response.status_code != 200:
                logger.warning("HTTP isteği başarısız: %s", city)
                return []

            # HTML içeriğini parse et
            soup = BeautifulSoup(response.content, 'html.parser')

            # Takvim verilerini parse et
            calendar_data = self._parse_calendar(soup)

            appointments = []
            if calendar_data:
                for date, available in calendar_data.items():
                    if available:
                        appointments.append(f"📍 {location_info['name']} (HTTP): {date}")

            if appointments:
                logger.info("HTTP ile %d randevu bulundu: %s", len(appointments), city)

            return appointments

        except Exception as e:
            logger.error("HTTP requests hatası (%s): %s", city, str(e))
            return []

    def _check_with_browser(self, city: str, location_info: Dict) -> List[str]:
        """Playwright ile dinamik JavaScript kontrolü"""
        try:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                # Proxy ayarları
                proxy_url = self._get_random_proxy_url()
                proxy_config = None
                if proxy_url:
                    proxy_config = {"server": proxy_url}
                    logger.info("Browser proxy: %s", proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url)

                # Browser başlat
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox', 
                        '--disable-dev-shm-usage',
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor'
                    ],
                    proxy=proxy_config
                )

                # Context oluştur
                context = browser.new_context(
                    user_agent=BrowserHeaders.USER_AGENTS[0],  # İlk user-agent'ı kullan
                    locale='de-DE',  # Almanya lokali
                    ignore_https_errors=True,
                    extra_http_headers=BrowserHeaders.get_playwright_headers(location_info['url'], 'de')
                )

                page = context.new_page()
                page.set_default_timeout(45000)  # 45 saniye timeout

                # iDATA sayfasına git
                logger.info("iDATA sayfası yükleniyor: %s", location_info['url'])
                response = page.goto(location_info['url'], wait_until='networkidle')

                if not response or response.status != 200:
                    logger.error("Sayfa yüklenemedi (%s): %d", city, response.status if response else 0)
                    browser.close()
                    return []

                # Sayfa yüklenmesini bekle ve dinamik içeriği bekle
                time.sleep(random.uniform(5, 8))

                # JavaScript ile iframe ve dinamik içerik kontrolü
                try:
                    appointment_check = page.evaluate("""() => {
                        const bodyText = document.body.innerText.toLowerCase();
                        
                        // Iframe kontrolü - iDATA çok iframe kullanır
                        const hasIframe = document.querySelector('iframe') !== null;
                        
                        // Randevu sistemi ifadeleri (Türkçe/Almanca/İngilizce)
                        const appointmentSystems = [
                            'randevu',
                            'appointment', 
                            'termin',
                            'calendar',
                            'tarih',
                            'datum',
                            'verfügbar',
                            'available',
                            'müsait'
                        ];
                        
                        // Form elementleri kontrolü
                        const hasForm = document.querySelector('form') !== null ||
                                       document.querySelector('input[type="date"]') !== null ||
                                       document.querySelector('select') !== null ||
                                       document.querySelector('.calendar') !== null ||
                                       document.querySelector('[class*="calendar"]') !== null ||
                                       document.querySelector('[class*="appointment"]') !== null ||
                                       document.querySelector('[class*="termin"]') !== null;
                        
                        // iDATA spesifik elementler
                        const hasIdataElements = document.querySelector('[class*="idata"]') !== null ||
                                               document.querySelector('[id*="idata"]') !== null ||
                                               document.querySelector('.appointment-form') !== null ||
                                               document.querySelector('.booking-form') !== null;
                        
                        // Randevu sistemi metni kontrolü
                        let hasAppointmentText = false;
                        for (const term of appointmentSystems) {
                            if (bodyText.includes(term)) {
                                hasAppointmentText = true;
                                break;
                            }
                        }
                        
                        // Randevu yokluğu ifadeleri
                        const noAppointmentPhrases = [
                            'keine termine verfügbar',
                            'no appointments available',
                            'randevu yok',
                            'hiç randevu yok',
                            'keine termine',
                            'ausgebucht',
                            'fully booked'
                        ];
                        
                        let hasNoAppointment = false;
                        for (const phrase of noAppointmentPhrases) {
                            if (bodyText.includes(phrase)) {
                                hasNoAppointment = true;
                                break;
                            }
                        }
                        
                        // Sonuç hesaplama
                        if (hasNoAppointment) {
                            return false; // Açıkça randevu yok
                        }
                        
                        // Iframe VAR veya randevu sistemi elementleri VAR
                        if (hasIframe || hasForm || hasIdataElements || hasAppointmentText) {
                            return true; // Randevu sistemi mevcut
                        }
                        
                        return false; // Belirsiz/randevu sistemi yok
                    }""")

                    logger.info("JavaScript kontrolü (%s): %s", city, appointment_check)

                    # Ek kontrol: iframe içeriği varsa detaylı bak
                    iframe_content = None
                    try:
                        iframe_content = page.evaluate("""() => {
                            const iframes = document.querySelectorAll('iframe');
                            let content = '';
                            
                            for (let iframe of iframes) {
                                try {
                                    // iframe source URL'ini kontrol et
                                    if (iframe.src) {
                                        content += iframe.src + ' ';
                                    }
                                    
                                    // iframe'in boyutlarını kontrol et (gizli değilse)
                                    const rect = iframe.getBoundingClientRect();
                                    if (rect.width > 100 && rect.height > 100) {
                                        content += 'visible-iframe ';
                                    }
                                } catch (e) {
                                    // Cross-origin iframe access hatası
                                }
                            }
                            
                            return content;
                        }""")
                        
                        if iframe_content:
                            logger.info("Iframe içeriği (%s): %s", city, iframe_content[:100])
                            
                    except Exception as iframe_error:
                        logger.debug("Iframe kontrol hatası (%s): %s", city, str(iframe_error))

                    browser.close()

                    if appointment_check:
                        return [f"📍 {location_info['name']} (Browser): Randevu sistemi mevcut"]
                    else:
                        return []

                except Exception as js_error:
                    logger.warning("JavaScript evaluation hatası (%s): %s", city, str(js_error))
                    browser.close()
                    return []

        except Exception as e:
            logger.error("Browser kontrolü hatası (%s): %s", city, str(e))
            return []

    def _get_random_proxy_url(self) -> Optional[str]:
        """Random proxy URL döndür"""
        if not self.proxies:
            return None

        available_proxies = [p for p in self.proxies if p not in self.blacklisted_proxies]
        if not available_proxies:
            return None

        return random.choice(available_proxies)

    def _parse_calendar(self, soup: BeautifulSoup) -> Dict[str, bool]:
        """Takvim verilerini parse et"""
        calendar_data = {}

        try:
            # Takvim hücrelerini bul
            calendar_cells = soup.find_all('td', class_=['nat-calendar-day'])

            for cell in calendar_cells:
                date_text = cell.get_text(strip=True)
                if date_text.isdigit():
                    # Randevu durumunu kontrol et
                    is_available = 'nat-calendar-day-available' in cell.get('class', [])

                    # Tam tarihi oluştur
                    day = int(date_text)
                    current_date = datetime.now()

                    # Ay bilgisini takvim başlığından al
                    month_year = soup.find('span', class_='nat-calendar-month-year')
                    if month_year:
                        # Tarih formatını oluştur
                        date_str = f"{current_date.year}-{current_date.month:02d}-{day:02d}"
                        calendar_data[date_str] = is_available

            return calendar_data

        except (ValueError, AttributeError) as e:  # HTML parse ve tarih hataları
            logger.error("HTML parse hatası: %s", str(e))
            return {}
        except Exception as e:  # Genel hata yakalama - beklenmeyen HTML yapısı vb.
            logger.error("Takvim parse hatası: %s", str(e))
            return {}

    def _check_appointment_slots(self, location_url: str, date: str) -> List[str]:
        """Belirli bir tarih için saat dilimlerini kontrol et"""
        try:
            # Saat dilimleri için ayrı istek gerekebilir
            slots_url = f"{location_url}&selectedDate={date}"
            response = self._make_request(slots_url)

            if response and response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                time_slots = soup.find_all('option', value=True)

                available_times = []
                for slot in time_slots:
                    if slot.get('value') and slot.get('value') != '':
                        available_times.append(slot.get_text(strip=True))

                return available_times

            return []

        except AttributeError as e:  # HTML parse hataları
            logger.error("HTML slot parse hatası: %s", str(e))
            return []
        except Exception as e:  # Genel hata yakalama - network/timeout hataları vb.
            logger.error("Saat kontrolü hatası: %s", str(e))
            return [] 