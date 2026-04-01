"""
Генерация карточки с ценами для Instagram через Pillow.
Загрузка на Cloudflare R2 через boto3.
"""
import io
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Размер — квадрат 1080×1080 (стандарт Instagram)
IMG_W, IMG_H = 1080, 1080

# Цвета (минималистичный стиль)
BG_COLOR      = (255, 255, 255)   # белый фон
ACCENT_COLOR  = (34, 139, 87)     # зелёный (как minprice)
TEXT_DARK     = (30, 30, 30)      # почти чёрный
TEXT_GRAY     = (110, 110, 110)   # серый для подписей
ROW_ALT       = (245, 248, 245)   # чуть зелёный для чётных строк
DIVIDER       = (220, 232, 220)   # линия-разделитель


def _load_fonts():
    """Загружает системные шрифты (fallback на дефолтный если нет нужных)."""
    from PIL import ImageFont
    font_paths = [
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    regular_paths = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

    def try_load(paths, size):
        for p in paths:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    return {
        "logo":    try_load(font_paths,   64),
        "title":   try_load(font_paths,   42),
        "product": try_load(regular_paths, 38),
        "price":   try_load(font_paths,   44),
        "store":   try_load(regular_paths, 30),
        "footer":  try_load(regular_paths, 28),
        "date":    try_load(regular_paths, 26),
    }


def generate_price_image(products: list[dict]) -> bytes:
    """
    Генерирует PNG-картинку 1080×1080 с топ-5 продуктами.

    products: [{"name": str, "price": int|float, "store": str}, ...]
    Возвращает PNG в виде bytes.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (IMG_W, IMG_H), BG_COLOR)
    draw = ImageDraw.Draw(img)
    fonts = _load_fonts()

    # ── Шапка ──────────────────────────────────────────────
    draw.rectangle([0, 0, IMG_W, 130], fill=ACCENT_COLOR)

    # Логотип
    logo_text = "minprice.kz"
    draw.text((50, 35), logo_text, font=fonts["logo"], fill="white")

    # Дата справа
    date_str = datetime.now().strftime("%d.%m.%Y")
    date_bbox = draw.textbbox((0, 0), date_str, font=fonts["date"])
    date_w = date_bbox[2] - date_bbox[0]
    draw.text((IMG_W - date_w - 40, 52), date_str, font=fonts["date"], fill="white")

    # ── Подзаголовок ────────────────────────────────────────
    title = "🛒 Топ дешёвых продуктов сегодня"
    draw.text((50, 155), title, font=fonts["title"], fill=TEXT_DARK)

    # Линия под подзаголовком
    draw.rectangle([50, 210, IMG_W - 50, 213], fill=DIVIDER)

    # ── Список продуктов ────────────────────────────────────
    top5 = products[:5]
    row_h = 140          # высота строки
    y_start = 230

    for i, product in enumerate(top5):
        y = y_start + i * row_h

        # Чередующийся фон
        if i % 2 == 1:
            draw.rectangle([40, y, IMG_W - 40, y + row_h - 8], fill=ROW_ALT, outline=DIVIDER)

        # Номер
        num_text = f"{i + 1}."
        draw.text((60, y + 38), num_text, font=fonts["product"], fill=ACCENT_COLOR)

        # Название продукта (обрезаем если длинное)
        name = str(product.get("name", ""))
        if len(name) > 28:
            name = name[:26] + "…"
        draw.text((105, y + 38), name, font=fonts["product"], fill=TEXT_DARK)

        # Магазин
        store = str(product.get("store", ""))
        draw.text((105, y + 85), store, font=fonts["store"], fill=TEXT_GRAY)

        # Цена (справа)
        price_val = product.get("price", 0)
        price_text = f"{int(price_val):,}₸".replace(",", " ")
        price_bbox = draw.textbbox((0, 0), price_text, font=fonts["price"])
        price_w = price_bbox[2] - price_bbox[0]
        draw.text((IMG_W - price_w - 60, y + 42), price_text, font=fonts["price"], fill=ACCENT_COLOR)

    # Линия над футером
    draw.rectangle([50, IMG_H - 110, IMG_W - 50, IMG_H - 107], fill=DIVIDER)

    # ── Футер ───────────────────────────────────────────────
    footer = "Сравнивай цены во всех магазинах Казахстана"
    draw.text((50, IMG_H - 92), footer, font=fonts["footer"], fill=TEXT_GRAY)

    site = "minprice.kz"
    site_bbox = draw.textbbox((0, 0), site, font=fonts["footer"])
    site_w = site_bbox[2] - site_bbox[0]
    draw.text((IMG_W - site_w - 50, IMG_H - 92), site, font=fonts["footer"], fill=ACCENT_COLOR)

    # ── Сохраняем в байты ───────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def upload_to_r2(image_bytes: bytes, filename: str | None = None) -> str:
    """
    Загружает PNG в Cloudflare R2 и возвращает публичный URL.
    filename по умолчанию: posts/daily-2026-03-31.png
    """
    import boto3
    from botocore.config import Config

    if filename is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"posts/daily-{date_str}.png"

    account_id  = os.getenv("CLOUDFLARE_R2_ACCOUNT_ID", "")
    access_key  = os.getenv("CLOUDFLARE_R2_ACCESS_KEY", "")
    secret_key  = os.getenv("CLOUDFLARE_R2_SECRET_KEY", "")
    bucket      = os.getenv("CLOUDFLARE_R2_BUCKET", "instagram-pics")
    endpoint    = os.getenv("CLOUDFLARE_R2_ENDPOINT",
                            f"https://{account_id}.r2.cloudflarestorage.com")
    public_url  = os.getenv("CLOUDFLARE_R2_PUBLIC_URL", "").rstrip("/")

    if not all([account_id, access_key, secret_key]):
        raise ValueError("Cloudflare R2 credentials не настроены в .env")

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

    s3.put_object(
        Bucket=bucket,
        Key=filename,
        Body=image_bytes,
        ContentType="image/png",
        # R2 не поддерживает ACL — публичный доступ через public bucket policy
    )

    url = f"{public_url}/{filename}"
    logger.info(f"R2: загружено → {url}")
    return url
