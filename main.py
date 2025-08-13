import requests
from bs4 import BeautifulSoup
import sqlite3
from telegram import Bot, InputMediaPhoto
from telegram.error import TelegramError
import time
import schedule
import random
import logging
from datetime import datetime
import json
import os

# ===== تهيئة التسجيل =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ouedkniss_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('OuedknissMonitor')

# ===== إعدادات التكوين =====
class Config:
    TELEGRAM_TOKEN = "8049690165:AAEYWPJzpggMfwEXykjG-ybYFc8poRtY9_0"
    CHAT_ID = "5686817749"
    CHECK_INTERVAL = 5  # دقائق بين الفحوصات
    REQUEST_TIMEOUT = 25  # ثانية لكل طلب
    MAX_RETRIES = 3  # محاولات إعادة لكل طلب فاشل

    BASE_URL = "https://www.ouedkniss.com/automobiles_vehicules/1?keywords="

    KEYWORDS = [
        "Transporter", "Multivan", "Transporteur", "Caravelle", "Kombi",
        "طروسبورتار", "ميلتيفان", "T5", "T6", "T6.1", "طرانسبورتاو", "golf"
    ]

    WILAYAS = []  # مثال: ["16", "31"] للجزائر العاصمة ووهران

    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
    ]

    PROXY_LIST = []  # قم بملء هذه القائمة إذا كنت تستخدم بروكسي

    @classmethod
    def get_random_proxy(cls):
        return random.choice(cls.PROXY_LIST) if cls.PROXY_LIST else None

