#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Header sistemini test eden basit script
"""

print("🔧 Header sistemi test ediliyor...")

try:
    from config.browser_headers import get_anti_bot_headers, BrowserHeaders
    print("✅ browser_headers modülü başarıyla import edildi")
    
    # Test 1: BLS İspanya
    print("\n🇪🇸 BLS İspanya Headers Test:")
    headers = get_anti_bot_headers('https://blsspainvisa.com', 'es')
    print(f"   Header sayısı: {len(headers)}")
    print(f"   User-Agent: {headers.get('User-Agent', '')[:50]}...")
    print(f"   Accept-Language: {headers.get('Accept-Language', '')}")
    print(f"   Cache-Control: {headers.get('Cache-Control', '')}")
    print(f"   Sec-Fetch-Dest: {headers.get('Sec-Fetch-Dest', '')}")
    print(f"   Referer: {headers.get('Referer', '')}")
    
    # Test 2: VFS Global
    print("\n🇮🇹 VFS Global Headers Test:")
    vfs_headers = get_anti_bot_headers('https://visa.vfsglobal.com/api/test', 'it', 'https://visa.vfsglobal.com')
    print(f"   Header sayısı: {len(vfs_headers)}")
    print(f"   User-Agent: {vfs_headers.get('User-Agent', '')[:50]}...")
    print(f"   Accept-Language: {vfs_headers.get('Accept-Language', '')}")
    print(f"   Referer: {vfs_headers.get('Referer', '')}")
    
    # Test 3: ABD
    print("\n🇺🇸 ABD Headers Test:")
    us_headers = get_anti_bot_headers('https://www.ustraveldocs.com', 'tr')
    print(f"   Header sayısı: {len(us_headers)}")
    print(f"   User-Agent: {us_headers.get('User-Agent', '')[:50]}...")
    print(f"   Accept-Language: {us_headers.get('Accept-Language', '')}")
    
    # Test 4: Almanya
    print("\n🇩🇪 Almanya Headers Test:")
    de_headers = get_anti_bot_headers('https://service2.diplo.de', 'de')
    print(f"   Header sayısı: {len(de_headers)}")
    print(f"   User-Agent: {de_headers.get('User-Agent', '')[:50]}...")
    print(f"   Accept-Language: {de_headers.get('Accept-Language', '')}")
    
    # Test 5: Kanada
    print("\n🇨🇦 Kanada Headers Test:")
    ca_headers = get_anti_bot_headers('https://canada.ca', 'en-ca')
    print(f"   Header sayısı: {len(ca_headers)}")
    print(f"   User-Agent: {ca_headers.get('User-Agent', '')[:50]}...")
    print(f"   Accept-Language: {ca_headers.get('Accept-Language', '')}")
    
    # Test 6: Playwright Headers
    print("\n🎭 Playwright Headers Test:")
    pw_headers = BrowserHeaders.get_playwright_headers('https://blsspainvisa.com', 'es')
    print(f"   Playwright Header sayısı: {len(pw_headers)}")
    for key, value in list(pw_headers.items())[:3]:
        print(f"   {key}: {value}")
    
    print("\n✅ Tüm header testleri başarıyla tamamlandı!")
    print("🚀 403 hatalarına karşı gelişmiş header sistemi hazır!")
    
except ImportError as e:
    print(f"❌ Import hatası: {e}")
except Exception as e:
    print(f"❌ Test hatası: {e}")
    import traceback
    traceback.print_exc() 