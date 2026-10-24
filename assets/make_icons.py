from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 256
LINE_YS = (67, 87, 107, 127, 147, 167)
HIGHLIGHT_Y = 107


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16, 52, 239, 215), radius=12, fill="#2f4b7c")
    for left in (28, 130):
        d.rectangle((left, 40, left + 97, 203), fill="#ffffff", outline="#c9d1dc", width=2)
        for y in LINE_YS:
            width = 50 if y == LINE_YS[-1] else 70
            if left == 130 and y == HIGHLIGHT_Y:
                d.rounded_rectangle((138, 98, 221, 121), radius=4, fill="#ffe58a")
                color = "#4a5568"
            else:
                color = "#9aa5b4"
            d.rectangle((left + 14, y, left + 14 + width - 1, y + 5), fill=color)
    return img


def main() -> None:
    img = draw_icon()
    png_path = ROOT / "speedreader" / "assets" / "icon.png"
    ico_path = ROOT / "assets" / "icon.ico"
    png_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(png_path)
    img.save(ico_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {png_path} and {ico_path}")


if __name__ == "__main__":
    main()
