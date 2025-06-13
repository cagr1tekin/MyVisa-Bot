#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VFS Global Ana Site Randevu Kontrolü
VFS Global'in ana website'i üzerinden browser ile
dinamik form doldurup randevu kontrolü yapar.
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

class VFSGlobalMainChecker:
    """VFS Global ana site randevu kontrol işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://www.vfsglobal.com"
        
        # Gelişmiş anti-bot header sistemi
        self.headers = get_anti_bot_headers(self.base_url, 'tr')
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
        
        # VFS Global ülke/başvuru kombinasyonları (Türkiye için)
        self.visa_selections = [
            {
                'country': 'Turkey',
                'destination': 'Italy',
                'visa_type': 'Tourism',
                'name': 'Türkiye → İtalya Turizm Vizesi'
            },
            {
                'country': 'Turkey', 
                'destination': 'Spain',
                'visa_type': 'Tourism',
                'name': 'Türkiye → İspanya Turizm Vizesi'
            },
            {
                'country': 'Turkey',
                'destination': 'Netherlands',
                'visa_type': 'Tourism', 
                'name': 'Türkiye → Hollanda Turizm Vizesi'
            }
        ]
    
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
        """VFS Global ana site üzerinden randevu kontrolü"""
        try:
            available_appointments = []

            for selection in self.visa_selections:
                logger.info("%s kontrol ediliyor...", selection['name'])

                # Browser ile interaktif kontrol
                appointments = self._check_with_interactive_browser(selection)
                if appointments:
                    available_appointments.extend(appointments)

            if available_appointments:
                return "\n".join(available_appointments)

            return None

        except Exception as e:
            logger.error("VFS Global ana site kontrolünde hata: %s", str(e))
            raise

    def _check_with_interactive_browser(self, selection: Dict) -> List[str]:
        """Playwright ile interaktif browser kontrolü"""
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
                    locale='tr-TR',  # Türkiye lokali
                    ignore_https_errors=True,
                    extra_http_headers=BrowserHeaders.get_playwright_headers(self.base_url, 'tr')
                )

                page = context.new_page()
                page.set_default_timeout(45000)

                # VFS Global ana sayfasına git
                logger.info("VFS Global ana sayfa yükleniyor: %s", self.base_url)
                response = page.goto(self.base_url, wait_until='networkidle')

                if not response or response.status != 200:
                    logger.error("Ana sayfa yüklenemedi: %d", response.status if response else 0)
                    browser.close()
                    return []

                # Sayfa yüklenmesini bekle
                time.sleep(random.uniform(3, 5))

                try:
                    # Sayfa yapısını analiz et
                    page_analysis = page.evaluate("""() => {
                        // Sayfa yapısını analiz et
                        const pageInfo = {
                            title: document.title,
                            url: window.location.href,
                            hasStartButton: false,
                            buttonTexts: [],
                            linkTexts: [],
                            formElements: [],
                            interactiveElements: []
                        };
                        
                        // Tüm butonları analiz et
                        const buttons = document.querySelectorAll('button, input[type="button"], input[type="submit"]');
                        buttons.forEach(btn => {
                            const text = btn.textContent || btn.value || btn.getAttribute('title') || btn.getAttribute('aria-label') || '';
                            if (text.trim()) {
                                pageInfo.buttonTexts.push(text.trim());
                            }
                        });
                        
                        // Tüm linkleri analiz et
                        const links = document.querySelectorAll('a[href]');
                        links.forEach(link => {
                            const text = link.textContent || link.getAttribute('title') || link.getAttribute('aria-label') || '';
                            if (text.trim()) {
                                pageInfo.linkTexts.push(text.trim());
                            }
                        });
                        
                        // Form elementlerini kontrol et
                        const forms = document.querySelectorAll('form');
                        pageInfo.formElements = Array.from(forms).map(form => ({
                            action: form.action,
                            method: form.method,
                            id: form.id,
                            className: form.className
                        }));
                        
                        // İnteraktif elementleri bul
                        const interactive = document.querySelectorAll('[onclick], [href*="javascript"], .clickable, .btn, .button');
                        interactive.forEach(elem => {
                            const text = elem.textContent || elem.getAttribute('title') || '';
                            if (text.trim()) {
                                pageInfo.interactiveElements.push(text.trim());
                            }
                        });
                        
                        return pageInfo;
                    }""")
                    
                    logger.info("Sayfa analizi: %s", {
                        'title': page_analysis.get('title', '')[:100],
                        'button_count': len(page_analysis.get('buttonTexts', [])),
                        'link_count': len(page_analysis.get('linkTexts', [])),
                        'form_count': len(page_analysis.get('formElements', [])),
                        'interactive_count': len(page_analysis.get('interactiveElements', []))
                    })
                    
                    # İlk birkaç buton/link metnini göster
                    if page_analysis.get('buttonTexts'):
                        logger.info("Bulunan butonlar: %s", page_analysis['buttonTexts'][:10])
                    if page_analysis.get('linkTexts'):
                        logger.info("Bulunan linkler: %s", page_analysis['linkTexts'][:10])
                    
                    # "Start now" butonunu bul ve tıkla - Gelişmiş arama
                    start_button_found = page.evaluate("""() => {
                        // Genişletilmiş "Start" buton/link seçicileri
                        const startKeywords = [
                            'start now', 'start', 'get started', 'begin', 'başla', 'başlat',
                            'apply now', 'apply', 'visa application', 'book appointment',
                            'find visa centre', 'select country', 'choose destination',
                            'visa services', 'visa center', 'continue', 'proceed'
                        ];
                        
                        const allClickableElements = document.querySelectorAll(`
                            button, a[href], input[type="button"], input[type="submit"],
                            [onclick], [role="button"], .btn, .button, .clickable,
                            div[class*="button"], span[class*="button"], 
                            div[class*="btn"], span[class*="btn"],
                            [class*="start"], [class*="apply"], [class*="begin"]
                        `);
                        
                        for (const element of allClickableElements) {
                            const elementText = (
                                element.textContent || 
                                element.value || 
                                element.getAttribute('title') || 
                                element.getAttribute('aria-label') || 
                                element.getAttribute('alt') ||
                                ''
                            ).toLowerCase().trim();
                            
                            // Anahtar kelime kontrolü
                            for (const keyword of startKeywords) {
                                if (elementText.includes(keyword.toLowerCase())) {
                                    // Element görünür mü kontrol et
                                    const isVisible = element.offsetParent !== null && 
                                                    getComputedStyle(element).display !== 'none' &&
                                                    getComputedStyle(element).visibility !== 'hidden';
                                    
                                    if (isVisible) {
                                        return { 
                                            found: true, 
                                            element: element.tagName,
                                            text: elementText,
                                            keyword: keyword,
                                            selector: element.className ? `.${element.className.split(' ')[0]}` : element.tagName
                                        };
                                    }
                                }
                            }
                        }
                        
                        // Hiçbir start butonu bulunamadıysa, herhangi bir ana işlem butonunu ara
                        const mainActionElements = document.querySelectorAll(`
                            button:not([type="button"]), input[type="submit"], 
                            a[href]:not([href="#"]):not([href="javascript:void(0)"]),
                            [class*="primary"], [class*="main"], [class*="hero"]
                        `);
                        
                        for (const element of mainActionElements) {
                            const isVisible = element.offsetParent !== null && 
                                            getComputedStyle(element).display !== 'none';
                            
                            if (isVisible) {
                                const text = (element.textContent || '').trim();
                                if (text.length > 0 && text.length < 100) { // Makul uzunlukta metin
                                    return { 
                                        found: true, 
                                        element: element.tagName,
                                        text: text,
                                        keyword: 'main_action',
                                        selector: element.className ? `.${element.className.split(' ')[0]}` : element.tagName
                                    };
                                }
                            }
                        }
                        
                        return { found: false, element: null, text: '', keyword: '', selector: '' };
                    }""")

                    if start_button_found['found']:
                        logger.info("Start/Action buton bulundu: %s (%s) - %s", 
                                  start_button_found['element'], 
                                  start_button_found['keyword'],
                                  start_button_found['text'][:50])
                        
                        # Butona tıkla
                        click_success = page.evaluate(f"""() => {{
                            const startKeywords = [
                                'start now', 'start', 'get started', 'begin', 'başla', 'başlat',
                                'apply now', 'apply', 'visa application', 'book appointment',
                                'find visa centre', 'select country', 'choose destination',
                                'visa services', 'visa center', 'continue', 'proceed'
                            ];
                            
                            const targetKeyword = '{start_button_found['keyword']}';
                            
                            const allClickableElements = document.querySelectorAll(`
                                button, a[href], input[type="button"], input[type="submit"],
                                [onclick], [role="button"], .btn, .button, .clickable,
                                div[class*="button"], span[class*="button"], 
                                div[class*="btn"], span[class*="btn"],
                                [class*="start"], [class*="apply"], [class*="begin"]
                            `);
                            
                            for (const element of allClickableElements) {{
                                const elementText = (
                                    element.textContent || 
                                    element.value || 
                                    element.getAttribute('title') || 
                                    element.getAttribute('aria-label') || 
                                    element.getAttribute('alt') ||
                                    ''
                                ).toLowerCase().trim();
                                
                                if (targetKeyword === 'main_action' || elementText.includes(targetKeyword.toLowerCase())) {{
                                    const isVisible = element.offsetParent !== null && 
                                                    getComputedStyle(element).display !== 'none' &&
                                                    getComputedStyle(element).visibility !== 'hidden';
                                    
                                    if (isVisible) {{
                                        try {{
                                            element.click();
                                            return true;
                                        }} catch (e) {{
                                            console.log('Click hatası:', e);
                                        }}
                                    }}
                                }}
                            }}
                            
                            return false;
                        }}""")
                        
                        if click_success:
                            logger.info("Start butonuna başarıyla tıklandı")
                            
                            # Yeni sayfa yüklenmesini bekle
                            time.sleep(random.uniform(3, 6))
                            
                            # URL değişim kontrolü
                            new_url = page.url
                            if new_url != self.base_url:
                                logger.info("Sayfa yönlendirildi: %s", new_url)
                                
                                # Eğer direkt visa.vfsglobal.com'a yönlendirildiyse
                                if 'visa.vfsglobal.com' in new_url:
                                    logger.info("Direkt VFS visa portal'ına yönlendirildi")
                                    api_result = self._check_visa_api(page, new_url, selection)
                                    browser.close()
                                    
                                    if api_result:
                                        return [f"📍 {selection['name']}: {api_result}"]
                                    else:
                                        return [f"📍 {selection['name']}: Visa portal'ına yönlendirildi"]
                                else:
                                    # Form sayfasında devam et
                                    return self._continue_with_form_filling(page, selection, browser)
                            else:
                                logger.warning("Sayfa yönlendirmesi gerçekleşmedi")
                        else:
                            logger.warning("Start butonuna tıklanamadı")
                    else:
                        logger.warning("Start buton bulunamadı - Alternatif yöntem deneniyor")
                        
                        # Alternatif: Doğrudan VFS visa portal URL'lerine git
                        alternative_result = self._try_direct_visa_urls(page, selection, browser)
                        if alternative_result:
                            return alternative_result

                    browser.close()
                    return []

                except Exception as interaction_error:
                    logger.error("Browser interaction hatası: %s", str(interaction_error))
                    browser.close()
                    return []

        except Exception as e:
            logger.error("Interactive browser kontrolü hatası: %s", str(e))
            return []

    def _select_country(self, page, country: str = "Turkey") -> bool:
        """Ülke seçimi yap - gelişmiş JavaScript yöntemleri ile"""
        try:
            logger.info("Ülke seçiliyor: %s", country)
            
            # Yöntem 1: Gelişmiş JavaScript evaluate ile element arama
            country_selected = page.evaluate(f"""() => {{
                const targetText = '{country}';
                const alternativeTexts = ['Türkiye', 'Turkiye', 'Turkey', 'TR'];
                
                // 1. Önce select option'larında ara
                const selects = document.querySelectorAll('select');
                for (const select of selects) {{
                    const options = select.querySelectorAll('option');
                    for (const option of options) {{
                        const optionText = option.innerText.toLowerCase();
                        const optionValue = option.value.toLowerCase();
                        
                        // Herhangi bir alternatif metinle eşleşiyor mu?
                        for (const altText of alternativeTexts) {{
                            if (optionText.includes(altText.toLowerCase()) || 
                                optionValue.includes(altText.toLowerCase())) {{
                                select.value = option.value;
                                select.dispatchEvent(new Event('change'));
                                return true;
                            }}
                        }}
                    }}
                }}
                
                // 2. Clickable elementlerde ara (div, span, a, button)
                const clickableElements = [...document.querySelectorAll('div, span, a, button, li, p')];
                for (const element of clickableElements) {{
                    if (!element.innerText) continue;
                    
                    const elementText = element.innerText.toLowerCase();
                    for (const altText of alternativeTexts) {{
                        if (elementText.includes(altText.toLowerCase())) {{
                            element.click();
                            return true;
                        }}
                    }}
                }}
                
                // 3. Input field'larda ara ve otomatik tamamlama
                const inputs = document.querySelectorAll('input[type="text"], input[type="search"]');
                for (const input of inputs) {{
                    // Placeholder, name veya id'si ülke ile ilgili mi?
                    const inputAttrs = [
                        input.placeholder,
                        input.name,
                        input.id,
                        input.className
                    ].join(' ').toLowerCase();
                    
                    if (inputAttrs.includes('country') || 
                        inputAttrs.includes('ülke') ||
                        inputAttrs.includes('origin') ||
                        inputAttrs.includes('from')) {{
                        
                        // Önce Turkey dene
                        input.value = 'Turkey';
                        input.dispatchEvent(new Event('input'));
                        input.dispatchEvent(new Event('change'));
                        
                        // Biraz bekle ve Türkiye dene
                        setTimeout(() => {{
                            input.value = 'Türkiye';
                            input.dispatchEvent(new Event('input'));
                            input.dispatchEvent(new Event('change'));
                        }}, 500);
                        
                        return true;
                    }}
                }}
                
                // 4. Radio button kontrolleri
                const radios = document.querySelectorAll('input[type="radio"]');
                for (const radio of radios) {{
                    const label = document.querySelector(`label[for="${{radio.id}}"]`);
                    if (label) {{
                        const labelText = label.innerText.toLowerCase();
                        for (const altText of alternativeTexts) {{
                            if (labelText.includes(altText.toLowerCase())) {{
                                radio.checked = true;
                                radio.dispatchEvent(new Event('change'));
                                return true;
                            }}
                        }}
                    }}
                }}
                
                return false;
            }}""")
            
            if country_selected:
                logger.info("Ülke JavaScript ile başarıyla seçildi: %s", country)
                time.sleep(3)  # Ülke seçimi sonrası biraz daha bekle
                return True
            
            # Yöntem 2: Playwright selector metodları
            try:
                # Select dropdown kontrolleri
                selects = page.query_selector_all('select')
                for select in selects:
                    options = select.query_selector_all('option')
                    for option in options:
                        option_text = option.inner_text().strip().lower()
                        option_value = option.get_attribute('value').lower() if option.get_attribute('value') else ""
                        
                        # Türkiye alternatifleri
                        if any(alt.lower() in option_text or alt.lower() in option_value 
                               for alt in ['turkey', 'türkiye', 'turkiye', 'tr']):
                            select.select_option(option.get_attribute('value'))
                            logger.info("Select dropdown ile ülke seçildi: %s", option_text)
                            time.sleep(3)
                            return True
                
                # Text içeren elementleri bul ve tıkla
                country_selectors = [
                    "//div[contains(text(), 'Turkey')]",
                    "//div[contains(text(), 'Türkiye')]",
                    "//span[contains(text(), 'Turkey')]", 
                    "//span[contains(text(), 'Türkiye')]",
                    "//a[contains(text(), 'Turkey')]",
                    "//a[contains(text(), 'Türkiye')]",
                    "//button[contains(text(), 'Turkey')]",
                    "//li[contains(text(), 'Turkey')]",
                    "//li[contains(text(), 'Türkiye')]",
                    "//*[contains(@data-value, 'turkey')]",
                    "//*[contains(@data-value, 'TR')]"
                ]
                
                for selector in country_selectors:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible():
                            element.click()
                            logger.info("XPath selector ile ülke seçildi: %s", selector)
                            time.sleep(3)
                            return True
                    except Exception:
                        continue
                
            except Exception as e:
                logger.debug("Playwright selector hatası: %s", str(e))
            
            # Yöntem 3: Search input'larına yazma
            try:
                search_selectors = [
                    'input[type="text"]',
                    'input[type="search"]',
                    'input[placeholder*="country"]',
                    'input[placeholder*="ülke"]',
                    'input[name*="country"]',
                    'input[name*="origin"]'
                ]
                
                for selector in search_selectors:
                    try:
                        input_field = page.locator(selector).first
                        if input_field.is_visible():
                            # Önce Turkey dene
                            input_field.fill("Turkey")
                            input_field.press('Enter')
                            logger.info("Input field'a Turkey yazıldı: %s", selector)
                            time.sleep(2)
                            
                            # Eğer dropdown açıldıysa Turkey'i seç
                            try:
                                turkey_option = page.locator("text=Turkey").first
                                if turkey_option.is_visible():
                                    turkey_option.click()
                                    time.sleep(2)
                            except:
                                # Türkiye dene
                                input_field.fill("Türkiye")
                                input_field.press('Enter')
                                time.sleep(2)
                            
                            return True
                    except Exception:
                        continue
                        
            except Exception as e:
                logger.debug("Input field hatası: %s", str(e))
            
            logger.warning("Ülke seçimi başarısız: %s", country)
            return False
            
        except Exception as e:
            logger.error("Ülke seçim hatası: %s", str(e))
            return False

    def _select_destination(self, page, destination: str) -> bool:
        """Hedef ülke seçimi yap"""
        try:
            destination_selected = page.evaluate(f"""(destination) => {{
                // Hedef ülke/vize seçim elementleri
                const selectors = [
                    'select[name*="destination"]',
                    'select[name*="visa"]',
                    'select[id*="destination"]',
                    'select[id*="visa"]',
                    '.destination-select',
                    '.visa-select'
                ];
                
                for (const selector of selectors) {{
                    try {{
                        const element = document.querySelector(selector);
                        if (element && element.tagName === 'SELECT') {{
                            const options = element.querySelectorAll('option');
                            for (const option of options) {{
                                if (option.textContent.toLowerCase().includes(destination.toLowerCase())) {{
                                    option.selected = true;
                                    element.dispatchEvent(new Event('change'));
                                    return true;
                                }}
                            }}
                        }}
                    }} catch (e) {{
                        // Devam et
                    }}
                }}
                
                // Alternatif: Tıklanabilir hedef ülke linkleri
                const destinationLinks = document.querySelectorAll('a, button, div, span');
                for (const link of destinationLinks) {{
                    if (link.textContent && link.textContent.toLowerCase().includes(destination.toLowerCase())) {{
                        link.click();
                        return true;
                    }}
                }}
                
                return false;
            }}""", destination)
            
            if destination_selected:
                logger.info("Hedef ülke seçildi: %s", destination)
                time.sleep(random.uniform(1, 2))
                return True
            else:
                logger.warning("Hedef ülke seçimi başarısız: %s", destination)
                return False
                
        except Exception as e:
            logger.error("Hedef ülke seçim hatası: %s", str(e))
            return False

    def _select_visa_type(self, page, visa_type: str = "Tourism") -> bool:
        """Vize tipini seç - gelişmiş JavaScript yöntemleri ile"""
        try:
            logger.info("Vize tipi seçiliyor: %s", visa_type)
            
            # Yöntem 1: Gelişmiş JavaScript evaluate ile element arama
            tourism_selected = page.evaluate(f"""() => {{
                const targetText = '{visa_type}';
                
                // 1. Önce select option'larında ara
                const selects = document.querySelectorAll('select');
                for (const select of selects) {{
                    const options = select.querySelectorAll('option');
                    for (const option of options) {{
                        if (option.innerText.toLowerCase().includes(targetText.toLowerCase()) ||
                            option.value.toLowerCase().includes(targetText.toLowerCase())) {{
                            select.value = option.value;
                            select.dispatchEvent(new Event('change'));
                            return true;
                        }}
                    }}
                }}
                
                // 2. Clickable elementlerde ara (div, span, a, button)
                const clickableElements = [...document.querySelectorAll('div, span, a, button, li, p')];
                const matches = clickableElements.filter(e => 
                    e.innerText && e.innerText.toLowerCase().includes(targetText.toLowerCase())
                );
                
                if (matches.length > 0) {{
                    // En iyi match'i bul (tam eşleşme öncelikli)
                    let bestMatch = matches[0];
                    for (const match of matches) {{
                        if (match.innerText.toLowerCase().trim() === targetText.toLowerCase()) {{
                            bestMatch = match;
                            break;
                        }}
                    }}
                    
                    // Element'e tıkla
                    bestMatch.click();
                    return true;
                }}
                
                // 3. Input field'larda ara
                const inputs = document.querySelectorAll('input[type="text"], input[type="search"]');
                for (const input of inputs) {{
                    if (input.placeholder && input.placeholder.toLowerCase().includes('visa') ||
                        input.name && input.name.toLowerCase().includes('visa') ||
                        input.id && input.id.toLowerCase().includes('visa')) {{
                        input.value = targetText;
                        input.dispatchEvent(new Event('input'));
                        input.dispatchEvent(new Event('change'));
                        return true;
                    }}
                }}
                
                // 4. Radio button kontrolleri
                const radios = document.querySelectorAll('input[type="radio"]');
                for (const radio of radios) {{
                    const label = document.querySelector(`label[for="${{radio.id}}"]`);
                    if (label && label.innerText.toLowerCase().includes(targetText.toLowerCase())) {{
                        radio.checked = true;
                        radio.dispatchEvent(new Event('change'));
                        return true;
                    }}
                }}
                
                return false;
            }}""")
            
            if tourism_selected:
                logger.info("Vize tipi JavaScript ile başarıyla seçildi: %s", visa_type)
                time.sleep(2)  # Seçim sonrası bekle
                return True
            
            # Yöntem 2: Playwright selector metodları
            try:
                # Select dropdown kontrolleri
                selects = page.query_selector_all('select')
                for select in selects:
                    options = select.query_selector_all('option')
                    for option in options:
                        option_text = option.inner_text().strip().lower()
                        if visa_type.lower() in option_text:
                            select.select_option(option.get_attribute('value'))
                            logger.info("Select dropdown ile vize tipi seçildi: %s", option_text)
                            time.sleep(2)
                            return True
                
                # Text içeren elementleri bul ve tıkla
                clickable_selectors = [
                    f"//div[contains(text(), '{visa_type}')]",
                    f"//span[contains(text(), '{visa_type}')]", 
                    f"//a[contains(text(), '{visa_type}')]",
                    f"//button[contains(text(), '{visa_type}')]",
                    f"//li[contains(text(), '{visa_type}')]",
                    f"//*[contains(@class, 'tourism')]",
                    f"//*[contains(@class, 'visa')]",
                    f"//*[contains(@data-value, 'tourism')]"
                ]
                
                for selector in clickable_selectors:
                    try:
                        element = page.locator(selector).first
                        if element.is_visible():
                            element.click()
                            logger.info("XPath selector ile vize tipi seçildi: %s", selector)
                            time.sleep(2)
                            return True
                    except Exception:
                        continue
                
            except Exception as e:
                logger.debug("Playwright selector hatası: %s", str(e))
            
            # Yöntem 3: Fallback - Manuel input doldurma
            try:
                # Visible input field'ları bul
                input_selectors = [
                    'input[type="text"]',
                    'input[type="search"]', 
                    'input[placeholder*="visa"]',
                    'input[placeholder*="type"]',
                    'input[name*="visa"]',
                    'input[name*="type"]'
                ]
                
                for selector in input_selectors:
                    try:
                        input_field = page.locator(selector).first
                        if input_field.is_visible():
                            input_field.fill(visa_type)
                            input_field.press('Enter')
                            logger.info("Input field ile vize tipi girildi: %s", selector)
                            time.sleep(2)
                            return True
                    except Exception:
                        continue
                        
            except Exception as e:
                logger.debug("Input field hatası: %s", str(e))
            
            logger.warning("Vize tipi seçimi başarısız: %s", visa_type)
            return False
            
        except Exception as e:
            logger.error("Vize tipi seçim hatası: %s", str(e))
            return False

    def _submit_form(self, page) -> bool:
        """Form submit/continue işlemi - Gelişmiş element bekleme ile"""
        try:
            # Sayfanın tam yüklenmesini bekle
            page.wait_for_load_state('networkidle', timeout=10000)
            time.sleep(random.uniform(1, 3))
            
            # Dinamik içerik için fazladan bekleme
            logger.debug("Submit buton aranıyor - dinamik içerik bekleniyor...")
            
            # JavaScript ile dinamik submit buton bekleme ve tıklama
            submitted = page.evaluate("""() => {
                return new Promise((resolve) => {
                    let attempts = 0;
                    const maxAttempts = 10;
                    
                    function findAndClickSubmit() {
                        attempts++;
                        
                        // Submit/Continue buton seçicileri - daha kapsamlı
                        const submitSelectors = [
                            // Standart submit butonları
                            'button[type="submit"]',
                            'input[type="submit"]',
                            
                            // Metin tabanlı seçiciler (case insensitive)
                            'button:has-text("Continue")',
                            'button:has-text("CONTINUE")',
                            'button:has-text("Next")',
                            'button:has-text("NEXT")',
                            'button:has-text("Submit")',
                            'button:has-text("SUBMIT")',
                            'button:has-text("Proceed")',
                            'button:has-text("PROCEED")',
                            'button:has-text("Search")',
                            'button:has-text("SEARCH")',
                            'button:has-text("Find")',
                            'button:has-text("Book")',
                            'button:has-text("Apply")',
                            
                            // Türkçe butonlar
                            'button:has-text("Devam")',
                            'button:has-text("İleri")',
                            'button:has-text("Gönder")',
                            'button:has-text("Ara")',
                            'button:has-text("Bul")',
                            
                            // Link tabanlı continue
                            'a:has-text("Continue")',
                            'a:has-text("Next")',
                            'a:has-text("Proceed")',
                            'a:has-text("Devam")',
                            
                            // CSS class tabanlı
                            '.btn-continue',
                            '.btn-submit',
                            '.btn-next',
                            '.btn-search',
                            '.btn-primary',
                            '.submit-btn',
                            '.continue-btn',
                            '.next-btn',
                            
                            // Generic butonlar - son çare
                            'button[class*="btn"]',
                            'button[class*="button"]',
                            'input[class*="btn"]',
                            'input[class*="button"]',
                            
                            // Form submit
                            'form button',
                            'form input[type="submit"]'
                        ];
                        
                        // Her seçiciyi dene
                        for (const selector of submitSelectors) {
                            try {
                                const elements = document.querySelectorAll(selector);
                                
                                for (const element of elements) {
                                    // Element görünür mü?
                                    if (element.offsetParent !== null && 
                                        getComputedStyle(element).display !== 'none' &&
                                        getComputedStyle(element).visibility !== 'hidden') {
                                        
                                        // Element clickable durumda mı?
                                        const rect = element.getBoundingClientRect();
                                        if (rect.width > 0 && rect.height > 0) {
                                            console.log('Submit buton bulundu:', selector, element.textContent || element.value);
                                            element.click();
                                            resolve(true);
                                            return;
                                        }
                                    }
                                }
                            } catch (e) {
                                console.log('Seçici hatası:', selector, e.message);
                                // Devam et
                            }
                        }
                        
                        // Henüz bulunamadı, tekrar dene
                        if (attempts < maxAttempts) {
                            console.log('Submit buton bulunamadı, tekrar deneniyor...', attempts);
                            setTimeout(findAndClickSubmit, 1000); // 1 saniye bekle
                        } else {
                            // Son çare: Enter tuşu simüle et
                            try {
                                const inputs = document.querySelectorAll('input[type="text"], input[type="email"], select');
                                if (inputs.length > 0) {
                                    const lastInput = inputs[inputs.length - 1];
                                    const enterEvent = new KeyboardEvent('keydown', {
                                        key: 'Enter',
                                        code: 'Enter',
                                        keyCode: 13,
                                        which: 13,
                                        bubbles: true
                                    });
                                    lastInput.dispatchEvent(enterEvent);
                                    console.log('Enter tuşu simüle edildi');
                                    resolve(true);
                                    return;
                                }
                            } catch (e) {
                                console.log('Enter tuşu hatası:', e.message);
                            }
                            
                            resolve(false);
                        }
                    }
                    
                    // İlk deneme
                    findAndClickSubmit();
                });
            }""")
            
            if submitted:
                logger.info("Form submit edildi")
                # Submit sonrası yönlendirme için bekle
                time.sleep(random.uniform(3, 6))
                return True
            else:
                logger.warning("Submit buton bulunamadı - Tüm seçiciler ve dinamik bekleme denendi")
                
                # Debug için sayfa durumunu logla
                page_debug = page.evaluate("""() => {
                    return {
                        url: window.location.href,
                        title: document.title,
                        buttonCount: document.querySelectorAll('button').length,
                        inputCount: document.querySelectorAll('input').length,
                        formCount: document.querySelectorAll('form').length,
                        submitInputs: document.querySelectorAll('input[type="submit"]').length,
                        submitButtons: document.querySelectorAll('button[type="submit"]').length,
                        allButtons: Array.from(document.querySelectorAll('button')).map(b => b.textContent?.trim() || b.value || 'Unnamed').slice(0, 5)
                    };
                }""")
                logger.debug("Sayfa debug bilgisi: %s", page_debug)
                
                return False
                
        except Exception as e:
            logger.error("Form submit hatası: %s", str(e))
            return False

    def _check_visa_api(self, page, url: str, selection: Dict) -> Optional[str]:
        """visa.vfsglobal.com API kontrolü"""
        try:
            # API endpoint'i oluştur
            api_endpoints = [
                "/appointment/api/calendar/availableDates",
                "/api/appointment/calendar/available",
                "/booking/api/appointments/available"
            ]
            
            for endpoint in api_endpoints:
                try:
                    # JavaScript ile API çağrısı yap
                    api_result = page.evaluate(f"""async (endpoint) => {{
                        try {{
                            const response = await fetch(endpoint, {{
                                method: 'GET',
                                headers: {{
                                    'Accept': 'application/json',
                                    'X-Requested-With': 'XMLHttpRequest'
                                }}
                            }});
                            
                            if (response.ok) {{
                                const data = await response.json();
                                return {{ success: true, data: data }};
                            }}
                            
                            return {{ success: false, status: response.status }};
                        }} catch (error) {{
                            return {{ success: false, error: error.message }};
                        }}
                    }}""", endpoint)
                    
                    if api_result and api_result.get('success'):
                        data = api_result.get('data', {})
                        
                        # Randevu mevcut mu kontrol et
                        if isinstance(data, list) and len(data) > 0:
                            available_dates = []
                            for item in data:
                                if isinstance(item, dict) and item.get('available', False):
                                    date_str = item.get('date', '')
                                    if date_str:
                                        available_dates.append(date_str)
                            
                            if available_dates:
                                return f"API: {len(available_dates)} randevu tarihi mevcut"
                        
                        elif isinstance(data, dict):
                            available_dates = data.get('availableDates', [])
                            if available_dates:
                                return f"API: {len(available_dates)} randevu tarihi mevcut"
                    
                    logger.debug("API endpoint yanıt vermiyor: %s", endpoint)
                    
                except Exception as api_error:
                    logger.debug("API endpoint hatası %s: %s", endpoint, str(api_error))
            
            # API başarısız olursa sayfa içeriği kontrolü
            page_result = page.evaluate("""() => {
                const bodyText = document.body.innerText.toLowerCase();
                
                // Randevu mevcut ifadeleri
                const appointmentAvailable = [
                    'randevu alınabilir',
                    'randevu mevcut',
                    'appointment available',
                    'available dates',
                    'book appointment'
                ];
                
                for (const phrase of appointmentAvailable) {
                    if (bodyText.includes(phrase)) {
                        return 'Sayfa: Randevu mevcut olabilir';
                    }
                }
                
                return null;
            }""")
            
            return page_result
            
        except Exception as e:
            logger.error("API kontrol hatası: %s", str(e))
            return None

    def _get_random_proxy_url(self) -> Optional[str]:
        """Random proxy URL döndür"""
        if not self.proxies:
            return None

        available_proxies = [p for p in self.proxies if p not in self.blacklisted_proxies]
        if not available_proxies:
            return None

        return random.choice(available_proxies)

    def _continue_with_form_filling(self, page, selection: Dict, browser) -> List[str]:
        """Form doldurma işlemlerini devam ettir"""
        try:
            # Ülke seçimi yap
            country_selected = self._select_country(page, selection['country'])
            if not country_selected:
                logger.warning("Ülke seçimi başarısız: %s", selection['country'])
                browser.close()
                return []
            
            # Hedef ülke seçimi yap
            destination_selected = self._select_destination(page, selection['destination'])
            if not destination_selected:
                logger.warning("Hedef ülke seçimi başarısız: %s - Direkt URL'ler deneniyor", selection['destination'])
                # Alternatif: Direkt visa URL'lerini dene
                return self._try_direct_visa_urls(page, selection, browser)
            
            # Vize tipi seçimi yap
            visa_type_selected = self._select_visa_type(page, selection['visa_type'])
            if not visa_type_selected:
                logger.warning("Vize tipi seçimi başarısız: %s - Direkt URL'ler deneniyor", selection['visa_type'])
                # Alternatif: Direkt visa URL'lerini dene
                return self._try_direct_visa_urls(page, selection, browser)
            
            # Submit/Continue butonuna tıkla
            submitted = self._submit_form(page)
            if not submitted:
                logger.warning("Form submit başarısız - Direkt URL'ler deneniyor")
                # Alternatif: Direkt visa URL'lerini dene
                return self._try_direct_visa_urls(page, selection, browser)
            
            # Yönlendirme sonrasında URL kontrol et
            time.sleep(random.uniform(3, 5))
            current_url = page.url
            logger.info("Final yönlendirilen URL: %s", current_url)
            
            # visa.vfsglobal.com alt domain'ine yönlendirildi mi?
            if 'visa.vfsglobal.com' in current_url:
                logger.info("VFS visa portal'ına yönlendirildi")
                
                # API kontrolü yap
                api_result = self._check_visa_api(page, current_url, selection)
                browser.close()
                
                if api_result:
                    return [f"📍 {selection['name']}: {api_result}"]
                else:
                    return [f"📍 {selection['name']}: Visa portal'ına yönlendirildi"]
            else:
                logger.warning("Beklenmeyen URL yönlendirmesi: %s", current_url)
                browser.close()
                return []
                
        except Exception as e:
            logger.error("Form doldurma hatası: %s", str(e))
            browser.close()
            return []

    def _try_direct_visa_urls(self, page, selection: Dict, browser) -> List[str]:
        """Direkt VFS visa URL'lerini dene"""
        try:
            # Türkiye için bilinen VFS visa URL'leri
            direct_urls = [
                f"https://visa.vfsglobal.com/{selection['destination'].lower()}/turkey/",
                f"https://visa.vfsglobal.com/{selection['destination'].lower()}/turkey/istanbul/",
                f"https://visa.vfsglobal.com/{selection['destination'].lower()}/turkey/ankara/",
                "https://visa.vfsglobal.com/italy/turkey/",
                "https://visa.vfsglobal.com/spain/turkey/",
                "https://visa.vfsglobal.com/netherlands/turkey/"
            ]
            
            for url in direct_urls:
                try:
                    logger.info("Direkt URL deneniyor: %s", url)
                    response = page.goto(url, wait_until='networkidle', timeout=15000)
                    
                    if response and response.status == 200:
                        logger.info("Direkt URL başarılı: %s", url)
                        time.sleep(random.uniform(2, 4))
                        
                        # API kontrolü yap
                        api_result = self._check_visa_api(page, url, selection)
                        if api_result:
                            browser.close()
                            return [f"📍 {selection['name']} (Direkt): {api_result}"]
                    
                except Exception as url_error:
                    logger.debug("Direkt URL hatası %s: %s", url, str(url_error))
                    continue
            
            logger.warning("Hiçbir direkt URL çalışmadı")
            browser.close()
            return []
            
        except Exception as e:
            logger.error("Direkt URL kontrolü hatası: %s", str(e))
            browser.close()
            return [] 