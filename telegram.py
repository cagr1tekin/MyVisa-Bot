#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot Bildirimi
Randevu bulunduğunda Telegram üzerinden çoklu kullanıcıya bildirim gönderir.
"""

import os
import logging
import json
from typing import List, Dict, Optional

try:
    import requests
except ImportError:
    print("❌ 'requests' modülü bulunamadı!")
    print("📦 Lütfen şu komutu çalıştırın: pip install requests")
    raise ImportError("requests modülü kurulu değil")

try:
    from dotenv import load_dotenv
except ImportError:
    print("❌ 'python-dotenv' modülü bulunamadı!")
    print("📦 Lütfen şu komutu çalıştırın: pip install python-dotenv")
    raise ImportError("python-dotenv modülü kurulu değil")

# .env dosyasını yükle (backward compatibility için)
load_dotenv()

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram bildirim sınıfı - çoklu kullanıcı desteği ile"""
    
    def __init__(self):
        self.config_file = "config/telegram_config.json"
        self.config = self._load_config()
        
        # Bot token'ı al
        self.bot_token = self._get_bot_token()
        if not self.bot_token:
            logger.error("Telegram bot token bulunamadı!")
            raise ValueError("Telegram bot token gerekli")
        
        # Chat ID'leri al
        self.chat_ids = self._get_chat_ids()
        if not self.chat_ids:
            logger.warning("Hiç chat ID bulunamadı, bildirim gönderilemeyecek")
        
        # Ayarları al
        self.settings = self.config.get('notification_settings', {})
        self.message_format = self.settings.get('message_format', 'HTML')
        self.retry_attempts = self.settings.get('retry_attempts', 3)
        self.timeout = self.settings.get('timeout', 30)
        self.enabled = self.settings.get('enable_notifications', True)
        
        logger.info("Telegram Notifier başlatıldı - %d kullanıcı", len(self.chat_ids))
    
    def _load_config(self) -> Dict:
        """JSON config dosyasını yükle"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                logger.info("Telegram config dosyası yüklendi: %s", self.config_file)
                return config
            else:
                logger.warning("Config dosyası bulunamadı: %s", self.config_file)
                return {}
        except Exception as e:
            logger.error("Config dosyası yüklenirken hata: %s", str(e))
            return {}
    
    def _get_bot_token(self) -> Optional[str]:
        """Bot token'ı al - önce config, sonra .env"""
        # 1. JSON config'den dene
        token = self.config.get('telegram_bot_token')
        if token and token != "BOT_TOKEN_BURAYA":
            return token
        
        # 2. .env dosyasından dene (backward compatibility)
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        if token:
            logger.info("Bot token .env dosyasından alındı")
            return token
        
        return None
    
    def _get_chat_ids(self) -> List[str]:
        """Chat ID'leri al - önce config, sonra .env"""
        chat_ids = []
        
        # 1. JSON config'den dene
        config_chat_ids = self.config.get('telegram_chat_ids', [])
        if config_chat_ids:
            # Fake ID'leri filtrele
            valid_ids = [cid for cid in config_chat_ids if cid not in ["123456789", "987654321", "112233445"]]
            if valid_ids:
                chat_ids.extend(valid_ids)
        
        # 2. .env dosyasından dene (backward compatibility)
        env_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if env_chat_id and env_chat_id not in chat_ids:
            chat_ids.append(env_chat_id)
            logger.info("Chat ID .env dosyasından eklendi")
        
        return chat_ids
    
    def send_message(self, message: str) -> Dict[str, bool]:
        """
        Çoklu kullanıcıya mesaj gönder
        
        Args:
            message (str): Gönderilecek mesaj
            
        Returns:
            Dict[str, bool]: Chat ID başına başarı durumu
        """
        if not self.enabled:
            logger.info("Telegram bildirimleri devre dışı")
            return {}
        
        if not self.chat_ids:
            logger.warning("Hiç chat ID tanımlanmamış")
            return {}
        
        results = {}
        successful_sends = 0
        
        for chat_id in self.chat_ids:
            success = self._send_to_chat(chat_id, message)
            results[chat_id] = success
            if success:
                successful_sends += 1
        
        logger.info("Telegram mesajı gönderildi: %d/%d başarılı", successful_sends, len(self.chat_ids))
        return results
    
    def _send_to_chat(self, chat_id: str, message: str) -> bool:
        """
        Tek bir chat'e mesaj gönder
        
        Args:
            chat_id (str): Hedef chat ID
            message (str): Gönderilecek mesaj
            
        Returns:
            bool: Başarılıysa True
        """
        try:
            # Telegram API URL'si
            api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            # Mesaj payload'ı
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': self.message_format
            }
            
            # Retry logic ile istek gönder
            for attempt in range(self.retry_attempts):
                try:
                    response = requests.post(api_url, json=payload, timeout=self.timeout)
                    response.raise_for_status()
                    
                    logger.debug("✅ Mesaj gönderildi: Chat %s (Deneme %d)", chat_id[-4:], attempt + 1)
                    return True
                    
                except requests.exceptions.RequestException as e:
                    if attempt < self.retry_attempts - 1:
                        logger.warning("Chat %s için deneme %d başarısız, tekrar denenecek: %s", 
                                     chat_id[-4:], attempt + 1, str(e))
                    else:
                        logger.error("Chat %s için tüm denemeler başarısız: %s", chat_id[-4:], str(e))
            
            return False
            
        except Exception as e:
            logger.error("Chat %s için beklenmeyen hata: %s", chat_id[-4:], str(e))
            return False
    
    def get_stats(self) -> Dict:
        """Notifier istatistiklerini döndür"""
        return {
            'enabled': self.enabled,
            'chat_count': len(self.chat_ids),
            'bot_token_configured': bool(self.bot_token),
            'config_file_exists': os.path.exists(self.config_file),
            'message_format': self.message_format,
            'retry_attempts': self.retry_attempts,
            'timeout': self.timeout
        }


