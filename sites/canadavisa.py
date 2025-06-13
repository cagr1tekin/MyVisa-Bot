#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kanada Vize Randevu Kontrolü (canada.ca)
Kanada vize randevularını IRCC sistemi üzerinden kontrol eder.
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

class CanadaVisaChecker:
    """Kanada vize randevu kontrol işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://canada.ca"
        
        # Gelişmiş anti-bot header sistemi
        self.headers = get_anti_bot_headers(self.base_url, 'en-ca')
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
        
        # Kanada vize merkezi URL'leri (Türkiye için)
        self.locations = {
            'ankara': {
                'url': 'https://www.canada.ca/en/immigration-refugees-citizenship/services/application/application-forms-guides/guide-0005-application-temporary-resident-visa.html',
                'name': 'Kanada Ankara Konsolosluğu'
            },
            'istanbul': {
                'url': 'https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada.html',
                'name': 'Kanada İstanbul Konsolosluğu'
            },
            'ircc_portal': {
                'url': 'https://ircc.canada.ca/english/information/applications/visa.asp',
                'name': 'IRCC Randevu Sistemi'
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
            dynamic_headers = get_anti_bot_headers(url, 'en-ca', referer=self.base_url)
            
            # Mevcut header'ları güncelle
            combined_headers = {**self.headers, **dynamic_headers}
            if 'headers' in kwargs:
                combined_headers.update(kwargs['headers'])
            kwargs['headers'] = combined_headers
            
            # Timeout'u kwargs'ta yoksa ekle
            if 'timeout' not in kwargs:
                kwargs['timeout'] = self.proxy_timeout
            
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
        """Kanada vize randevularını kontrol et"""
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
            logger.error("Kanada vize kontrolünde hata: %s", str(e))
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
            
            # Randevu sistemi ifadeleri
            appointment_system_phrases = [
                'book an appointment',
                'schedule appointment',
                'make an appointment',
                'appointment booking',
                'visa application centre',
                'biometric appointment',
                'randevu al',
                'randevu oluştur'
            ]

            # Randevu link'leri
            appointment_links = soup.find_all('a', href=re.compile(r'appointment|booking|schedule', re.I))

            # IRCC portal referansları
            ircc_references = soup.find_all(text=re.compile(r'ircc|immigration.*canada', re.I))

            appointments = []
            
            # Randevu sistemi metni kontrolü
            has_appointment_system = any(phrase in page_text for phrase in appointment_system_phrases)
            
            # Link kontrolü
            has_appointment_links = len(appointment_links) > 0
            
            # IRCC referansı
            has_ircc_reference = len(ircc_references) > 0

            if has_appointment_system or has_appointment_links:
                appointments.append(f"📍 {location_info['name']} (HTTP): Randevu sistemi mevcut")
            elif has_ircc_reference:
                appointments.append(f"📍 {location_info['name']} (HTTP): IRCC portal yönlendirmesi")

            if appointments:
                logger.info("HTTP ile randevu sistemi bulundu: %s", city)

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
                    locale='en-CA',  # Kanada lokali
                    ignore_https_errors=True,
                    extra_http_headers=BrowserHeaders.get_playwright_headers(location_info['url'], 'en-ca')
                )

                page = context.new_page()
                page.set_default_timeout(30000)

                # Canada.ca sayfasına git
                logger.info("Canada.ca sayfası yükleniyor: %s", location_info['url'])
                response = page.goto(location_info['url'], wait_until='networkidle')

                if not response or response.status != 200:
                    logger.error("Sayfa yüklenemedi (%s): %d", city, response.status if response else 0)
                    browser.close()
                    return []

                # Sayfa yüklenmesini bekle
                time.sleep(random.uniform(3, 6))

                # JavaScript ile randevu kontrol sistemi - gelişmiş kontroller
                try:
                    appointment_check = page.evaluate("""() => {
                        const bodyText = document.body.innerText.toLowerCase();
                        const titleText = document.title.toLowerCase();
                        
                        // Özel Kanada visa kontrolleri
                        const canadaVisaChecks = {
                            hasTemporaryResidentVisa: bodyText.includes('temporary resident visa'),
                            hasVisitorVisaApplication: titleText.includes('application for visitor visa'),
                            hasWorkPermit: bodyText.includes('work permit') || bodyText.includes('permis de travail'),
                            hasStudyPermit: bodyText.includes('study permit') || bodyText.includes('permis d\\'études'),
                            hasBiometric: bodyText.includes('biometric') || bodyText.includes('biométrique')
                        };
                        
                        // Randevu sistemi ifadeleri (İngilizce/Fransızca)
                        const appointmentSystemPhrases = [
                            'book an appointment',
                            'schedule appointment', 
                            'make an appointment',
                            'appointment booking',
                            'biometric appointment',
                            'visa application centre',
                            'vac appointment',
                            'rendez-vous',
                            'prendre rendez-vous',
                            'planifier un rendez-vous',
                            'centre de réception des demandes de visa'
                        ];
                        
                        // IRCC portal ifadeleri
                        const irccPhrases = [
                            'ircc',
                            'immigration, refugees and citizenship canada',
                            'online application system',
                            'my application',
                            'check application status',
                            'gckey',
                            'signin-canada',
                            'application status tracker',
                            'permanent residence portal'
                        ];
                        
                        // VFS Global / VAC (Visa Application Centre) kontrolleri
                        const vacPhrases = [
                            'vfs global',
                            'visa application centre',
                            'vac appointment',
                            'biometric services',
                            'document submission',
                            'passport collection'
                        ];
                        
                        // Appointment link'leri kontrolü
                        const appointmentLinks = document.querySelectorAll(`
                            a[href*="appointment"], a[href*="booking"], a[href*="schedule"],
                            a[href*="vfsglobal"], a[href*="vac"], a[href*="biometric"]
                        `);
                        
                        // IRCC/Portal link'leri
                        const irccLinks = document.querySelectorAll(`
                            a[href*="ircc"], a[href*="cic.gc.ca"], a[href*="canada.ca/en/immigration"],
                            a[href*="gckey"], a[href*="signin-canada"]
                        `);
                        
                        // Form elementleri (randevu için)
                        const hasAppointmentForm = document.querySelector('form') !== null ||
                                                 document.querySelector('input[type="date"]') !== null ||
                                                 document.querySelector('select[name*="appointment"]') !== null ||
                                                 document.querySelector('button[class*="book"]') !== null ||
                                                 document.querySelector('button[class*="appointment"]') !== null ||
                                                 document.querySelector('input[name*="date"]') !== null;
                        
                        // Canada.ca spesifik elementler
                        const hasCanadaElements = document.querySelector('[class*="canada"]') !== null ||
                                                document.querySelector('[id*="canada"]') !== null ||
                                                document.querySelector('.gc-') !== null ||
                                                document.querySelector('[class*="ircc"]') !== null ||
                                                document.querySelector('.wb-') !== null;
                        
                        // Gelişmiş JavaScript ile element arama
                        const hasClickableAppointmentElements = [...document.querySelectorAll('div, span, a, button')].some(e => {
                            const text = (e.innerText || '').toLowerCase();
                            return text.includes('book appointment') || 
                                   text.includes('schedule appointment') ||
                                   text.includes('biometric appointment') ||
                                   text.includes('vac appointment');
                        });
                        
                        // Randevu sistemi metni kontrolü
                        let hasAppointmentText = false;
                        for (const phrase of appointmentSystemPhrases) {
                            if (bodyText.includes(phrase)) {
                                hasAppointmentText = true;
                                break;
                            }
                        }
                        
                        // IRCC referansı kontrolü
                        let hasIrccReference = false;
                        for (const phrase of irccPhrases) {
                            if (bodyText.includes(phrase)) {
                                hasIrccReference = true;
                                break;
                            }
                        }
                        
                        // VAC/VFS Global kontrolü
                        let hasVacReference = false;
                        for (const phrase of vacPhrases) {
                            if (bodyText.includes(phrase)) {
                                hasVacReference = true;
                                break;
                            }
                        }
                        
                        // API endpoint'lerini kontrol et
                        const hasApiEndpoints = [...document.querySelectorAll('script')].some(script => {
                            const scriptText = script.textContent || '';
                            return scriptText.includes('/api/appointment') ||
                                   scriptText.includes('/booking/') ||
                                   scriptText.includes('appointment-api') ||
                                   scriptText.includes('ircc-api');
                        });
                        
                        // Sonuç hesaplama ve detay
                        const result = {
                            foundAppointmentSystem: false,
                            foundIrccPortal: false,
                            foundVacSystem: false,
                            foundVisaApplication: false,
                            details: []
                        };
                        
                        // Özel visa kontrollerini değerlendir
                        if (canadaVisaChecks.hasTemporaryResidentVisa) {
                            result.foundVisaApplication = true;
                            result.details.push('Temporary Resident Visa sayfası');
                        }
                        
                        if (canadaVisaChecks.hasVisitorVisaApplication) {
                            result.foundVisaApplication = true;
                            result.details.push('Visitor Visa Application sayfası');
                        }
                        
                        if (canadaVisaChecks.hasWorkPermit) {
                            result.foundVisaApplication = true;
                            result.details.push('Work Permit sayfası');
                        }
                        
                        if (canadaVisaChecks.hasStudyPermit) {
                            result.foundVisaApplication = true;
                            result.details.push('Study Permit sayfası');
                        }
                        
                        if (canadaVisaChecks.hasBiometric) {
                            result.foundAppointmentSystem = true;
                            result.details.push('Biometric Services');
                        }
                        
                        // Randevu sistemi kontrolü
                        if (hasAppointmentText || appointmentLinks.length > 0 || hasAppointmentForm || hasClickableAppointmentElements || hasApiEndpoints) {
                            result.foundAppointmentSystem = true;
                            result.details.push('Randevu sistemi mevcut');
                        }
                        
                        // VAC/VFS Global kontrolü
                        if (hasVacReference) {
                            result.foundVacSystem = true;
                            result.details.push('VAC/VFS Global sistemi');
                        }
                        
                        // IRCC portal kontrolü
                        if (hasIrccReference || irccLinks.length > 0 || hasCanadaElements) {
                            result.foundIrccPortal = true;
                            result.details.push('IRCC portal/yönlendirme');
                        }
                        
                        // Genel başarı durumu
                        result.success = result.foundAppointmentSystem || 
                                       result.foundIrccPortal || 
                                       result.foundVacSystem || 
                                       result.foundVisaApplication;
                        
                        return result;
                    }""")

                    logger.info("JavaScript kontrolü (%s): %s", city, {
                        'success': appointment_check.get('success', False),
                        'details': appointment_check.get('details', [])
                    })

                    browser.close()

                    if appointment_check.get('success', False):
                        details = " | ".join(appointment_check.get('details', ['Sistem mevcut']))
                        return [f"📍 {location_info['name']} (Browser): {details}"]
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