# ===== إدارة قاعدة البيانات =====
class DatabaseManager:
    def __init__(self):
        self.db_path = 'ouedkniss_listings.db'
        self._init_db()

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()

        # إنشاء الجدول إذا لم يكن موجوداً
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                title TEXT,
                price TEXT,
                url TEXT,
                keyword TEXT,
                wilaya TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notified BOOLEAN DEFAULT 0
            )
        ''')

        # إنشاء الفهارس
        self.cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_listing_keyword 
            ON listings (keyword)
        ''')

        self.conn.commit()

    def listing_exists(self, listing_id):
        self.cursor.execute('''
            SELECT 1 FROM listings WHERE id = ?
        ''', (listing_id,))
        return self.cursor.fetchone() is not None

    def add_listing(self, listing_data):
        try:
            self.cursor.execute('''
                INSERT INTO listings 
                (id, title, price, url, keyword, wilaya, image_url, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                listing_data['id'],
                listing_data['title'],
                listing_data['price'],
                listing_data['url'],
                listing_data['keyword'],
                listing_data['wilaya'],
                listing_data['image_url'],
                1 if listing_data.get('notified', False) else 0
            ))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_unnotified_listings(self):
        self.cursor.execute('''
            SELECT * FROM listings WHERE notified = 0 LIMIT 10
        ''')
        columns = [col[0] for col in self.cursor.description]
        return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

    def mark_as_notified(self, listing_id):
        self.cursor.execute('''
            UPDATE listings SET notified = 1 WHERE id = ?
        ''', (listing_id,))
        self.conn.commit()

    def close(self):
        self.conn.close()

# ===== مدير التنبيهات =====
class AlertManager:
    def __init__(self):
        self.bot = Bot(token=Config.TELEGRAM_TOKEN)
        self.last_notification_time = 0
        self.notification_cooldown = 2  # ثانية بين الإشعارات

    def send_alert(self, listing):
        current_time = time.time()
        time_since_last = current_time - self.last_notification_time

        if time_since_last < self.notification_cooldown:
            time.sleep(self.notification_cooldown - time_since_last)

        try:
            message = self._format_message(listing)

            if listing.get('image_url'):
                self._send_photo_alert(listing['image_url'], message)
            else:
                self._send_text_alert(message)

            self.last_notification_time = time.time()
            return True

        except Exception as e:
            logger.error(f"فشل إرسال التنبيه: {str(e)}")
            return False

    def _format_message(self, listing):
        return (
            f"🚗 **إعلان جديد ({listing['keyword']})**\n\n"
            f"📍 **الولاية:** {listing.get('wilaya', 'غير معروف')}\n"
            f"🔹 **العنوان:** {listing['title']}\n"
            f"💰 **السعر:** {listing['price']}\n\n"
            f"🔗 [عرض الإعلان على Ouedkniss]({listing['url']})\n"
            f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _send_photo_alert(self, image_url, message):
        try:
            self.bot.send_photo(
                chat_id=Config.CHAT_ID,
                photo=image_url,
                caption=message,
                parse_mode='Markdown',
                timeout=Config.REQUEST_TIMEOUT
            )
        except Exception as e:
            logger.warning(f"فشل إرسال الصورة: {str(e)}، جارٍ إرسال نص فقط...")
            self._send_text_alert(message)

    def _send_text_alert(self, message):
        self.bot.send_message(
            chat_id=Config.CHAT_ID,
            text=message,
            parse_mode='Markdown',
            disable_web_page_preview=False,
            timeout=Config.REQUEST_TIMEOUT
        )

# ===== مدير الزحف =====
class CrawlerManager:
    def __init__(self):
        self.db = DatabaseManager()
        self.alert = AlertManager()
        self.session = requests.Session()
        self.session.headers.update({
            'Accept-Language': 'ar,fr-FR;q=0.9,fr;q=0.8,en-US;q=0.7,en;q=0.6',
            'Referer': 'https://www.ouedkniss.com/'
        })

    def scrape_keyword(self, keyword):
        url = self._build_search_url(keyword)

        for attempt in range(Config.MAX_RETRIES):
            try:
                response = self._make_request(url)
                return self._parse_response(response, keyword)
            except Exception as e:
                logger.warning(f"المحاولة {attempt + 1} فشلت للكلمة '{keyword}': {str(e)}")
                if attempt == Config.MAX_RETRIES - 1:
                    logger.error(f"فشل جميع المحاولات للكلمة '{keyword}'")
                    return []
                time.sleep(random.uniform(2, 5))

    def _build_search_url(self, keyword):
        url = Config.BASE_URL + requests.utils.quote(keyword)
        if Config.WILAYAS:
            url += f"&w={','.join(Config.WILAYAS)}"
        return url

    def _make_request(self, url):
        proxy = Config.get_random_proxy()
        headers = {'User-Agent': random.choice(Config.USER_AGENTS)}

        response = self.session.get(
            url,
            headers=headers,
            timeout=Config.REQUEST_TIMEOUT,
            proxies={'http': proxy, 'https': proxy} if proxy else None
        )
        response.raise_for_status()
        return response

    def _parse_response(self, response, keyword):
        soup = BeautifulSoup(response.text, 'html.parser')
        listings = soup.find_all('li', attrs={"data-id": True})
        new_listings = []

        for listing in listings:
            listing_data = self._extract_listing_data(listing, keyword)
            if listing_data and not self.db.listing_exists(listing_data['id']):
                if self.db.add_listing(listing_data):
                    new_listings.append(listing_data)

        return new_listings

    def _extract_listing_data(self, listing, keyword):
        # التحقق من وقت النشر
        time_element = listing.find('span', class_='annonce_date')
        if not time_element:
            return None

        time_text = time_element.get_text(strip=True).lower()
        if not any(x in time_text for x in ['minute', 'minutes', 'الآن', 'heure', 'hour', 'ساعة']):
            return None

        # استخراج بيانات الإعلان
        listing_id = listing.get("data-id")
        title = listing.find('h2').get_text(strip=True) if listing.find('h2') else "بدون عنوان"
        price = listing.find('span', class_='annonce_prix').get_text(strip=True) if listing.find('span', class_='annonce_prix') else "غير مذكور"
        link = listing.find('a', href=True)
        listing_url = "https://www.ouedkniss.com" + link['href'] if link else None

        # استخراج الصورة (يدعم السمة data-src)
        image = listing.find('img')
        image_url = None
        if image:
            image_url = image.get('src') or image.get('data-src')
            if image_url and image_url.startswith('//'):
                image_url = 'https:' + image_url

        # استخراج الولاية
        wilaya = None
        location = listing.find('span', class_='annonce_region')
        if location:
            wilaya = location.get_text(strip=True).split(' ')[0]

        return {
            'id': listing_id,
            'title': title,
            'price': price,
            'url': listing_url,
            'keyword': keyword,
            'wilaya': wilaya,
            'image_url': image_url
        }

# ===== الوظيفة الرئيسية =====
def main():
    logger.info("🚀 بدء تشغيل بوت مراقبة Ouedkniss")

    # إرسال رسالة بدء التشغيل
    alert = AlertManager()
    try:
        alert.send_alert({
            'keyword': 'نظام المراقبة',
            'title': 'بدأ تشغيل بوت مراقبة Ouedkniss',
            'price': '',
            'url': '',
            'wilaya': '',
            'image_url': None
        })
    except Exception as e:
        logger.error(f"فشل إرسال رسالة البدء: {str(e)}")

    # تهيئة المديرين
    crawler = CrawlerManager()

    # جدولة المهام
    def monitoring_job():
        logger.info("بدء دورة المراقبة الجديدة")
        start_time = time.time()

        for keyword in Config.KEYWORDS:
            try:
                logger.info(f"جارٍ فحص الكلمة المفتاحية: {keyword}")
                new_listings = crawler.scrape_keyword(keyword)

                for listing in new_listings:
                    if alert.send_alert(listing):
                        crawler.db.mark_as_notified(listing['id'])
                    time.sleep(random.uniform(1, 3))  # تأخير عشوائي بين الإعلانات

                # تأخير بين الكلمات المفتاحية المختلفة
                time.sleep(random.uniform(2, 5))

            except Exception as e:
                logger.error(f"خطأ أثناء معالجة الكلمة '{keyword}': {str(e)}")

        elapsed = time.time() - start_time
        logger.info(f"اكتملت دورة المراقبة في {elapsed:.2f} ثانية")

    # الجدولة الأولية
    schedule.every(Config.CHECK_INTERVAL).minutes.do(monitoring_job)

    # التشغيل الفوري للدورة الأولى
    monitoring_job()

    # الحلقة الرئيسية
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("تلقي إشارة الإيقاف، جارٍ الإغلاق...")
    finally:
        crawler.db.close()
        logger.info("تم إيقاف البوت بنجاح")

if __name__ == "__main__":
    main()