# Global instance oluştur
_notifier_instance = None

def get_notifier() -> TelegramNotifier:
    """Global TelegramNotifier instance'ını al"""
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = TelegramNotifier()
    return _notifier_instance


def send_telegram(message: str) -> bool:
    """
    Backward compatibility için eski fonksiyon
    Çoklu kullanıcıya mesaj gönderir
    
    Args:
        message (str): Gönderilecek mesaj
        
    Returns:
        bool: En az bir kullanıcıya başarıyla gönderildiyse True
    """
    try:
        notifier = get_notifier()
        results = notifier.send_message(message)
        
        # En az bir başarılı gönderim varsa True döndür
        success_count = sum(1 for success in results.values() if success)
        
        if success_count > 0:
            print(f"✅ Telegram mesajı {success_count}/{len(results)} kullanıcıya gönderildi")
            return True
        else:
            print("❌ Hiçbir kullanıcıya mesaj gönderilemedi")
            return False
            
    except Exception as e:
        print(f"❌ Telegram bildirimi hatası: {str(e)}")
        return False


def send_telegram_message(message: str) -> bool:
    """
    Alternatif fonksiyon adı (bazı modüller bunu kullanabilir)
    
    Args:
        message (str): Gönderilecek mesaj
        
    Returns:
        bool: En az bir kullanıcıya başarıyla gönderildiyse True
    """
    return send_telegram(message)


# Test fonksiyonu
if __name__ == "__main__":
    import sys
    
    # Logging ayarla
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    if len(sys.argv) > 1:
        if sys.argv[1] == '--stats':
            # İstatistikleri göster
            try:
                notifier = get_notifier()
                stats = notifier.get_stats()
                print("📊 Telegram Notifier İstatistikleri:")
                for key, value in stats.items():
                    print(f"  {key}: {value}")
            except Exception as e:
                print(f"❌ İstatistik hatası: {str(e)}")
                
        elif sys.argv[1] == '--test':
            # Test mesajı gönder
            test_message = "🧪 Test mesajı - Randevu bot çalışıyor! (Çoklu kullanıcı)"
            print("📱 Telegram bağlantısı test ediliyor...")
            result = send_telegram(test_message)
            
            if result:
                print("✅ Test başarılı!")
            else:
                print("❌ Test başarısız!")
                print("💡 config/telegram_config.json dosyasını kontrol edin")
        else:
            print("Kullanım:")
            print("  python telegram.py --test     # Test mesajı gönder")
            print("  python telegram.py --stats    # İstatistikleri göster")
    else:
        # Varsayılan test
        test_message = "🧪 Test mesajı - Randevu bot çalışıyor!"
        
        print("📱 Telegram bağlantısı test ediliyor...")
        result = send_telegram(test_message)

        if result:
            print("✅ Test başarılı!")
        else:
            print("❌ Test başarısız!")
            print("💡 config/telegram_config.json dosyasını kontrol edin ve doğru değerleri girdiğinizden emin olun")
