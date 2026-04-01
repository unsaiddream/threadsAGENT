"""
Генератор Reels для Instagram.
Создаёт смешной анимированный ролик с маскотом Минькой (minprice.kz).
Формат: 1080×1920 (вертикальный), MP4, ~10 сек.
"""
import io
import os
import math
import random
import logging
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 24

# Палитра
GREEN      = (34, 139, 87)
GREEN_DARK = (20, 100, 60)
GREEN_LITE = (120, 210, 150)
WHITE      = (255, 255, 255)
BLACK      = (20, 20, 20)
YELLOW     = (255, 220, 0)
RED        = (220, 50, 50)
CREAM      = (255, 248, 230)
GRAY       = (180, 180, 180)
ORANGE     = (255, 140, 0)


def _font(size: int, bold: bool = False):
    paths_bold = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    paths_regular = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    paths = paths_bold if bold else paths_regular
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _center_text(draw, text, y, font, color, width=W, shadow=False):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (width - tw) // 2
    if shadow:
        draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 120))
    draw.text((x, y), text, font=font, fill=color)


def _draw_mascot(draw, cx, cy, scale=1.0, frame=0, has_cape=False, mood="happy"):
    """
    Минька — круглый зелёный совёнок с большими глазами.
    cx, cy — центр; scale — масштаб; frame — для анимации (покачивание)
    mood: happy | shocked | cool
    """
    s = scale
    # Покачивание тела
    wobble = math.sin(frame * 0.3) * 4 * s

    # Плащ (если герой)
    if has_cape:
        cape_pts = [
            (cx - int(70*s), cy + int(20*s)),
            (cx + int(70*s), cy + int(20*s)),
            (cx + int(90*s), cy + int(160*s)),
            (cx,             cy + int(130*s)),
            (cx - int(90*s), cy + int(160*s)),
        ]
        draw.polygon(cape_pts, fill=RED)
        # Воротник плаща
        draw.rectangle([cx - int(70*s), cy + int(15*s),
                         cx + int(70*s), cy + int(35*s)], fill=YELLOW)

    # Тело
    body_r = int(110 * s)
    draw.ellipse([cx - body_r, cy - body_r + wobble,
                  cx + body_r, cy + body_r + wobble], fill=GREEN, outline=GREEN_DARK, width=4)

    # Живот (светлое пятно)
    belly_rx, belly_ry = int(65*s), int(75*s)
    draw.ellipse([cx - belly_rx, cy - belly_ry + wobble + int(20*s),
                  cx + belly_rx, cy + belly_ry + wobble + int(20*s)], fill=GREEN_LITE)

    # Глаза — большие
    eye_y = cy - int(20*s) + wobble
    for ex in [cx - int(40*s), cx + int(40*s)]:
        # Белок
        draw.ellipse([ex - int(32*s), eye_y - int(32*s),
                      ex + int(32*s), eye_y + int(32*s)], fill=WHITE, outline=BLACK, width=2)
        # Зрачок
        pupil_offset_x = int(math.sin(frame * 0.15) * 6 * s)
        pupil_offset_y = int(math.cos(frame * 0.1) * 4 * s)
        draw.ellipse([ex - int(14*s) + pupil_offset_x, eye_y - int(14*s) + pupil_offset_y,
                      ex + int(14*s) + pupil_offset_x, eye_y + int(14*s) + pupil_offset_y],
                     fill=BLACK)
        # Блик
        draw.ellipse([ex - int(5*s) + pupil_offset_x, eye_y - int(18*s) + pupil_offset_y,
                      ex + int(5*s) + pupil_offset_x, eye_y - int(8*s) + pupil_offset_y],
                     fill=WHITE)

    if mood == "shocked":
        # Рот О
        draw.ellipse([cx - int(18*s), cy + int(30*s) + wobble,
                      cx + int(18*s), cy + int(55*s) + wobble], fill=BLACK)
        # Брови вверх
        for ex in [cx - int(40*s), cx + int(40*s)]:
            brow_dir = -1 if ex < cx else 1
            draw.arc([ex - int(28*s), eye_y - int(55*s),
                      ex + int(28*s), eye_y - int(25*s)],
                     start=200, end=340, fill=BLACK, width=5)
    elif mood == "cool":
        # Очки
        for ex in [cx - int(40*s), cx + int(40*s)]:
            draw.rectangle([ex - int(35*s), eye_y - int(20*s),
                             ex + int(35*s), eye_y + int(20*s)], fill=(0,0,0,180))
        draw.line([cx - int(5*s), eye_y, cx + int(5*s), eye_y], fill=BLACK, width=4)
        # Ухмылка
        draw.arc([cx - int(30*s), cy + int(25*s) + wobble,
                  cx + int(30*s), cy + int(60*s) + wobble],
                 start=0, end=180, fill=BLACK, width=5)
    else:
        # Улыбка
        draw.arc([cx - int(30*s), cy + int(25*s) + wobble,
                  cx + int(30*s), cy + int(60*s) + wobble],
                 start=0, end=180, fill=BLACK, width=5)

    # Клюв
    beak_y = cy + int(10*s) + wobble
    draw.polygon([
        (cx,              beak_y),
        (cx - int(14*s), beak_y + int(22*s)),
        (cx + int(14*s), beak_y + int(22*s)),
    ], fill=ORANGE)

    # Ушки-рожки (ушная часть совы)
    for ex, side in [(cx - int(80*s), -1), (cx + int(80*s), 1)]:
        ear_y = cy - body_r + int(10*s) + wobble
        draw.polygon([
            (ex,                    ear_y),
            (ex + side*int(30*s),   ear_y - int(50*s)),
            (ex + side*int(10*s),   ear_y + int(20*s)),
        ], fill=GREEN_DARK)

    # Крылья
    wing_y = cy + int(30*s) + wobble
    wing_flap = math.sin(frame * 0.4) * 15 * s
    for side in [-1, 1]:
        wing_pts = [
            (cx + side * int(90*s),    wing_y),
            (cx + side * int(145*s),   wing_y - int(20*s) + wing_flap),
            (cx + side * int(155*s),   wing_y + int(50*s)),
            (cx + side * int(100*s),   wing_y + int(70*s)),
        ]
        draw.polygon(wing_pts, fill=GREEN_DARK)

    # Корона (на всех кадрах)
    crown_y = cy - body_r - int(45*s) + wobble
    crown_pts = [
        (cx - int(50*s), crown_y + int(40*s)),
        (cx - int(50*s), crown_y),
        (cx - int(25*s), crown_y + int(20*s)),
        (cx,             crown_y - int(10*s)),
        (cx + int(25*s), crown_y + int(20*s)),
        (cx + int(50*s), crown_y),
        (cx + int(50*s), crown_y + int(40*s)),
    ]
    draw.polygon(crown_pts, fill=YELLOW, outline=ORANGE, width=3)
    # Камушки в короне
    for gem_x in [cx - int(25*s), cx, cx + int(25*s)]:
        draw.ellipse([gem_x - int(6*s), crown_y + int(5*s),
                      gem_x + int(6*s), crown_y + int(17*s)], fill=RED)


