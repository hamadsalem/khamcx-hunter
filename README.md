# KHAMCX Hunter Bot 🎯

بوت اكتشاف عملات Solana المسبق يطبق **Hunter KHAMCX 40%** من خطة التحدي 16 يوم.

## 🎯 ما يفعله

البوت يفحص توكنات Solana الجديدة على Dexscreener كل 15 دقيقة، ويطبق:

### الفلاتر (من PDF):
- ✅ العمر: 30 دقيقة - 8 ساعات
- ✅ Market Cap: $25K - $220K
- ✅ Liquidity: $8K - $80K
- ✅ Volume 5M: > $2K
- ✅ Volume 1H: > $20K
- ✅ Transactions: 25+ (5M) / 120+ (1H)
- ✅ Buy/Sell Ratio: 1.2x - 3x
- ✅ Price Change 5M: -12% to +20%

### Kill Switches:
- 🚫 شموع طويلة (5m > 50%)
- 🚫 صعود تجاوز 250% في 24h
- 🚫 سيولة منخفضة جداً

### نظام التقييم (100 نقطة):
- 25 - قوة الشارت (يدوي - افتراضي 20)
- 20 - الفوليوم والسيولة
- 15 - قوة المشترين
- 15 - سلامة العملة (افتراضي 12)
- 15 - مساحة الصعود 40%
- 10 - وقت التداول

**القاعدة الصارمة: لا قبول تحت 85 نقطة**

## 📨 إشعارات Telegram

كل ACCEPT يأتي بـ:
- العقد كاملاً
- Score breakdown مفصل
- روابط Dexscreener / GMGN / Photon
- قائمة تحقق يدوي

## ⚙️ الإعداد

### 1. GitHub Secrets المطلوبة:
- `TELEGRAM_TOKEN` - من BotFather
- `TELEGRAM_CHAT_ID` - معرف القناة/المحادثة

### 2. Repository Settings:
- Settings → Actions → General → Workflow permissions: **Read and write**

### 3. Make repo Public (اختياري):
لتفعيل GitHub Actions غير محدود مجاناً.

## 🚀 التشغيل

البوت يعمل تلقائياً كل 15 دقيقة، أو يمكنك تشغيله يدوياً من تبويب Actions.

## ⚠️ تنبيه

- هذا paper mode فقط
- لا توصيات تداول
- البيانات تُجمع لمدة أسبوع للتحليل
- الجزء البصري (الشارت، الارتداد، الدعم) يحتاج عينك أنت

## 📊 الملفات الناتجة

- `khamcx_signals.jsonl` - سجل كل القرارات
- `khamcx_seen.json` - cache الـ deduplication

## 🧪 التحليل

بعد أسبوع من التشغيل:
1. حمّل ملف `khamcx_signals.jsonl`
2. حلل النسب: ACCEPT vs REJECT vs KILL
3. تابع التوكنات المقبولة - كم نجحت في تحقيق +25%؟

---

Built for personal research and education. Not financial advice.
