"""Generate placeholder media in samples/ for each event type.

Writes <type>.jpg (label on a dark frame) and a 3 s <type>.mp4. Frames are drawn
with Pillow and piped as raw video into ffmpeg: the system ffmpeg if present,
else the binary bundled with imageio-ffmpeg. Without either, mp4s are skipped.

    cd backend && uv run --with imageio-ffmpeg python scripts/make_samples.py
"""
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[2] / "samples"
W, H, FPS, SECS = 640, 360, 10, 3
TYPES = {
    "unsafe_lift": (220, 60, 60),
    "no_hardhat": (235, 150, 40),
    "no_vest": (230, 200, 50),
    "restricted_zone": (220, 60, 60),
    "no_hairnet": (235, 150, 40),
    "no_gloves": (230, 200, 50),
}


def font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def frame(label, color, t=0.0):
    img = Image.new("RGB", (W, H), (18, 20, 24))
    d = ImageDraw.Draw(img)
    x = 220 + int(60 * t)
    d.rectangle([x, 90, x + 120, 300], outline=color, width=4)
    d.text((24, 20), "SAESR SAMPLE", font=font(18), fill=(140, 140, 150))
    d.text((24, H - 60), label.upper().replace("_", " "), font=font(34), fill=color)
    d.text((W - 120, 20), f"t={t:.1f}s", font=font(18), fill=(140, 140, 150))
    return img


def ffmpeg_bin():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def main():
    OUT.mkdir(exist_ok=True)
    ff = ffmpeg_bin()
    if not ff:
        print("no ffmpeg found: skipping mp4s")
    for label, color in TYPES.items():
        frame(label, color).save(OUT / f"{label}.jpg", quality=80)
        if not ff:
            continue
        cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-crf", "30", "-movflags", "+faststart",
               str(OUT / f"{label}.mp4")]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for i in range(FPS * SECS):
            p.stdin.write(frame(label, color, i / FPS).tobytes())
        p.stdin.close()
        p.wait()
    print("wrote", sorted(f.name for f in OUT.iterdir()))


if __name__ == "__main__":
    main()