def _stars(draw, frame, density=30):
    """Мерцающие звёздочки/конфетти на фоне."""
    random.seed(42)
    for _ in range(density):
        x = random.randint(0, W)
        y = random.randint(0, H)
        size = random.randint(4, 14)
        alpha = int(abs(math.sin(frame * 0.2 + random.random() * 6)) * 200 + 55)
        color = random.choice([YELLOW, WHITE, GREEN_LITE, ORANGE])
        draw.ellipse([x-size, y-size, x+size, y+size], fill=(*color[:3], alpha))


def _price_tag(draw, x, y, price, store, crossed=False, scale=1.0):
    """Рисует ценник."""
    s = scale
    tw, th = int(260*s), int(100*s)
    # Фон ценника
    draw.rounded_rectangle([x, y, x+tw, y+th], radius=15, fill=WHITE, outline=GREEN, width=3)
    # Магазин
    draw.text((x + int(14*s), y + int(10*s)), store,
              font=_font(int(26*s)), fill=GRAY)
    # Цена
    price_text = f"{price:,}₸".replace(",", " ")
    draw.text((x + int(14*s), y + int(40*s)), price_text,
              font=_font(int(40*s), bold=True), fill=BLACK if not crossed else GRAY)
    if crossed:
        # Зачёркнутая цена (дорого)
        py = y + int(60*s)
        draw.line([x + int(10*s), py, x + tw - int(10*s), py], fill=RED, width=4)


