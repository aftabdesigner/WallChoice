"""Shared fixtures: real images and videos, built in memory."""

import itertools
import os
import shutil
import subprocess
import tempfile
from io import BytesIO

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image, ImageDraw

from wallpapers import media
from wallpapers.models import Category, Wallpaper

TEMP_MEDIA = tempfile.mkdtemp(prefix="wallchoice-test-media-")

TEST_SETTINGS = dict(
    MEDIA_ROOT=TEMP_MEDIA,
    USE_LOCAL_MEDIA=True,
    # STORAGES is built once at settings import, so USE_LOCAL_MEDIA alone does
    # not undo an object-storage .env - without this the suite would upload.
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    ALLOWED_HOSTS=["*"],
    API_CACHE_SECONDS=0,
    SCORES_RECALC_ASYNC=False,
    # WhiteNoise warns when collectstatic has not run; tests never serve static.
    MIDDLEWARE=[m for m in settings.MIDDLEWARE if "whitenoise" not in m],
)

use_test_media = override_settings(**TEST_SETTINGS)

HOST = "192.168.18.8:8000"
_colours = itertools.count(1)


def clear_media():
    for entry in os.listdir(TEMP_MEDIA):
        shutil.rmtree(os.path.join(TEMP_MEDIA, entry), ignore_errors=True)


def image_bytes(width=540, height=1200, fmt="PNG", colour=None, alpha=False):
    """A unique, real image (each call differs, so duplicate checks pass)."""
    n = next(_colours)
    colour = colour or ((n * 47) % 256, (n * 91) % 256, (n * 13) % 256)
    mode = "RGBA" if alpha else "RGB"
    fill = (*colour, 255) if alpha else colour
    image = Image.new(mode, (width, height), (0, 0, 0, 0) if alpha else fill)
    draw = ImageDraw.Draw(image)
    if alpha:
        draw.ellipse((width // 4, height // 4, width * 3 // 4, height * 3 // 4), fill=fill)
    else:
        draw.rectangle((0, 0, width // 3, height // 5), fill=(255 - colour[0], 255 - colour[1], n % 256))
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def image_upload(name="shot.png", **kwargs):
    fmt = "JPEG" if name.lower().endswith((".jpg", ".jpeg")) else (
        "WEBP" if name.lower().endswith(".webp") else "PNG"
    )
    return SimpleUploadedFile(name, image_bytes(fmt=fmt, **kwargs), content_type=f"image/{fmt.lower()}")


_videos = {}


def video_bytes(variant=0, with_audio=True):
    """A real 1-second H.264 mp4 with an audio track and the index at the end."""
    key = (variant, with_audio)
    if key not in _videos:
        handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        handle.close()
        colour = ["red", "blue", "green", "yellow", "purple", "orange", "white", "gray"][variant % 8]
        args = [
            media.ffmpeg_executable(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=216x{384 + 2 * (variant // 8)}:d=1:r=24",
        ]
        if with_audio:
            args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest"]
        if with_audio:
            args += ["-c:a", "aac"]
        args += [handle.name]
        subprocess.run(args, check=True, timeout=60)
        with open(handle.name, "rb") as output:
            _videos[key] = output.read()
        os.unlink(handle.name)
    return _videos[key]


def video_upload(name="clip.mp4", variant=0):
    return SimpleUploadedFile(name, video_bytes(variant), content_type="video/mp4")


def make_category(name, wallpaper_type="image", kind="standard", **kwargs):
    if kind != "standard":
        # Migration 0009 already made Trending and Popular for each type.
        category = Category.objects.get(feature="wallpaper", wallpaper_type=wallpaper_type, kind=kind)
        for key, value in {"name": name, **kwargs}.items():
            setattr(category, key, value)
        category.save()
        return category
    return Category.objects.create(name=name, wallpaper_type=wallpaper_type, kind=kind, **kwargs)


_video_variants = itertools.count(0)


def make_wallpaper(wallpaper_type="image", category=None, name=None, **kwargs):
    """Create a fully processed wallpaper the way the admin would."""
    if wallpaper_type in ("live",):
        variant = next(_video_variants)
        upload = video_upload(name or f"clip{variant}.mp4", variant=variant)
    else:
        upload = image_upload(name or "shot.png")
    wallpaper = Wallpaper(type=wallpaper_type, category=category, file=upload, **kwargs)
    wallpaper.full_clean()
    wallpaper.save()
    return wallpaper


_audio = {}
_audio_variants = itertools.count(0)


def audio_bytes(seconds=5, frequency=None, fmt="mp3"):
    """A real audio file: a sine tone (each call a new pitch unless given)."""
    frequency = frequency or 300 + 20 * next(_audio_variants)
    key = (seconds, frequency, fmt)
    if key not in _audio:
        handle = tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False)
        handle.close()
        codec = ["-c:a", "libmp3lame", "-b:a", "128k"] if fmt == "mp3" else []
        subprocess.run(
            [media.ffmpeg_executable(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}",
             *codec, handle.name],
            check=True, timeout=60,
        )
        with open(handle.name, "rb") as output:
            _audio[key] = output.read()
        os.unlink(handle.name)
    return _audio[key]


def audio_upload(name="tone.mp3", seconds=5, frequency=None):
    fmt = os.path.splitext(name)[1].lstrip(".") or "mp3"
    return SimpleUploadedFile(name, audio_bytes(seconds, frequency, fmt), content_type="audio/mpeg")


def lottie_bytes(width=360, height=640, frames=60, extra_layers=0, label="pulse"):
    import json

    layer = {"ddd": 0, "ind": 1, "ty": 4, "nm": label, "ks": {}, "shapes": [], "ip": 0, "op": frames}
    document = {
        "v": "5.7.4", "fr": 30, "ip": 0, "op": frames, "w": width, "h": height,
        "nm": label, "layers": [layer] + [dict(layer, ind=i + 2) for i in range(extra_layers)],
    }
    return json.dumps(document, indent=2).encode()


def lottie_upload(name="halo.json", **kwargs):
    return SimpleUploadedFile(name, lottie_bytes(**kwargs), content_type="application/json")


def feature_category(name, feature, kind="standard", **kwargs):
    if kind != "standard":
        category = Category.objects.get(feature=feature, kind=kind)
        for key, value in {"name": name, **kwargs}.items():
            setattr(category, key, value)
        category.save()
        return category
    return Category.objects.create(name=name, feature=feature, kind=kind, **kwargs)


def make_ringtone(category, name=None, **kwargs):
    from wallpapers.models import Ringtone

    ringtone = Ringtone(
        category=category,
        audio=audio_upload(name or "tone.mp3"),
        artwork=image_upload("cover.png", width=900, height=900),
        **kwargs,
    )
    ringtone.full_clean()
    ringtone.save()
    return ringtone


def make_charging(category, animation=None, **kwargs):
    from wallpapers.models import ChargingAnimation

    if animation is None:
        animation = lottie_upload(label=f"anim{next(_colours)}")
        kwargs.setdefault("thumbnail", image_upload("card.png"))
    item = ChargingAnimation(category=category, animation=animation, **kwargs)
    item.full_clean()
    item.save()
    return item