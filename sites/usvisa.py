#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ABD Vize Randevu Kontrolü (ustraveldocs.com)
ABD vize randevularını ustraveldocs.com üzerinden kontrol eder.
ProxyManager entegrasyonu ile optimizasyon.
"""

import requests
import json
import logging
import time
import random
import re
import os
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

class USVisaChecker:
    """ABD vize randevu kontrol işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://www.ustraveldocs.com"
        
        # Gelişmiş anti-bot header sistemi
        self.headers = get_anti_bot_headers(self.base_url, 'tr')
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
        
        # ABD konsolosluk lokasyonları (Türkiye için)
        self.locations = {
            'ankara': {
                'url': 'https://www.ustraveldocs.com/tr/tr-niv-appointmentschedule.asp?embassy=ankara',
                'name': 'ABD Ankara Büyükelçiliği'
            },
            'istanbul': {
                'url': 'https://www.ustraveldocs.com/tr/tr-niv-appointmentschedule.asp?embassy=istanbul',
                'name': 'ABD İstanbul Konsolosluğu'
            },
            'adana': {
                'url': 'https://www.ustraveldocs.com/tr/tr-niv-appointmentschedule.asp?embassy=adana',
                'name': 'ABD Adana Konsolosluğu'
            }
        }
        
        logger.info("US Visa Checker başlatıldı - ProxyManager entegrasyonu ile")
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
            dynamic_headers = get_anti_bot_headers(url, 'tr', referer=self.base_url)
            
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
                # ProxyManager ile blacklist'e ekle
                self.proxy_manager.add_to_blacklist(proxy_url, error_type)
                
                # Local blacklist'e de ekle
                self.blacklisted_proxies.add(proxy_url)
                logger.warning("LOCAL BLACKLIST: Proxy session'dan çıkarıldı: %s", display_proxy)
                
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

    def check(self) -> bool:
        """
        Basit randevu kontrolü yapar
        HTTP requests ile hızlı kontrol yapar

        Returns:
            bool: Randevu varsa True, yoksa False
        """
        try:
            # Ankara konsolosluğunu kontrol et
            result = self.check_availability('ankara')
            
            if result['success'] and result['appointments']:
                logger.info("✅ ABD randevu mevcut: %d randevu bulundu", len(result['appointments']))
                return True
            else:
                logger.info("ℹ️ ABD randevu mevcut değil")
                return False

        except Exception as e:
            logger.error("ABD randevu kontrolünde hata: %s", str(e))
            return False

    def check_appointments(self) -> Optional[str]:
        """ABD vize randevularını kontrol et"""
        try:
            # Türkiye lokasyonları için randevu kontrolü
            locations = {
                'ankara': 25,  # Ankara Konsolosluğu
                'istanbul': 26  # İstanbul Konsolosluğu
            }

            available_appointments = []

            for city, location_id in locations.items():
                logger.info("%s konsolosluğu kontrol ediliyor...", city.title())

                # Randevu API endpoint'i
                url = f"{self.base_url}/tr/niv/schedule/{location_id}/appointment/days/95.json"

                response = self._make_request(url)
                if not response or response.status_code != 200:
                    continue

                try:
                    appointments = response.json()

                    # Uygun randevuları filtrele
                    for appointment in appointments:
                        if appointment.get('date'):
                            date_str = appointment['date']
                            appointment_date = datetime.strptime(date_str, '%Y-%m-%d')

                            # 6 ay içindeki randevuları kabul et
                            if appointment_date <= datetime.now().replace(month=datetime.now().month + 6):
                                available_appointments.append(
                                    f"📍 {city.title()}: {date_str}"
                                )

                except json.JSONDecodeError:
                    logger.error("%s için JSON parse hatası", city)
                    continue

            if available_appointments:
                return "\n".join(available_appointments)

            return None

        except Exception as e:  # Genel hata yakalama - beklenmeyen durumlar için
            logger.error("ABD vize kontrolünde hata: %s", str(e))
            raise

    def get_appointment_times(self, location_id: int, date: str) -> List[str]:
        """Belirli bir tarih için saat dilimlerini getir"""
        try:
            url = f"{self.base_url}/tr/niv/schedule/{location_id}/appointment/times/{date}.json"
            response = self._make_request(url)

            if response and response.status_code == 200:
                times_data = response.json()
                available_times = []

                if 'business_times' in times_data:
                    for time_slot in times_data['business_times']:
                        available_times.append(time_slot)

                return available_times

            return []

        except json.JSONDecodeError as e:  # JSON parse hataları
            logger.error("JSON parse hatası: %s", str(e))
            return []
        except Exception as e:  # Genel hata yakalama - network/timeout hataları vb.
            logger.error("Saat kontrolü hatası: %s", str(e))
            return []

    def _make_request_with_proxy(self, url: str, method: str = 'GET', **kwargs) -> Optional[requests.Response]:
        """
        Proxy ile HTTP request gönder
        
        Args:
            url (str): Hedef URL
            method (str): HTTP method (GET, POST, etc.)
            **kwargs: requests için ek parametreler
            
        Returns:
            Optional[requests.Response]: Response objesi veya None
        """
        if not self.proxies:
            logger.warning("Proxy listesi boş, direkt bağlantı denenecek")
            try:
                response = self.session.request(method, url, timeout=self.proxy_timeout, **kwargs)
                response._proxy_used = 'direct'
                return response
            except Exception as e:
                logger.error("Direkt bağlantı hatası: %s", str(e))
                return None
        
        # Kullanılabilir proxy'leri filtrele
        available_proxies = [p for p in self.proxies if p not in self.blacklisted_proxies]
        
        if not available_proxies:
            logger.error("Kullanılabilir proxy kalmadı")
            return None
        
        # Random proxy seç
        proxy_url = random.choice(available_proxies)
        
        try:
            # Proxy ayarları
            if not proxy_url.startswith('http://'):
                proxy_dict = {'http': f'http://{proxy_url}', 'https': f'http://{proxy_url}'}
            else:
                proxy_dict = {'http': proxy_url, 'https': proxy_url}
            
            # Request gönder
            response = self.session.request(
                method, 
                url, 
                proxies=proxy_dict, 
                timeout=self.proxy_timeout,
                **kwargs
            )
            
            # Başarılı response
            if response.status_code == 200:
                response._proxy_used = proxy_url
                logger.debug("✅ Proxy başarılı: %s", proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url)
                return response
            else:
                logger.warning("Proxy HTTP error %d: %s", response.status_code, proxy_url)
                self._handle_proxy_failure(proxy_url, f"HTTP_{response.status_code}")
                
        except requests.exceptions.ProxyError as e:
            logger.debug("Proxy error: %s", proxy_url)
            self._handle_proxy_failure(proxy_url, "ProxyError")
        except requests.exceptions.Timeout as e:
            logger.debug("Proxy timeout: %s", proxy_url)
            self._handle_proxy_failure(proxy_url, "Timeout")
        except requests.exceptions.ConnectionError as e:
            logger.debug("Proxy connection error: %s", proxy_url)
            self._handle_proxy_failure(proxy_url, "ConnectionError")
        except Exception as e:
            logger.debug("Proxy unknown error: %s - %s", proxy_url, str(e))
            self._handle_proxy_failure(proxy_url, f"UnknownError_{type(e).__name__}")
        
        return None

    def check_availability(self, embassy: str = 'ankara', visa_type: str = 'B1/B2') -> Dict:
        """
        ABD vize randevu müsaitliğini kontrol et
        
        Args:
            embassy (str): Konsolosluk ('ankara', 'istanbul', 'adana')
            visa_type (str): Vize türü (varsayılan: 'B1/B2')
            
        Returns:
            Dict: Randevu bilgileri ve durum
        """
        result = {
            'success': False,
            'embassy': embassy,
            'visa_type': visa_type,
            'appointments': [],
            'earliest_date': None,
            'error': None,
            'proxy_used': None
        }
        
        try:
            # Konsolosluk bilgisini al
            if embassy not in self.locations:
                result['error'] = f'Geçersiz konsolosluk: {embassy}'
                return result
            
            location_info = self.locations[embassy]
            target_url = location_info['url']
            location_name = location_info['name']
            
            logger.info("ABD vize randevu kontrolü başlatıldı: %s", location_name)
            
            # Proxy ile sayfayı al
            response = self._make_request_with_proxy(target_url)
            
            if not response:
                result['error'] = 'Sayfa yüklenemedi'
                return result
            
            # HTML parsing
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Randevu tarihlerini parse et
            appointments = self._parse_appointments(soup, embassy)
            
            if appointments:
                result['success'] = True
                result['appointments'] = appointments
                result['earliest_date'] = min([apt['date'] for apt in appointments])
                logger.info("✅ %s için %d randevu bulundu", location_name, len(appointments))
            else:
                result['error'] = 'Müsait randevu bulunamadı'
                logger.info("ℹ️ %s için müsait randevu yok", location_name)
            
            result['proxy_used'] = getattr(response, '_proxy_used', None)
            
        except Exception as e:
            error_msg = f"Randevu kontrol hatası: {str(e)}"
            result['error'] = error_msg
            logger.error(error_msg)
        
        return result
    
    def _parse_appointments(self, soup: BeautifulSoup, embassy: str) -> List[Dict]:
        """HTML'den randevu tarihlerini parse et"""
        appointments = []
        
        try:
            # Farklı seçicileri dene (site yapısına göre)
            selectors = [
                'input[name="consulate_appointment_date_time_input"]',
                'select[name="appointment_date"] option',
                '.appointment-date',
                '.date-picker option',
                'td.calendar-date'
            ]
            
            for selector in selectors:
                elements = soup.select(selector)
                if elements:
                    logger.debug("Randevu seçici bulundu: %s (%d element)", selector, len(elements))
                    
                    for element in elements:
                        date_text = element.get('value') or element.text.strip()
                        if date_text and self._is_valid_date(date_text):
                            appointments.append({
                                'date': date_text,
                                'embassy': embassy,
                                'source': selector
                            })
                    
                    if appointments:
                        break
            
            # Eğer hiç randevu bulunamadıysa, sayfa içeriğini logla
            if not appointments:
                text_content = soup.get_text()[:500]
                logger.debug("Randevu bulunamadı. Sayfa içeriği: %s", text_content)
                
                # "no appointments" gibi mesajları ara
                no_appointment_indicators = [
                    'no appointments available',
                    'no available dates',
                    'müsait randevu bulunmamaktadır',
                    'randevu mevcut değil'
                ]
                
                for indicator in no_appointment_indicators:
                    if indicator.lower() in text_content.lower():
                        logger.info("Randevu yok mesajı tespit edildi: %s", indicator)
                        break
            
        except Exception as e:
            logger.error("Randevu parsing hatası: %s", str(e))
        
        return appointments
    
    def _is_valid_date(self, date_str: str) -> bool:
        """Tarih string'inin geçerli olup olmadığını kontrol et"""
        try:
            # Çeşitli tarih formatlarını dene
            date_formats = [
                '%Y-%m-%d',
                '%d/%m/%Y',
                '%m/%d/%Y',
                '%d.%m.%Y',
                '%B %d, %Y',
                '%d %B %Y'
            ]
            
            for fmt in date_formats:
                try:
                    datetime.strptime(date_str, fmt)
                    return True
                except ValueError:
                    continue
            
            return False
            
        except Exception:
            return False

    def check_availability_with_browser(self, embassy: str = 'ankara') -> Dict:
        """
        DEVRE DIŞI - Playwright ile randevu kontrolü (artık kullanılmıyor)
        Basit HTTP requests kullanılıyor.
        """
        logger.warning("check_availability_with_browser artık desteklenmiyor. check_availability kullanın.")
        return {'success': False, 'error': 'Bu fonksiyon artık desteklenmiyor'}

# Test fonksiyonu
if __name__ == "__main__":
    import logging
    
    # Logging ayarla
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Test
    checker = USVisaChecker()
    
    print("🇺🇸 ABD Vize Randevu Test")
    print("========================")
    
    # Ankara konsolosluğunu test et
    result = checker.check_availability('ankara')
    
    if result['success']:
        print(f"✅ Başarılı: {len(result['appointments'])} randevu bulundu")
        for apt in result['appointments'][:3]:  # İlk 3 randevuyu göster
            print(f"  📅 {apt['date']} - {apt['embassy']}")
    else:
        print(f"❌ Hata: {result.get('error', 'Bilinmeyen hata')}")
    
    print(f"🔗 Kullanılan proxy: {result.get('proxy_used', 'Yok')}")
    print("Test tamamlandı") 