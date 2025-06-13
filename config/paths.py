#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Path Helper Module
Tüm dosyalar için absolute path yönetimi
"""

import os

# Project root directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_project_path(*paths):
    """Project root'tan relative path ile absolute path döndür"""
    return os.path.join(PROJECT_ROOT, *paths)

# Yaygın kullanılan dosya path'leri
PROXY_LIST_FILE = get_project_path("proxy_list.txt")
PROXY_POOL_FILE = get_project_path("proxies", "proxy_pool.txt")
BLACKLIST_FILE = get_project_path("proxies", "blacklist.txt")
WORKING_PROXIES_FILE = get_project_path("proxies", "working_proxies.txt")
VALID_PROXY_POOL_FILE = get_project_path("proxies", "proxy_pool.json")

# Config dosyaları
TELEGRAM_CONFIG_FILE = get_project_path("config", "telegram_config.json")

# Log dosyası
RANDEVU_BOT_LOG = get_project_path("randevu_bot.log")

# Proxy dizini
PROXIES_DIR = get_project_path("proxies")

def ensure_directories():
    """Gerekli dizinleri oluştur"""
    os.makedirs(PROXIES_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(TELEGRAM_CONFIG_FILE), exist_ok=True)

if __name__ == "__main__":
    print(f"Project Root: {PROJECT_ROOT}")
    print(f"Proxy List: {PROXY_LIST_FILE}")
    print(f"Proxy Pool: {PROXY_POOL_FILE}")
    print(f"Blacklist: {BLACKLIST_FILE}")
    print(f"Telegram Config: {TELEGRAM_CONFIG_FILE}") 