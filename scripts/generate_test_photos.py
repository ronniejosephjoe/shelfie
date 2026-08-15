"""
Generates the synthetic bookshelf photos committed under photos/.

Honest disclosure (also in README): these are generated, not photos of
a real shelf. I don't have camera access or a way to source real
bookshelf photos I'd have rights to commit to a public repo in this
environment. They're built to be genuinely testing -- real catalog
titles/authors rendered as spine text at varying orientation, size,
contrast and legibility -- so the pipeline's detect -> read -> match
path is exercised meaningfully, not just "does the code run." At the
presentation, real shelf photos (handed to us live, and ideally RJ's
own shelf beforehand) are what actually matters; these are the
repo's required "photos you tested with," and what
scripts/bench_pipeline.py's numbers are measured against.

Four images, increasing difficulty:
  01_clean_horizontal.jpg   thick paperbacks, horizontal spine text,
                             high contrast -- the easy case
  02_vertical_spines.jpg    normal upright books, vertical spine text
                             (the common real case) -- also the case
                             TesseractSpineDetector's rotation passes
                             exist for
  03_messy_shelf.jpg        mixed orientation, mixed sizes, one blank
                             spine (no text at all), one low-contrast
                             spine, real catalog titles throughout
  04_low_light_blur.jpg     same layout as 02 but dim + blurred, to
                             exercise the "detected but basically
                             unreadable" path
"""
import random

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# (title, author) pulled straight from catalog.csv so a correct read
# has something real to match against.
BOOKS = [
    ("Dune", "Frank Herbert"),
    ("1984", "George Orwell"),
    ("Circe", "Madeline Miller"),
    ("Gone Girl", "Gillian Flynn"),
    ("The Hobbit", "J.R.R. Tolkien"),
    ("Emma", "Jane Austen"),
    ("The Martian", "Andy Weir"),
    ("Educated", "Tara Westover"),
    ("Neuromancer", "William Gibson"),
    ("Beloved", "Toni Morrison"),
    ("Sapiens", "Yuval Noah Harari"),
    ("Atomic Habits", "James Clear"),
]

random.seed(42)


def _spine_color():
    return tuple(random.randint(50, 210) for _ in range(3))


def _text_color(bg):
    brightness = sum(bg) / 3
    return (20, 20, 20) if brightness > 130 else (245, 245, 245)


def make_horizontal_shelf(path, n=8, w=1400, h=520, blur=0, dim=1.0):
    img = Image.new("RGB", (w, h), (235, 228, 214))  # shelf background
    draw = ImageDraw.Draw(img)
    x = 30
    books = random.sample(BOOKS, k=min(n, len(BOOKS)))
    for title, author in books:
        spine_w = random.randint(140, 210)
        spine_h = random.randint(int(h * 0.6), int(h * 0.85))
        y0 = h - spine_h - 20
        color = _spine_color()
        draw.rectangle([x, y0, x + spine_w, h - 20], fill=color)
        tcolor = _text_color(color)
        font_t = ImageFont.truetype(FONT_BOLD, 26)
        font_a = ImageFont.truetype(FONT_REGULAR, 18)
        draw.text((x + 12, y0 + 20), title, font=font_t, fill=tcolor)
        draw.text((x + 12, y0 + 60), author, font=font_a, fill=tcolor)
        x += spine_w + 14
    return _finish(img, path, blur, dim)


def make_vertical_shelf(path, n=8, w=1400, h=520, blur=0, dim=1.0):
    img = Image.new("RGB", (w, h), (235, 228, 214))
    x = 30
    books = random.sample(BOOKS, k=min(n, len(BOOKS)))
    for title, author in books:
        spine_w = random.randint(70, 100)
        spine_h = random.randint(int(h * 0.65), int(h * 0.88))
        y0 = h - spine_h - 20
        color = _spine_color()
        cv_placeholder = Image.new("RGB", (spine_w, spine_h), color)
        img.paste(cv_placeholder, (x, y0))

        # render title+author sideways (rotated) so it reads bottom-to-top
        text = f"{title}  —  {author}"
        font = ImageFont.truetype(FONT_BOLD, 24)
        tmp = Image.new("RGBA", (spine_h - 20, 40), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(tmp)
        tcolor = _text_color(color)
        tdraw.text((0, 4), text, font=font, fill=tcolor)
        rotated = tmp.rotate(90, expand=True)
        img.paste(rotated, (x + max(0, (spine_w - rotated.width) // 2), y0 + 10), rotated)
        x += spine_w + 12
    return _finish(img, path, blur, dim)


def make_messy_shelf(path, w=1500, h=520, blur=0, dim=1.0):
    img = Image.new("RGB", (w, h), (235, 228, 214))
    draw = ImageDraw.Draw(img)
    x = 30
    books = random.sample(BOOKS, k=9)
    for i, (title, author) in enumerate(books):
        spine_w = random.randint(70, 190)
        spine_h = random.randint(int(h * 0.55), int(h * 0.88))
        y0 = h - spine_h - 20
        color = _spine_color()
        draw.rectangle([x, y0, x + spine_w, h - 20], fill=color)
        tcolor = _text_color(color)

        if i == 3:
            # blank spine on purpose: no text at all -- the local
            # detector should simply not find a region here.
            x += spine_w + 12
            continue
        if i == 5:
            # low-contrast spine on purpose: text present but hard to read.
            tcolor = tuple(min(255, c + random.randint(-15, 15)) for c in color)

        vertical = spine_w < 110
        if vertical:
            text = f"{title} - {author}"
            font = ImageFont.truetype(FONT_BOLD, 20)
            tmp = Image.new("RGBA", (spine_h - 20, 32), (0, 0, 0, 0))
            tdraw = ImageDraw.Draw(tmp)
            tdraw.text((0, 2), text, font=font, fill=tcolor)
            rotated = tmp.rotate(90, expand=True)
            img.paste(rotated, (x + max(0, (spine_w - rotated.width) // 2), y0 + 10), rotated)
        else:
            font_t = ImageFont.truetype(FONT_BOLD, 22)
            font_a = ImageFont.truetype(FONT_REGULAR, 16)
            draw.text((x + 10, y0 + 16), title, font=font_t, fill=tcolor)
            draw.text((x + 10, y0 + 48), author, font=font_a, fill=tcolor)
        x += spine_w + 12
    return _finish(img, path, blur, dim)


def _finish(img, path, blur, dim):
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    if dim != 1.0:
        arr = np.clip(arr.astype(np.float32) * dim, 0, 255).astype(np.uint8)
    if blur:
        arr = cv2.GaussianBlur(arr, (blur, blur), 0)
    cv2.imwrite(path, arr, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"wrote {path}  {arr.shape[1]}x{arr.shape[0]}")


if __name__ == "__main__":
    import os

    out_dir = os.path.join(os.path.dirname(__file__), "..", "photos")
    os.makedirs(out_dir, exist_ok=True)
    make_horizontal_shelf(os.path.join(out_dir, "01_clean_horizontal.jpg"))
    make_vertical_shelf(os.path.join(out_dir, "02_vertical_spines.jpg"))
    make_messy_shelf(os.path.join(out_dir, "03_messy_shelf.jpg"))
    make_vertical_shelf(os.path.join(out_dir, "04_low_light_blur.jpg"), blur=7, dim=0.45)
