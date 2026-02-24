import os
import tempfile

from PIL import Image, ImageDraw, ImageFont

from bot.config import logger


def _get_fonts():
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    bold_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
    ]
    font_path = next((f for f in font_paths if os.path.exists(f)), None)
    font_bold = next((f for f in bold_paths if os.path.exists(f)), None)
    try:
        return (
            ImageFont.truetype(font_bold or font_path, 20),
            ImageFont.truetype(font_bold or font_path, 13),
            ImageFont.truetype(font_path or font_bold, 12),
            ImageFont.truetype(font_bold or font_path, 12),
        )
    except Exception as e:
        logger.warning(f"Не удалось загрузить шрифты, используем default: {e}")
        d = ImageFont.load_default()
        return d, d, d, d


def generate_schedule_image(title: str, headers: list, rows: list) -> str:
    DATE_COL_W, NAME_COL_W = 90, 125
    ROW_H, HEADER_H, TITLE_H, PAD = 48, 55, 65, 20
    total_w = PAD * 2 + DATE_COL_W + NAME_COL_W * len(headers)
    total_h = PAD * 2 + TITLE_H + HEADER_H + ROW_H * len(rows)
    img = Image.new("RGB", (total_w, total_h), (15, 15, 25))
    draw = ImageDraw.Draw(img)
    font_title, font_header, font_cell, font_date = _get_fonts()

    def rr(x1, y1, x2, y2, r, fill):
        draw.rectangle([x1 + r, y1, x2 - r, y2], fill=fill)
        draw.rectangle([x1, y1 + r, x2, y2 - r], fill=fill)
        for cx, cy in [(x1, y1), (x2 - 2 * r, y1), (x1, y2 - 2 * r), (x2 - 2 * r, y2 - 2 * r)]:
            draw.ellipse([cx, cy, cx + 2 * r, cy + 2 * r], fill=fill)

    def ct(text, x, y, w, h, font, color):
        bb = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        draw.text((x + (w - tw) // 2, y + (h - th) // 2), text, font=font, fill=color)

    ct(title, PAD, PAD, total_w - PAD * 2, TITLE_H, font_title, (130, 170, 255))

    y = PAD + TITLE_H
    x = PAD
    rr(x + 1, y + 2, x + DATE_COL_W - 1, y + HEADER_H - 2, 8, (35, 35, 60))
    ct("Дата", x, y, DATE_COL_W, HEADER_H, font_header, (130, 170, 255))
    x += DATE_COL_W
    for name in headers:
        rr(x + 1, y + 2, x + NAME_COL_W - 1, y + HEADER_H - 2, 8, (35, 35, 60))
        ct(name, x, y, NAME_COL_W, HEADER_H, font_header, (130, 170, 255))
        x += NAME_COL_W

    for ri, row in enumerate(rows):
        y = PAD + TITLE_H + HEADER_H + ri * ROW_H
        x = PAD
        rr(x + 1, y + 2, x + DATE_COL_W - 1, y + ROW_H - 2, 6, (28, 28, 48))
        ct(f"{row['date']} {row['day']}", x, y, DATE_COL_W, ROW_H, font_date, (200, 200, 220))
        x += DATE_COL_W
        for val in row["values"]:
            if val == "Выходной":
                rr(x + 1, y + 2, x + NAME_COL_W - 1, y + ROW_H - 2, 6, (65, 25, 25))
                ct("Вых", x, y, NAME_COL_W, ROW_H, font_cell, (235, 100, 100))
            else:
                rr(x + 1, y + 2, x + NAME_COL_W - 1, y + ROW_H - 2, 6, (25, 70, 50))
                ct(val.replace(":00", "").replace(" ", ""), x, y, NAME_COL_W, ROW_H, font_cell, (100, 235, 160))
            x += NAME_COL_W

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path
