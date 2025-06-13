#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gelişmiş Tarayıcı Header'ları
403 hatalarını önlemek için gerçekçi tarayıcı header'ları sağlar.
"""

import random
from typing import Dict, Optional

class BrowserHeaders:
    """Gerçekçi tarayıcı header'larını yönetir"""
    
    # Güncel User-Agent listesi
    USER_AGENTS = [
        # Chrome Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        
        # Firefox Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        
        # Edge Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
        
        # Çeşitli platformlar
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    ]
    
    # Accept header varyasyonları
    ACCEPT_HEADERS = [
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    ]
    
    # Accept-Language header'ları
    ACCEPT_LANGUAGE_HEADERS = {
        'tr': 'tr-TR,tr;q=0.9,en;q=0.8,en-US;q=0.7',
        'en': 'en-US,en;q=0.9,tr;q=0.8',
        'en-ca': 'en-CA,en;q=0.9,fr-CA;q=0.8,fr;q=0.7',
        'es': 'es-ES,es;q=0.9,en;q=0.8,tr;q=0.7',
        'it': 'it-IT,it;q=0.9,en;q=0.8,tr;q=0.7',
        'de': 'de-DE,de;q=0.9,en;q=0.8,tr;q=0.7',
    }
    
    # Accept-Encoding
    ACCEPT_ENCODING = 'gzip, deflate, br, zstd'
    
    # Cache-Control options
    CACHE_CONTROL_OPTIONS = [
        'max-age=0',
        'no-cache',
        'no-cache, no-store, must-revalidate',
        'max-age=300',
    ]
    
    @classmethod
    def get_headers(cls, 
                   site_type: str = 'general',
                   language: str = 'tr',
                   referer: Optional[str] = None,
                   include_anti_bot: bool = True) -> Dict[str, str]:
        """
        Belirtilen site tipine göre optimized header set döndürür
        
        Args:
            site_type: 'visa', 'government', 'api', 'general'
            language: Dil kodu ('tr', 'en', 'es', etc.)
            referer: Referer URL'si
            include_anti_bot: Anti-bot header'larını dahil et
            
        Returns:
            Dict[str, str]: Header dictionary
        """
        headers = {}
        
        # User-Agent (random seçim)
        headers['User-Agent'] = random.choice(cls.USER_AGENTS)
        
        # Accept header (site tipine göre)
        if site_type == 'api':
            headers['Accept'] = 'application/json, text/plain, */*'
        else:
            headers['Accept'] = random.choice(cls.ACCEPT_HEADERS)
        
        # Accept-Language
        lang_key = language.lower()
        if lang_key in cls.ACCEPT_LANGUAGE_HEADERS:
            headers['Accept-Language'] = cls.ACCEPT_LANGUAGE_HEADERS[lang_key]
        else:
            headers['Accept-Language'] = cls.ACCEPT_LANGUAGE_HEADERS['tr']
        
        # Accept-Encoding
        headers['Accept-Encoding'] = cls.ACCEPT_ENCODING
        
        # Connection
        headers['Connection'] = 'keep-alive'
        
        # Cache-Control (anti-bot için)
        if include_anti_bot:
            headers['Cache-Control'] = random.choice(cls.CACHE_CONTROL_OPTIONS)
        
        # DNT (Do Not Track)
        if include_anti_bot and random.choice([True, False]):
            headers['DNT'] = '1'
        
        # Sec-Fetch headers (modern tarayıcılar için)
        if include_anti_bot:
            headers['Sec-Fetch-Dest'] = 'document'
            headers['Sec-Fetch-Mode'] = 'navigate'
            headers['Sec-Fetch-Site'] = 'same-origin' if referer else 'none'
            headers['Sec-Fetch-User'] = '?1'
        
        # Upgrade-Insecure-Requests
        headers['Upgrade-Insecure-Requests'] = '1'
        
        # Referer (varsa)
        if referer:
            headers['Referer'] = referer
        
        # Site-specific headers
        if site_type == 'visa':
            # VFS Global/iDATA/BLS için
            headers['Pragma'] = 'no-cache'
            if 'vfsglobal' in (referer or ''):
                headers['X-Requested-With'] = 'XMLHttpRequest'
        
        elif site_type == 'government':
            # Resmi siteler için
            headers['Sec-GPC'] = '1'  # Global Privacy Control
            
        elif site_type == 'api':
            # API istekleri için
            headers['Content-Type'] = 'application/json'
            headers['X-Requested-With'] = 'XMLHttpRequest'
        
        return headers
    
    @classmethod
    def get_requests_headers(cls, 
                           site_url: str = '',
                           language: str = 'tr',
                           referer: Optional[str] = None) -> Dict[str, str]:
        """
        Requests library için optimize edilmiş header'lar
        
        Args:
            site_url: Hedef site URL'si
            language: Dil kodu
            referer: Referer URL
            
        Returns:
            Dict[str, str]: Header dictionary
        """
        site_type = 'general'
        
        # Site tipini belirle
        if any(keyword in site_url.lower() for keyword in ['visa', 'vfs', 'bls', 'diplo', 'ustraveldocs']):
            site_type = 'visa'
        elif any(keyword in site_url.lower() for keyword in ['gov', 'canada.ca', 'administracion']):
            site_type = 'government'
        elif '/api/' in site_url.lower() or 'json' in site_url.lower():
            site_type = 'api'
        
        return cls.get_headers(
            site_type=site_type,
            language=language,
            referer=referer,
            include_anti_bot=True
        )
    
    @classmethod  
    def get_playwright_headers(cls,
                              site_url: str = '',
                              language: str = 'tr') -> Dict[str, str]:
        """
        Playwright browser için header'lar
        
        Args:
            site_url: Hedef site URL'si
            language: Dil kodu
            
        Returns:
            Dict[str, str]: Header dictionary
        """
        headers = cls.get_requests_headers(site_url, language)
        
        # Playwright için gereksiz header'ları kaldır
        unnecessary_headers = [
            'Accept-Encoding',  # Playwright otomatik halleder
            'Connection',       # Playwright otomatik halleder
            'Upgrade-Insecure-Requests'  # Playwright otomatik halleder
        ]
        
        for header in unnecessary_headers:
            headers.pop(header, None)
        
        return headers
    
    @classmethod
    def get_session_config(cls, site_url: str = '') -> Dict:
        """
        Requests session için kapsamlı konfigürasyon
        
        Args:
            site_url: Hedef site URL'si
            
        Returns:
            Dict: Session konfigürasyonu
        """
        config = {
            'headers': cls.get_requests_headers(site_url),
            'timeout': 10,
            'allow_redirects': True,
            'verify': True,  # SSL sertifika doğrulama
        }
        
        # Site-specific ayarlar
        if 'blsspainvisa' in site_url.lower():
            config['verify'] = False  # BLS SSL problemi için
            config['timeout'] = 15
        
        return config

# Kullanım kolaylığı için fonksiyon wrapper'ları
def get_anti_bot_headers(site_url: str = '', language: str = 'tr', referer: str = None) -> Dict[str, str]:
    """403 hatalarını önlemek için anti-bot header'lar döndürür"""
    return BrowserHeaders.get_requests_headers(site_url, language, referer)

def get_random_user_agent() -> str:
    """Random User-Agent döndürür"""
    return random.choice(BrowserHeaders.USER_AGENTS) 