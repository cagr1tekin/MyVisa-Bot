#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
İtalya Vize Randevu Kontrolü (VFS Global)
İtalya vize randevularını VFS Global sistemi üzerinden kontrol eder.
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

class VFSGlobalChecker:
    """İtalya vize randevu kontrol işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://visa.vfsglobal.com"
        
        # Gelişmiş anti-bot header sistemi (VFS için API headers)
        self.headers = get_anti_bot_headers(self.base_url, 'it')
        self.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        })
        self.session.headers.update(self.headers)
        
        # ProxyManager kullan
        self.proxy_manager = ProxyManager()
        self.proxies = self.proxy_manager.load_valid_proxies()
        
        # Hatalı proxy'leri blacklist'te tut
        self.blacklisted_proxies = set()
        # Başarısız proxy denemelerini takip et
        self.failed_proxy_attempts = {}  # proxy_url: fail_count
        self.max_proxy_failures = 1  # Maksimum başarısızlık sayısı (daha katı)
        # Bağlantı timeout'u (saniye)
        self.proxy_timeout = 3
        
        # Türkiye VFS Global merkezleri
        self.locations = {
            'ankara': {
                'center_id': 'ita_tr_ank',
                'name': 'VFS Global Ankara',
                'url': 'https://visa.vfsglobal.com/tur/tr/ita/'
            },
            'istanbul': {
                'center_id': 'ita_tr_ist',
                'name': 'VFS Global İstanbul',
                'url': 'https://visa.vfsglobal.com/tur/tr/ita/'
            }
        }
        
        logger.info("VFS Global Checker başlatıldı - ProxyManager entegrasyonu ile")
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
            dynamic_headers = get_anti_bot_headers(url, 'it', referer=self.base_url)
            
            # VFS API için özel header'lar
            if '/api/' in url:
                dynamic_headers.update({
                    'Accept': 'application/json, text/plain, */*',
                    'X-Requested-With': 'XMLHttpRequest'
                })
            
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
        Proxy başarısızlıklarını yönet ve gerekirse kalıcı blacklist'e ekle
        
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
                self.blacklisted_proxies.add(proxy_url)
                logger.warning("BLACKLIST: Proxy artık kullanılmayacak: %s (Toplam %d başarısızlık - %s)", 
                             display_proxy, fail_count, error_type)
                
                # Proxy listesinden de çıkar
                if proxy_url in self.proxies:
                    self.proxies.remove(proxy_url)
                    logger.info("REMOVED: Proxy ana listeden çıkarıldı: %s", display_proxy)
                
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
        """İtalya vize randevularını kontrol et"""
        try:
            available_appointments = []

            for city, location_info in self.locations.items():
                logger.info("%s kontrol ediliyor...", location_info['name'])

                # Önce API endpoint'ini dene
                api_appointments = self._check_api_endpoint(city, location_info)
                if api_appointments:
                    available_appointments.extend(api_appointments)
                else:
                    # API başarısız olursa browser kontrolü yap
                    browser_appointments = self._check_with_browser(city, location_info)
                    if browser_appointments:
                        available_appointments.extend(browser_appointments)

            if available_appointments:
                return "\n".join(available_appointments)

            return None

        except Exception as e:
            logger.error("İtalya vize kontrolünde hata: %s", str(e))
            raise

    def _check_api_endpoint(self, city: str, location_info: Dict) -> List[str]:
        """API endpoint ile randevu kontrolü"""
        try:
            # VFS Global API parametreleri (İtalya için)
            api_params = {
                'missionCode': 'ita',  # İtalya
                'centerCode': 'ist' if city == 'istanbul' else 'ank',  # İstanbul/Ankara
                'categoryCode': '1',  # Turizm vizesi
                'languageCode': 'tr'  # Türkçe
            }
            
            # API endpoint URL'si
            api_url = "https://visa.vfsglobal.com/appointment/api/calendar/availableDates"
            
            # API headers - gelişmiş anti-bot sistemi
            api_headers = get_anti_bot_headers(api_url, 'it', referer=location_info['url'])
            api_headers.update({
                'Accept': 'application/json, text/plain, */*',
                'Referer': location_info['url'],
                'X-Requested-With': 'XMLHttpRequest'
            })

            # GET isteği gönder
            response = self._make_request(
                api_url, 
                method='GET',
                params=api_params,
                headers=api_headers
            )

            if not response or response.status_code != 200:
                logger.warning("API isteği başarısız: %s", city)
                return []

            try:
                api_data = response.json()
                appointments = []
                
                # API yanıtında available dates'leri kontrol et
                if isinstance(api_data, list):
                    for date_info in api_data:
                        if isinstance(date_info, dict) and date_info.get('available', False):
                            date_str = date_info.get('date', '')
                            if date_str:
                                appointments.append(f"📍 {location_info['name']} (API): {date_str}")
                
                elif isinstance(api_data, dict):
                    # Alternatif JSON yapısı
                    available_dates = api_data.get('availableDates', [])
                    for date_str in available_dates:
                        appointments.append(f"📍 {location_info['name']} (API): {date_str}")

                if appointments:
                    logger.info("API ile %d randevu bulundu: %s", len(appointments), city)
                
                return appointments

            except json.JSONDecodeError as e:
                logger.error("API JSON parse hatası (%s): %s", city, str(e))
                return []

        except Exception as e:
            logger.error("API endpoint hatası (%s): %s", city, str(e))
            return []

    def _check_with_browser(self, city: str, location_info: Dict) -> List[str]:
        """Browser ile JavaScript kontrolü"""
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
                    args=['--no-sandbox', '--disable-dev-shm-usage'],
                    proxy=proxy_config
                )

                # Context oluştur
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    locale='tr-TR',
                    ignore_https_errors=True
                )

                page = context.new_page()
                page.set_default_timeout(30000)

                # VFS Global sayfasına git
                logger.info("VFS Global sayfası yükleniyor: %s", location_info['url'])
                response = page.goto(location_info['url'], wait_until='networkidle')

                if not response or response.status != 200:
                    logger.error("Sayfa yüklenemedi (%s): %d", city, response.status if response else 0)
                    browser.close()
                    return []

                # Sayfa yüklenmesini bekle
                time.sleep(random.uniform(3, 6))

                # JavaScript ile randevu kontrolü
                try:
                    appointment_check = page.evaluate("""() => {
                        const bodyText = document.body.innerText.toLowerCase();
                        
                        // Türkçe randevu mevcut ifadeleri
                        const appointmentAvailable = [
                            'randevu alınabilir',
                            'randevu mevcut',
                            'müsait randevu',
                            'uygun randevu',
                            'appointment available',
                            'available appointment'
                        ];
                        
                        // Randevu yok ifadeleri
                        const noAppointment = [
                            'randevu yok',
                            'hiç randevu yok',
                            'müsait randevu yok',
                            'no appointments available',
                            'no slots available'
                        ];
                        
                        // Önce randevu var mı kontrol et
                        for (const phrase of appointmentAvailable) {
                            if (bodyText.includes(phrase)) {
                                return true; // Randevu var
                            }
                        }
                        
                        // Sonra randevu yok mu kontrol et
                        for (const phrase of noAppointment) {
                            if (bodyText.includes(phrase)) {
                                return false; // Randevu yok
                            }
                        }
                        
                        // Belirsiz durum - calendar elementi var mı?
                        const calendarExists = document.querySelector('.calendar') || 
                                             document.querySelector('[class*="calendar"]') ||
                                             document.querySelector('[class*="appointment"]');
                        
                        return calendarExists ? true : false;
                    }""")

                    logger.info("JavaScript kontrolü (%s): %s", city, appointment_check)

                    browser.close()

                    if appointment_check:
                        return [f"📍 {location_info['name']} (Browser): Randevu mevcut olabilir"]
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
    
    def _get_appointment_slots(self, center_id: str, date: str) -> List[str]:
        """Belirli bir tarih için saat dilimlerini getir"""
        try:
            slots_url = f"{self.base_url}/api/appointment/slots"
            
            payload = {
                'centerId': center_id,
                'date': date,
                'categoryId': 'ITALY_TOURISM',
                'subCategoryId': 'TOURISM_INDIVIDUAL'
            }
            
            response = self._make_request(slots_url, method='POST', json=payload)
            
            if response and response.status_code == 200:
                slots_data = response.json()
                
                available_times = []
                if 'data' in slots_data and 'slots' in slots_data['data']:
                    for slot in slots_data['data']['slots']:
                        if slot.get('available', False):
                            time_str = slot.get('time')
                            if time_str:
                                available_times.append(time_str)
                
                return available_times
            
            return []
            
        except json.JSONDecodeError as e:  # JSON parse hataları
            logger.error("JSON parse hatası: %s", str(e))
            return []
        except (KeyError, TypeError) as e:  # API yanıt format hataları
            logger.error("API slot format hatası: %s", str(e))
            return []
        except Exception as e:  # Genel hata yakalama - network/timeout hataları vb.
            logger.error("Saat kontrolü hatası: %s", str(e))
            return []
    
    def _check_visa_types(self) -> Dict[str, str]:
        """Mevcut vize türlerini listele"""
        visa_types = {
            'ITALY_TOURISM': 'Turizm Vizesi',
            'ITALY_BUSINESS': 'İş Vizesi',
            'ITALY_FAMILY': 'Aile Birleşimi',
            'ITALY_STUDY': 'Öğrenci Vizesi',
            'ITALY_TRANSIT': 'Transit Vize'
        }
        return visa_types 
