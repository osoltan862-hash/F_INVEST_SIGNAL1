# F-INVEST Signal Engine V1.0

مشروع مستقل لتحليل عملة محددة على فريم 5 دقائق، مستوحى من العناصر الظاهرة في الفيديو.

## مواصفات النسخة

- Symbol محدد يدويًا، الافتراضي: `BTC/USDT`
- Timeframe: `5m`
- Volume Filter: `1.3x`
- Volume Lookback: `11`
- Impulse WMA: `50 / 103`
- Momentum Confirmation: OFF
- SL: `30`
- TP1: `15`
- TP2: `30`
- TP3: `50`
- Analysis only: لا يرسل أوامر تداول.

## تشغيل

```bash
pip install -r requirements.txt
python main.py
```

لتغيير العملة:

```bash
SYMBOL=ETH/USDT python main.py
```

في Railway ضع المتغيرات الموجودة في `.env.example` داخل Variables.

## ملاحظة

هذا ليس نسخة حرفية من كود المؤشر الأصلي؛ كود المؤشر الأصلي غير موجود في الفيديو. المنطق هنا يعيد بناء العناصر والإعدادات الظاهرة فقط، لذلك يجب اختبار الإشارات ونتائج TP/SL قبل الاعتماد عليه.
