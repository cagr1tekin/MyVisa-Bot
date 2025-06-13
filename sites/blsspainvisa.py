#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
İspanya Vize Randevu Kontrolü (BLS Spain Visa)
İspanya vize randevularını BLS sistemi üzerinden kontrol eder.
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

# Path helper import et
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.paths import PROXY_LIST_FILE
from config.browser_headers import BrowserHeaders, get_anti_bot_headers

logger = logging.getLogger(__name__)

class BLSSpainChecker:
    """İspanya vize randevu kontrol işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://blsspainvisa.com"
        
        # Gelişmiş anti-bot header sistemi
        self.headers = get_anti_bot_headers(self.base_url, 'es')
        self.session.headers.update(self.headers)
        
        # Proxy dosyasından proxy listesini yükle
        self.proxies = self._load_proxies()
        # Hatalı proxy'leri blacklist'te tut
        self.blacklisted_proxies = set()
        # Başarısız proxy denemelerini takip et
        self.failed_proxy_attempts = {}  # proxy_url: fail_count
        self.max_proxy_failures = 1  # Maksimum başarısızlık sayısı (daha katı)
        # Bağlantı timeout'u (saniye)
        self.proxy_timeout = 3  # 7'den 3'e düşürüldü (agresif)
        
        # Türkiye BLS Spain Visa merkezleri
        self.locations = {
            'ankara': {
                'url': 'https://turkey.blsspainvisa.com/ankara/english/',
                'name': 'BLS İspanya Vize Merkezi Ankara'
            },
            'istanbul': {
                'url': 'https://turkey.blsspainvisa.com/istanbul/english/',
                'name': 'BLS İspanya Vize Merkezi İstanbul'
            }
        }
    
    def _normalize_proxy_url(self, proxy_line: str) -> Optional[str]:
        """
        Proxy URL'sini normalize eder ve validasyon yapar.
        
        Args:
            proxy_line (str): Ham proxy satırı
            
        Returns:
            str: Normalize edilmiş proxy URL'si veya None (hatalı ise)
        """
        try:
            proxy = proxy_line.strip()
            
            # Boş satır kontrolü
            if not proxy:
                return None
            
            # URL scheme'i kontrol et
            if not proxy.startswith(('http://', 'https://')):
                # Scheme yoksa http:// ekle
                proxy = f"http://{proxy}"
            
            # URL'yi parse et ve validate et
            try:
                parsed = urlparse(proxy)
                
                # Hostname ve port kontrolü
                if not parsed.hostname:
                    logger.warning("Hatalı proxy hostname: %s", proxy_line[:50])
                    return None
                
                if not parsed.port:
                    logger.warning("Hatalı proxy port: %s", proxy_line[:50])
                    return None
                
                # Port sayı kontrolü
                if not (1 <= parsed.port <= 65535):
                    logger.warning("Geçersiz port numarası: %s", proxy_line[:50])
                    return None
                
                # IP adresi regex kontrolü (opsiyonel)
                ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
                if re.match(ip_pattern, parsed.hostname):
                    # IP adresi formatında ise her oktet 0-255 arası olmalı
                    octets = parsed.hostname.split('.')
                    for octet in octets:
                        if not (0 <= int(octet) <= 255):
                            logger.warning("Geçersiz IP adresi: %s", proxy_line[:50])
                            return None
                
                # Normalize edilmiş URL'yi yeniden oluştur
                if parsed.username and parsed.password:
                    normalized_proxy = f"{parsed.scheme}://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
                else:
                    normalized_proxy = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
                
                return normalized_proxy
                
            except ValueError as e:
                logger.warning("URL parse hatası: %s - %s", proxy_line[:50], str(e))
                return None
            
        except Exception as e:
            logger.warning("Proxy normalize hatası: %s - %s", proxy_line[:50], str(e))
            return None
    
    def _load_proxies(self) -> List[str]:
        """
        proxy_list.txt dosyasından proxy listesini yükle ve normalize et
        """
        try:
            with open(PROXY_LIST_FILE, 'r', encoding='utf-8') as f:
                proxies = []
                total_lines = 0
                skipped_lines = 0
                
                for line_num, line in enumerate(f, 1):
                    total_lines += 1
                    line = line.strip()
                    
                    # Boş satırları ve comment satırlarını atla
                    if not line or line.startswith('#'):
                        skipped_lines += 1
                        continue
                    
                    # Proxy'yi normalize et
                    normalized_proxy = self._normalize_proxy_url(line)
                    
                    if normalized_proxy:
                        proxies.append(normalized_proxy)
                        logger.debug("Satır %d: Proxy eklendi: %s", line_num, 
                                   normalized_proxy.split('@')[0] + '@***' if '@' in normalized_proxy else normalized_proxy)
                    else:
                        skipped_lines += 1
                        logger.warning("Satır %d: Hatalı proxy atlandı: %s", line_num, line[:50])
                        # Hatalı proxy'yi blacklist'e ekle
                        self.blacklisted_proxies.add(line.strip())
                        
            logger.info("%d/%d proxy başarıyla yüklendi (%d hatalı proxy atlandı)", 
                       len(proxies), total_lines, skipped_lines)
            return proxies
            
        except FileNotFoundError:
            logger.warning("proxy_list.txt dosyası bulunamadı, proxy kullanılmayacak")
            return []
        except Exception as e:
            logger.error("Proxy dosyası okuma hatası: %s", str(e))
            return []
    
    def _get_random_proxy(self) -> Optional[Dict]:
        """
        Requests için proxy dict formatında döndür
        """
        if not self.proxies:
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
            
            logger.info("Seçilen proxy: %s", proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url)
            
            return {
                'http': proxy_url,
                'https': proxy_url
            }
            
        except Exception as e:
            logger.warning("Proxy dict oluşturma hatası: %s", str(e))
            self.blacklisted_proxies.add(proxy_url)
            return None
    
    def _make_request(self, url: str, method: str = 'GET', **kwargs) -> Optional[requests.Response]:
        """Proxy ile güvenli istek gönder - Gelişmiş anti-bot header'larla"""
        proxy = self._get_random_proxy()
        
        try:
            # Her istek için yeni anti-bot header'lar al
            dynamic_headers = get_anti_bot_headers(url, 'es', referer=self.base_url)
            
            # Mevcut header'ları güncelle
            combined_headers = {**self.headers, **dynamic_headers}
            if 'headers' in kwargs:
                combined_headers.update(kwargs['headers'])
            kwargs['headers'] = combined_headers
            
            # Timeout'u kwargs'ta yoksa ekle
            if 'timeout' not in kwargs:
                kwargs['timeout'] = self.proxy_timeout
            
            # SSL doğrulamasını BLS için kapat
            kwargs['verify'] = False
            
            if method.upper() == 'GET':
                response = self.session.get(url, proxies=proxy, **kwargs)
            else:
                response = self.session.post(url, proxies=proxy, **kwargs)

            # Başarılı istek - proxy'yi başarılı listesinden çıkar
            if proxy and 'http' in proxy:
                proxy_url = proxy['http']
                if proxy_url in self.failed_proxy_attempts:
                    del self.failed_proxy_attempts[proxy_url]
                    logger.debug("Proxy başarılı oldu, fail counter sıfırlandı: %s", 
                               proxy_url.split('@')[0] + '@***' if '@' in proxy_url else proxy_url)
            
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
        """İspanya vize randevularını kontrol et"""
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
            logger.error("İspanya vize kontrolünde hata: %s", str(e))
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

            # Sayfa metnini kontrol et
            page_text = soup.get_text().lower()
            
            # Randevu yokluğu ifadeleri
            no_appointment_phrases = [
                'no appointments available',
                'randevu yok',
                'hiç randevu yok',
                'müsait randevu yok',
                'appointment not available',
                'no slots available',
                'fully booked',
                'no available dates'
            ]

            # Randevu var ifadeleri
            appointment_available_phrases = [
                'book appointment',
                'randevu al',
                'appointment available',
                'available dates',
                'select date',
                'tarih seçin'
            ]

            appointments = []
            
            # Önce randevu var mı kontrol et
            has_appointment_available = any(phrase in page_text for phrase in appointment_available_phrases)
            has_no_appointment = any(phrase in page_text for phrase in no_appointment_phrases)

            if has_appointment_available and not has_no_appointment:
                appointments.append(f"📍 {location_info['name']} (HTTP): Randevu mevcut olabilir")
            elif not has_no_appointment:
                # Belirsiz durum - form elementleri var mı kontrol et
                if soup.find('form') or soup.find('input', {'type': 'date'}) or soup.find('select'):
                    appointments.append(f"📍 {location_info['name']} (HTTP): Randevu sistemi mevcut")

            if appointments:
                logger.info("HTTP ile randevu bulundu: %s", city)

            return appointments

        except Exception as e:
            logger.error("HTTP requests hatası (%s): %s", city, str(e))
            return []

    def _check_with_browser(self, city: str, location_info: Dict) -> List[str]:
        """Playwright ile JavaScript kontrolü"""
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
                    user_agent=BrowserHeaders.USER_AGENTS[0],  # İlk user-agent'ı kullan
                    locale='es-ES',  # İspanya lokali
                    ignore_https_errors=True,
                    extra_http_headers=BrowserHeaders.get_playwright_headers(location_info['url'], 'es')
                )

                page = context.new_page()
                page.set_default_timeout(30000)

                # BLS Spain Visa sayfasına git
                logger.info("BLS Spain Visa sayfası yükleniyor: %s", location_info['url'])
                response = page.goto(location_info['url'], wait_until='networkidle')

                if not response or response.status != 200:
                    logger.error("Sayfa yüklenemedi (%s): %d", city, response.status if response else 0)
                    browser.close()
                    return []

                # Sayfa yüklenmesini bekle
                time.sleep(random.uniform(3, 6))

                # JavaScript ile randevu kontrolü - gelişmiş kontroller
                try:
                    appointment_check = page.evaluate("""() => {
                        const bodyText = document.body.innerText.toLowerCase();
                        const titleText = document.title.toLowerCase();
                        
                        // BLS Spain spesifik kontroller
                        const blsSpainChecks = {
                            hasBlsSpain: bodyText.includes('bls spain') || titleText.includes('bls spain'),
                            hasSpainVisa: bodyText.includes('spain visa') || bodyText.includes('visado españa'),
                            hasSchengenVisa: bodyText.includes('schengen visa') || bodyText.includes('schengen'),
                            hasTourismVisa: bodyText.includes('tourism visa') || bodyText.includes('turismo'),
                            hasAppointmentBooking: bodyText.includes('appointment booking') || bodyText.includes('cita previa')
                        };
                        
                        // Randevu yokluğu ifadeleri (İngilizce/Türkçe/İspanyolca)
                        const noAppointmentPhrases = [
                            'no appointments available',
                            'appointment not available',
                            'randevu yok',
                            'hiç randevu yok',
                            'müsait randevu yok',
                            'no slots available',
                            'fully booked',
                            'no available dates',
                            'sin citas disponibles',
                            'keine termine verfügbar',
                            'pas de rendez-vous disponible',
                            'todos los horarios están ocupados',
                            'no hay fechas disponibles'
                        ];
                        
                        // Randevu mevcut ifadeleri
                        const appointmentAvailable = [
                            'book appointment',
                            'randevu al',
                            'appointment available',
                            'available dates',
                            'select date',
                            'tarih seçin',
                            'choose date',
                            'pick a date',
                            'schedule appointment',
                            'reservar cita',
                            'seleccionar fecha',
                            'cita disponible',
                            'fechas disponibles'
                        ];
                        
                        // BLS spesifik randevu ifadeleri
                        const blsAppointmentPhrases = [
                            'bls appointment',
                            'visa appointment',
                            'application appointment',
                            'biometric appointment',
                            'document submission',
                            'passport collection',
                            'vac appointment'
                        ];
                        
                        // Form elementleri kontrolü - gelişmiş
                        const hasForm = document.querySelector('form') !== null ||
                                       document.querySelector('input[type="date"]') !== null ||
                                       document.querySelector('select') !== null ||
                                       document.querySelector('.calendar') !== null ||
                                       document.querySelector('[class*="calendar"]') !== null ||
                                       document.querySelector('[class*="appointment"]') !== null ||
                                       document.querySelector('button[class*="book"]') !== null ||
                                       document.querySelector('[name*="date"]') !== null ||
                                       document.querySelector('[id*="appointment"]') !== null;
                        
                        // BLS spesifik elementler
                        const hasBlsElements = document.querySelector('[class*="bls"]') !== null ||
                                             document.querySelector('[id*="bls"]') !== null ||
                                             document.querySelector('.appointment-form') !== null ||
                                             document.querySelector('.booking-calendar') !== null ||
                                             document.querySelector('[class*="spain"]') !== null ||
                                             document.querySelector('[class*="visa"]') !== null;
                        
                        // Gelişmiş JavaScript ile clickable element arama
                        const hasClickableAppointmentElements = [...document.querySelectorAll('div, span, a, button')].some(e => {
                            const text = (e.innerText || '').toLowerCase();
                            return text.includes('book appointment') || 
                                   text.includes('schedule appointment') ||
                                   text.includes('reserve appointment') ||
                                   text.includes('cita previa') ||
                                   text.includes('reservar cita');
                        });
                        
                        // API endpoint kontrolü
                        const hasApiEndpoints = [...document.querySelectorAll('script')].some(script => {
                            const scriptText = script.textContent || '';
                            return scriptText.includes('/api/appointment') ||
                                   scriptText.includes('/booking/') ||
                                   scriptText.includes('appointment-api') ||
                                   scriptText.includes('bls-api') ||
                                   scriptText.includes('/calendar/');
                        });
                        
                        // Date picker elementleri
                        const hasDatePicker = document.querySelector('input[type="date"]') !== null ||
                                            document.querySelector('.datepicker') !== null ||
                                            document.querySelector('[class*="date"]') !== null ||
                                            document.querySelector('.ui-datepicker') !== null ||
                                            document.querySelector('[data-date]') !== null;
                        
                        // Sonuç hesaplama ve detay
                        const result = {
                            foundAppointmentSystem: false,
                            foundBlsSystem: false,
                            foundSpainVisa: false,
                            appointmentStatus: 'unknown',
                            details: []
                        };
                        
                        // BLS Spain spesifik kontroller
                        if (blsSpainChecks.hasBlsSpain) {
                            result.foundBlsSystem = true;
                            result.details.push('BLS Spain sistemi');
                        }
                        
                        if (blsSpainChecks.hasSpainVisa || blsSpainChecks.hasSchengenVisa) {
                            result.foundSpainVisa = true;
                            result.details.push('İspanya/Schengen visa sayfası');
                        }
                        
                        if (blsSpainChecks.hasTourismVisa) {
                            result.foundSpainVisa = true;
                            result.details.push('Turizm vizesi');
                        }
                        
                        if (blsSpainChecks.hasAppointmentBooking) {
                            result.foundAppointmentSystem = true;
                            result.details.push('Randevu booking sistemi');
                        }
                        
                        // Önce randevu yokluğunu kontrol et
                        let hasNoAppointment = false;
                        for (const phrase of noAppointmentPhrases) {
                            if (bodyText.includes(phrase)) {
                                hasNoAppointment = true;
                                result.appointmentStatus = 'not_available';
                                result.details.push('Randevu mevcut değil');
                                break;
                            }
                        }
                        
                        // Randevu mevcut kontrolü (sadece "randevu yok" yoksa)
                        if (!hasNoAppointment) {
                            let hasAppointmentAvailable = false;
                            
                            // Genel randevu ifadeleri
                            for (const phrase of appointmentAvailable) {
                                if (bodyText.includes(phrase)) {
                                    hasAppointmentAvailable = true;
                                    result.appointmentStatus = 'available';
                                    result.details.push('Randevu mevcut');
                                    break;
                                }
                            }
                            
                            // BLS spesifik randevu ifadeleri
                            if (!hasAppointmentAvailable) {
                                for (const phrase of blsAppointmentPhrases) {
                                    if (bodyText.includes(phrase)) {
                                        hasAppointmentAvailable = true;
                                        result.appointmentStatus = 'system_available';
                                        result.details.push('BLS randevu sistemi');
                                        break;
                                    }
                                }
                            }
                            
                            if (hasAppointmentAvailable) {
                                result.foundAppointmentSystem = true;
                            }
                        }
                        
                        // Form veya sistem elementleri kontrolü
                        if (hasForm || hasBlsElements || hasClickableAppointmentElements || hasApiEndpoints || hasDatePicker) {
                            result.foundAppointmentSystem = true;
                            if (result.appointmentStatus === 'unknown') {
                                result.appointmentStatus = 'system_available';
                                result.details.push('Randevu sistemi mevcut');
                            }
                        }
                        
                        // Genel başarı durumu
                        result.success = result.foundAppointmentSystem || 
                                       result.foundBlsSystem || 
                                       result.foundSpainVisa ||
                                       result.appointmentStatus !== 'unknown';
                        
                        return result;
                    }""")

                    logger.info("JavaScript kontrolü (%s): %s", city, {
                        'success': appointment_check.get('success', False),
                        'status': appointment_check.get('appointmentStatus', 'unknown'),
                        'details': appointment_check.get('details', [])
                    })

                    browser.close()

                    if appointment_check.get('success', False):
                        details = " | ".join(appointment_check.get('details', ['Sistem mevcut']))
                        status = appointment_check.get('appointmentStatus', 'unknown')
                        
                        if status == 'available':
                            return [f"📍 {location_info['name']} (Browser): ✅ {details}"]
                        elif status == 'not_available':
                            return [f"📍 {location_info['name']} (Browser): ❌ {details}"]
                        else:
                            return [f"📍 {location_info['name']} (Browser): 🔍 {details}"]
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