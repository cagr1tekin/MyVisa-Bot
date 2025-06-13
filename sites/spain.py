#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
İspanya Vize Randevu Kontrolü
İspanya vize randevularını kontrol eder.
ProxyManager entegrasyonu ile optimizasyon.
"""

import requests
import json
import logging
import time
import random
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

class SpainChecker:
    """İspanya vize randevu kontrol işlemlerini yönetir."""

    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://sede.administracionespublicas.gob.es"
        
        # Gelişmiş anti-bot header sistemi
        self.headers = get_anti_bot_headers(self.base_url, 'es')
        self.session.headers.update(self.headers)
        
        # ProxyManager kullan
        self.proxy_manager = ProxyManager()
        self.proxies = self.proxy_manager.load_valid_proxies()
        
        # Hatalı proxy'leri blacklist'te tut
        self.blacklisted_proxies = set()
        self.proxy_timeout = 3  # 7'den 3'e düşürüldü (agresif)
        
        logger.info("Spain Checker başlatıldı - ProxyManager entegrasyonu ile")
        logger.info("Geçerli proxy sayısı: %d", len(self.proxies))

    def _get_random_proxy(self) -> Optional[Dict]:
        """Requests için proxy dict formatında döndür - ProxyManager'dan çek"""
        # Proxy'leri ProxyManager'dan yenile
        self.proxies = self.proxy_manager.load_valid_proxies()
        
        if not self.proxies:
            return None

        available_proxies = [p for p in self.proxies if p not in self.blacklisted_proxies]
        if not available_proxies:
            return None

        proxy_url = random.choice(available_proxies)
        
        try:
            parsed = urlparse(proxy_url)
            if not (parsed.hostname and parsed.port):
                self.blacklisted_proxies.add(proxy_url)
                return None
            
            return {'http': proxy_url, 'https': proxy_url}
            
        except Exception as e:
            self.blacklisted_proxies.add(proxy_url)
            return None

    def _handle_proxy_failure(self, proxy_url: str, error_type: str):
        """Proxy başarısızlıklarını yönet"""
        try:
            # ProxyManager ile blacklist'e ekle
            self.proxy_manager.add_to_blacklist(proxy_url, error_type)
            # Local blacklist'e de ekle
            self.blacklisted_proxies.add(proxy_url)
            # Proxy listesinden çıkar
            if proxy_url in self.proxies:
                self.proxies.remove(proxy_url)
        except Exception as e:
            logger.error("Proxy başarısızlık yönetim hatası: %s", str(e))

    def _make_request(self, url: str, method: str = 'GET', **kwargs) -> Optional[requests.Response]:
        """HTTP request gönder - proxy ile - Gelişmiş anti-bot header'larla"""
        proxy_dict = self._get_random_proxy()
        
        try:
            # Her istek için yeni anti-bot header'lar al
            dynamic_headers = get_anti_bot_headers(url, 'es', referer=self.base_url)
            
            # Mevcut header'ları güncelle
            combined_headers = {**self.headers, **dynamic_headers}
            if 'headers' in kwargs:
                combined_headers.update(kwargs['headers'])
            kwargs['headers'] = combined_headers
            
            if proxy_dict:
                kwargs['proxies'] = proxy_dict
                kwargs['timeout'] = self.proxy_timeout
            
            response = self.session.request(method, url, **kwargs)
            
            if response.status_code == 200:
                return response
            else:
                if proxy_dict:
                    proxy_url = proxy_dict.get('http', '')
                    if proxy_url:
                        self._handle_proxy_failure(proxy_url, f"HTTP_{response.status_code}")
                
        except Exception as e:
            if proxy_dict:
                proxy_url = proxy_dict.get('http', '')
                if proxy_url:
                    self._handle_proxy_failure(proxy_url, type(e).__name__)
            logger.error("Request hatası: %s", str(e))
        
        return None

    def check_visa_appointment(self, visa_type: str = "turist", appointment_date: datetime = None) -> bool:
        """İspanya vize randevusu kontrolü yapar"""
        try:
            logger.info("İspanya %s vize randevusu kontrol ediliyor...", visa_type)
            
            # Örnek kontrol URL'i
            url = f"{self.base_url}/icpplus/index.html"
            
            response = self._make_request(url)
            
            if response:
                logger.info("✅ İspanya vize sistemi erişilebilir")
                return True
            else:
                logger.info("ℹ️ İspanya vize sistemi erişilemiyor")
                return False
                
        except Exception as e:
            logger.error("İspanya vize kontrolünde hata: %s", str(e))
            return False

    def check(self) -> bool:
        """Basit randevu kontrolü - main.py için"""
        return self.check_visa_appointment()

    def get_available_appointments(self, visa_type: str = "turist") -> List[datetime]:
        """Mevcut randevuları getirir - şimdilik boş liste döndürür"""
        logger.info("İspanya mevcut randevular kontrol ediliyor...")
        return [] 