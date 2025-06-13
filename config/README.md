# Telegram Bildirim Konfigürasyonu

Bu dosya Telegram bot'unuzun çoklu kullanıcıya bildirim gönderebilmesi için gerekli ayarları içerir.

## Kurulum

### 1. Telegram Bot Oluşturma

1. Telegram'da [@BotFather](https://t.me/BotFather)'a mesaj gönderin
2. `/newbot` komutunu gönderin
3. Bot'unuz için bir isim seçin
4. Bot'unuz için bir kullanıcı adı seçin (@yourbot_bot gibi)
5. BotFather size bir **Bot Token** verecek (örn: `1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789`)

### 2. Chat ID'leri Bulma

Her kullanıcı için Chat ID bulmanız gerekiyor:

**Yöntem 1: @userinfobot**
1. [@userinfobot](https://t.me/userinfobot)'a mesaj gönderin
2. Bot size Chat ID'nizi verecek

**Yöntem 2: Bot URL'si**
1. Botunuza `/start` mesajı gönderin
2. Bu URL'yi ziyaret edin: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
3. JSON yanıtında `"id"` değerini bulun

### 3. telegram_config.json Dosyasını Düzenleme

```json
{
  "telegram_bot_token": "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789",
  "telegram_chat_ids": ["123456789", "987654321", "555666777"],
  "notification_settings": {
    "enable_notifications": true,
    "message_format": "HTML",
    "retry_attempts": 3,
    "timeout": 30
  }
}
```

**Açıklamalar:**
- `telegram_bot_token`: BotFather'dan aldığınız bot token
- `telegram_chat_ids`: Bildirim almak isteyen kullanıcıların Chat ID'leri (array olarak)
- `enable_notifications`: Bildirimleri etkinleştir/devre dışı bırak
- `message_format`: Mesaj formatı ("HTML" veya "Markdown")
- `retry_attempts`: Başarısız mesajlar için yeniden deneme sayısı
- `timeout`: İstek timeout süresi (saniye)

## Backward Compatibility

Eski `.env` dosyası sistemi hala desteklenmektedir:

```env
TELEGRAM_BOT_TOKEN=1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789
TELEGRAM_CHAT_ID=123456789
```

**Öncelik sırası:**
1. `telegram_config.json` dosyası varsa önce onu kullanır
2. JSON'da eksik değerler varsa `.env` dosyasına bakar

## Test Etme

```bash
# İstatistikleri görüntüle
python telegram.py --stats

# Test mesajı gönder
python telegram.py --test
```

## Güvenlik

- Bot token'ınızı kimseyle paylaşmayın
- Config dosyasını git'e commit etmeyin (gerçek token ile)
- Mümkünse environment variables kullanın

## Sorun Giderme

### "Bot token bulunamadı" hatası
- `telegram_config.json` dosyasında `telegram_bot_token` değerini kontrol edin
- `.env` dosyasında `TELEGRAM_BOT_TOKEN` değerini kontrol edin

### "Hiç chat ID bulunamadı" uyarısı
- `telegram_config.json` dosyasında `telegram_chat_ids` array'ini kontrol edin
- Chat ID'lerin string olarak yazıldığından emin olun: `["123456789"]`

### "400 Bad Request" hatası
- Bot token'ın doğru olduğundan emin olun
- Chat ID'lerin doğru olduğundan emin olun
- Bot'un kullanıcılarla konuşma başlattığından emin olun (/start)

### Mesaj gönderilmiyor
- `enable_notifications: true` olarak ayarlandığından emin olun
- Network bağlantınızı kontrol edin
- Log'ları kontrol edin: retry_attempts kadar deneme yapılıyor 