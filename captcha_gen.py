import io
import math
import random
from PIL import Image, ImageDraw, ImageFont


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try to load a bold TTF font; fall back to default."""
    paths = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default(size=size)


def generate_captcha_image() -> tuple[bytes, int, list[int]]:
    """
    Generate a captcha image with a math question.
    Returns (png_bytes, correct_answer, four_options).
    """
    op = random.choice(["+", "-", "×"])
    if op == "+":
        a, b = random.randint(1, 50), random.randint(1, 50)
        answer = a + b
        expr = f"{a} + {b}"
    elif op == "-":
        a = random.randint(10, 60)
        b = random.randint(1, a)
        answer = a - b
        expr = f"{a} - {b}"
    else:
        a, b = random.randint(2, 12), random.randint(2, 12)
        answer = a * b
        expr = f"{a} × {b}"

    # Build wrong options
    wrong: set[int] = set()
    while len(wrong) < 3:
        diff = random.randint(1, 10) * random.choice([-1, 1])
        w = answer + diff
        if w != answer and w >= 0 and w not in wrong:
            wrong.add(w)
    options = list(wrong) + [answer]
    random.shuffle(options)

    # ─── Draw image ───
    W, H = 300, 120
    bg_color = (random.randint(230, 255), random.randint(230, 255), random.randint(230, 255))
    img = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(img)

    # Noise: random lines
    for _ in range(random.randint(6, 12)):
        x1, y1 = random.randint(0, W), random.randint(0, H)
        x2, y2 = random.randint(0, W), random.randint(0, H)
        color = (random.randint(150, 210), random.randint(150, 210), random.randint(150, 210))
        draw.line([(x1, y1), (x2, y2)], fill=color, width=random.randint(1, 2))

    # Noise: random dots
    for _ in range(random.randint(80, 150)):
        x, y = random.randint(0, W - 1), random.randint(0, H - 1)
        color = (random.randint(100, 200), random.randint(100, 200), random.randint(100, 200))
        draw.ellipse([(x, y), (x + 2, y + 2)], fill=color)

    # Draw the math expression character by character with random offsets & rotation
    text = f"{expr} = ?"
    font = _get_font(38)
    small_font = _get_font(16)

    # Measure total width to center
    total_w = 0
    char_sizes = []
    for ch in text:
        bbox = font.getbbox(ch)
        cw = bbox[2] - bbox[0] + random.randint(2, 6)
        char_sizes.append(cw)
        total_w += cw

    x_start = (W - total_w) // 2
    x_cursor = x_start

    for i, ch in enumerate(text):
        # Random color for each character
        r = random.randint(0, 100)
        g = random.randint(0, 100)
        b = random.randint(0, 100)

        y_off = random.randint(-8, 8)

        # Create a small image for this char and rotate it slightly
        char_img = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((10, 5), ch, font=font, fill=(r, g, b, 255))
        angle = random.uniform(-15, 15)
        char_img = char_img.rotate(angle, expand=False, resample=Image.BICUBIC)

        # Paste onto main image
        paste_y = (H - 60) // 2 + y_off
        img.paste(char_img, (x_cursor - 5, paste_y), char_img)
        x_cursor += char_sizes[i]

    # Noise: arcs / curves
    for _ in range(random.randint(2, 4)):
        x1 = random.randint(0, W // 2)
        x2 = random.randint(W // 2, W)
        ya = random.randint(0, H)
        yb = random.randint(0, H)
        y_top, y_bot = min(ya, yb), max(ya, yb)
        if y_top == y_bot:
            y_bot += 10
        color = (random.randint(100, 180), random.randint(100, 180), random.randint(100, 180))
        draw.arc([(x1, y_top), (x2, y_bot)], 0, 360, fill=color, width=2)

    # "Vote Bot" label
    draw.text((W - 200, H - 18), "@AliensVoteBot", font=small_font,
              fill=(180, 180, 180, 180))

    # Export to bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return buf.getvalue(), answer, options
