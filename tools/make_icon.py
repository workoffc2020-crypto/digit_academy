"""Builds assets/app.ico (multi-size, circular) and assets/app.png from assets/logo.png. Run: python tools/make_icon.py"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = os.path.join(HERE, "assets")


def main():
    logo = Image.open(os.path.join(A, "logo.png")).convert("RGBA")
    side = max(logo.size)
    sq = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    sq.paste(logo, ((side - logo.width) // 2, (side - logo.height) // 2), logo)
    big = sq.resize((1024, 1024), Image.LANCZOS)
    mask = Image.new("L", (1024, 1024), 0)
    ImageDraw.Draw(mask).ellipse((8, 8, 1016, 1016), fill=255)
    out = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    out.paste(big, (0, 0), mask)
    out.resize((512, 512), Image.LANCZOS).save(os.path.join(A, "app.png"))
    out.save(os.path.join(A, "app.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written")


if __name__ == "__main__":
    main()