def _make_frame(scene: int, t: float, total_scenes: int) -> Image.Image:
    """
    Генерирует один кадр.
    scene: номер сцены (0..N)
    t: прогресс внутри сцены 0.0..1.0
    """
    frame_idx = int(t * 30)
    img = Image.new("RGB", (W, H), CREAM)
    draw = ImageDraw.Draw(img, "RGBA")

    if scene == 0:
        # ── Сцена 0: "Цены в 2026 😭" ───────────────────────────────
        # Фон — красноватый градиент настроения
        for i in range(H):
            r = int(255 - i * 0.05)
            g = int(240 - i * 0.06)
            b = int(230 - i * 0.06)
            draw.line([(0, i), (W, i)], fill=(r, g, b))

        # Заголовок
        _center_text(draw, "ЦЕНЫ В 2026...", 120, _font(90, bold=True), RED, shadow=True)

        # Анимированные ценники летящие вверх
        for i, (name, price) in enumerate([("Яйца 10шт", 1200), ("Молоко 1л", 750), ("Мясо 1кг", 4500)]):
            fly_y = int(400 + i * 240 - t * 80)
            _price_tag(draw, W//2 - 130, fly_y, price, name, crossed=False, scale=1.0)

        # Стрелка вверх (цены растут)
        arrow_x = W - 140
        for i in range(3):
            ay = int(600 + i * 120 - t * 60)
            draw.polygon([
                (arrow_x,        ay + 50),
                (arrow_x - 40,   ay + 90),
                (arrow_x - 15,   ay + 90),
                (arrow_x - 15,   ay + 140),
                (arrow_x + 15,   ay + 140),
                (arrow_x + 15,   ay + 90),
                (arrow_x + 40,   ay + 90),
            ], fill=(*RED, int(180 - i*40)))

        # Минька шокированный внизу
        scale = 0.7 + t * 0.05
        _draw_mascot(draw, W//2, int(1500 + t * 20), scale=scale, frame=frame_idx, mood="shocked")
        _center_text(draw, "😱 ЧТО ПРОИСХОДИТ?!", 1750, _font(60, bold=True), RED, shadow=True)

    elif scene == 1:
        # ── Сцена 1: "НО ПОДОЖДИ... МИНЬКА СПЕШИТ НА ПОМОЩЬ!" ──────
        # Зелёный фон летит
        flash = int(abs(math.sin(t * math.pi * 3)) * 60)
        bg = (34 + flash, 139 + flash//2, 87 + flash//3)
        draw.rectangle([0, 0, W, H], fill=bg)

        # Лучи света из центра
        cx_r, cy_r = W//2, H//2
        for angle in range(0, 360, 20):
            rad = math.radians(angle + t * 180)
            x2 = cx_r + math.cos(rad) * 900
            y2 = cy_r + math.sin(rad) * 1100
            draw.line([(cx_r, cy_r), (int(x2), int(y2))],
                      fill=(*YELLOW, 60), width=30)

        # Минька летит (появляется снизу)
        entry_y = int(H - (H - 750) * min(t * 1.8, 1.0))
        _draw_mascot(draw, W//2, entry_y, scale=1.1, frame=frame_idx, has_cape=True, mood="happy")

        # Текст
        if t > 0.3:
            alpha = min(int((t - 0.3) / 0.3 * 255), 255)
            _center_text(draw, "✨ МИНЬКА ✨", 200, _font(100, bold=True), YELLOW, shadow=True)
            _center_text(draw, "СПЕШИТ НА ПОМОЩЬ!", 320, _font(65, bold=True), WHITE, shadow=True)

        # Звёзды
        _stars(draw, frame_idx, density=20)

    elif scene == 2:
        # ── Сцена 2: Сравнение цен (Минька показывает) ─────────────
        draw.rectangle([0, 0, W, H], fill=(245, 255, 245))

        # Полосатый фон
        for i in range(0, H, 80):
            draw.rectangle([0, i, W, i+40], fill=(230, 248, 235))

        _center_text(draw, "СМОТРИ СКОЛЬКО", 80, _font(68, bold=True), GREEN_DARK, shadow=True)
        _center_text(draw, "ТЫ ПЕРЕПЛАЧИВАЕШЬ!", 160, _font(68, bold=True), RED, shadow=True)

        # Ценники: магазин vs minprice
        items = [
            ("Яйца 10шт",  "Магнум",    1190, "minprice.kz",  780),
            ("Молоко 1л",  "Airba",      720, "minprice.kz",  450),
            ("Мясо 1кг",   "Small",     4200, "minprice.kz", 3100),
        ]
        for i, (name, store1, price1, store2, price2) in enumerate(items):
            show = t > i * 0.25  # появляются по очереди
            if not show:
                continue
            row_y = 300 + i * 350
            item_alpha = min(int((t - i*0.25) / 0.2 * 255), 255)

            # Название товара
            draw.text((60, row_y), name, font=_font(44, bold=True), fill=BLACK)

            # Дорого (зачёркнутый)
            _price_tag(draw, 60, row_y + 55, price1, store1, crossed=True, scale=0.95)
            draw.text((330, row_y + 95), "VS", font=_font(44, bold=True), fill=GRAY)

            # Дёшево (minprice)
            _price_tag(draw, 420, row_y + 55, price2, store2, crossed=False, scale=0.95)

            # Экономия
            saved = price1 - price2
            draw.text((730, row_y + 85), f"-{saved}₸ 🎉",
                      font=_font(38, bold=True), fill=GREEN)

        # Минька с указкой
        _draw_mascot(draw, W - 160, 1600, scale=0.65, frame=frame_idx, mood="cool")

    elif scene == 3:
        # ── Сцена 3: Финал — праздник + CTA ─────────────────────────
        # Конфетти фон
        draw.rectangle([0, 0, W, H], fill=GREEN)
        _stars(draw, frame_idx, density=50)

        # Большой Минька в центре
        bounce = abs(math.sin(frame_idx * 0.25)) * 40
        _draw_mascot(draw, W//2, int(800 - bounce), scale=1.3,
                     frame=frame_idx, has_cape=True, mood="happy")

        # Победный текст
        scale_pulse = 1.0 + math.sin(frame_idx * 0.4) * 0.05
        _center_text(draw, "ЭКОНОМЬ", 200, _font(int(110 * scale_pulse), bold=True), YELLOW, shadow=True)
        _center_text(draw, "КАК ПРОФИ!", 330, _font(int(100 * scale_pulse), bold=True), WHITE, shadow=True)

        # CTA
        draw.rounded_rectangle([100, 1350, W-100, 1550],
                                radius=40, fill=YELLOW, outline=ORANGE, width=5)
        _center_text(draw, "🔗 minprice.kz", 1370, _font(72, bold=True), GREEN_DARK)
        _center_text(draw, "Сравни цены бесплатно!", 1465, _font(46), GREEN_DARK)

        # Конфетти разлетается
        random.seed(frame_idx)
        for _ in range(40):
            cx2 = random.randint(0, W)
            cy2 = random.randint(0, H)
            size = random.randint(8, 22)
            color = random.choice([YELLOW, WHITE, ORANGE, RED, (100, 200, 255)])
            angle = random.random() * 360
            draw.ellipse([cx2-size, cy2-size//2, cx2+size, cy2+size//2], fill=color)

        # Минька имя в шапке
        _center_text(draw, "Минька из minprice.kz", 1600, _font(42), WHITE, shadow=True)
        _center_text(draw, "#цены #казахстан #минька #экономия", 1660,
                     _font(36), GREEN_LITE, shadow=False)

    return img


def generate_reel_video(output_path: str | None = None) -> str:
    """
    Создаёт MP4-видео рилс с Минькой.
    Возвращает путь к файлу.
    """
    import imageio

    if output_path is None:
        output_path = str(Path(tempfile.mkdtemp()) / "minka_reel.mp4")

    # Сцены: (scene_id, duration_sec)
    scenes = [
        (0, 2.5),   # Цены растут — шок
        (1, 2.5),   # Минька появляется
        (2, 3.5),   # Сравнение цен
        (3, 3.0),   # Финал + CTA
    ]

    logger.info(f"Генерирую {sum(d for _, d in scenes):.1f}с рилс...")

    writer = imageio.get_writer(
        output_path,
        fps=FPS,
        codec="libx264",
        quality=8,
        macro_block_size=None,
        ffmpeg_params=["-vf", "scale=1080:1920", "-pix_fmt", "yuv420p"],
    )

    for scene_id, duration in scenes:
        n_frames = int(duration * FPS)
        for f in range(n_frames):
            t = f / n_frames
            frame_img = _make_frame(scene_id, t, len(scenes))
            import numpy as np
            writer.append_data(np.array(frame_img))
            if f % (FPS * 2) == 0:
                logger.info(f"  Сцена {scene_id+1}: {f}/{n_frames} кадров")

    writer.close()
    size_mb = Path(output_path).stat().st_size / 1024 / 1024
    logger.info(f"Видео готово: {output_path} ({size_mb:.1f} MB)")
    return output_path


def upload_reel_to_r2(video_path: str) -> str:
    """Загружает MP4 на Cloudflare R2 и возвращает публичный URL."""
    from agent.skills.image_generator import upload_to_r2
    from datetime import datetime

    with open(video_path, "rb") as f:
        video_bytes = f.read()

    date_str = datetime.now().strftime("%Y-%m-%d-%H%M")
    filename = f"reels/minka-{date_str}.mp4"

    # Переопределяем upload_to_r2 для MP4
    import boto3
    from botocore.config import Config

    account_id = os.getenv("CLOUDFLARE_R2_ACCOUNT_ID", "")
    access_key = os.getenv("CLOUDFLARE_R2_ACCESS_KEY", "")
    secret_key = os.getenv("CLOUDFLARE_R2_SECRET_KEY", "")
    bucket     = os.getenv("CLOUDFLARE_R2_BUCKET", "instagram-pics")
    endpoint   = os.getenv("CLOUDFLARE_R2_ENDPOINT",
                           f"https://{account_id}.r2.cloudflarestorage.com")
    public_url = os.getenv("CLOUDFLARE_R2_PUBLIC_URL", "").rstrip("/")

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    s3.put_object(Bucket=bucket, Key=filename, Body=video_bytes, ContentType="video/mp4")

    url = f"{public_url}/{filename}"
    logger.info(f"Рилс загружен на R2: {url}")
    return url
