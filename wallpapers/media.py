"""Media processing: every rendition the app loads is produced here.

Uploads arrive at whatever size the designer exported. The app needs a fixed
set of files per wallpaper (see "Media requirements" in the API spec), each
with a hard byte budget, so the work is always "resize, then compress until it
fits" rather than a single fixed quality.

Videos go through ffmpeg. ``imageio-ffmpeg`` ships a static binary as a wheel,
so the same code runs on a developer's Windows box and in the slim Docker
image without installing anything system-wide.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

KB = 1024
MB = 1024 * KB

# --- the targets from the spec ----------------------------------------------

THUMBNAIL_WIDTH, THUMBNAIL_MAX_BYTES = 400, 60 * KB
PREVIEW_WIDTH, PREVIEW_MAX_BYTES = 1080, 400 * KB
HD_WIDTH, HD_MAX_BYTES = 1080, int(1.5 * MB)
UHD_WIDTH, UHD_MAX_BYTES = 2160, 6 * MB
LAYER_WIDTH, LAYER_MAX_BYTES = 1080, 1 * MB
PHASE_WIDTH, PHASE_MAX_BYTES = 1080, int(1.5 * MB)
COVER_SIZE, COVER_MAX_BYTES = (600, 375), 80 * KB

ARTWORK_SIZE, ARTWORK_THUMB_SIZE, ARTWORK_MAX_BYTES = 800, 200, 150 * KB
AUDIO_MAX_SECONDS, AUDIO_MIN_SECONDS = 40, 3
AUDIO_BITRATE = "160k"  # 40 s at 160 kbit/s is ~800 KB, inside the 1 MB budget
AUDIO_MAX_BYTES = 1 * MB
SOUND_MAX_SECONDS = 10
LOTTIE_MAX_BYTES = 300 * KB
ANIMATION_VIDEO_MAX_BYTES = 5 * MB
WAVEFORM_BARS = 44

VIDEO_EXTENSIONS = {"mp4"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "aac", "wav", "ogg", "flac"}

# Tried best first; the first encoding under budget wins.
_QUALITIES = (86, 80, 74, 68, 60, 52, 44, 36)
FFMPEG_TIMEOUT_SECONDS = 120


@dataclass
class Encoded:
    content: bytes
    width: int
    height: int

    @property
    def info(self) -> dict:
        return {"width": self.width, "height": self.height, "size_bytes": len(self.content)}

    def as_file(self, name: str) -> ContentFile:
        return ContentFile(self.content, name=name)


@dataclass
class VideoInfo:
    width: int
    height: int
    duration_ms: int
    size_bytes: int

    @property
    def info(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
            "size_bytes": self.size_bytes,
        }


def extension_of(name: str) -> str:
    return os.path.splitext(name or "")[1].lstrip(".").lower()


def is_video_name(name: str) -> bool:
    return extension_of(name) in VIDEO_EXTENSIONS


# --- files on disk ------------------------------------------------------------


@contextmanager
def local_path(fileobj, suffix=""):
    """Yield a filesystem path holding ``fileobj``'s bytes.

    ffmpeg needs a real path. A large admin upload already has one
    (TemporaryUploadedFile); anything else is spooled to a temp file, which is
    removed afterwards. ``delete=False`` because Windows will not let a second
    process open a NamedTemporaryFile that is still open.
    """
    existing = getattr(fileobj, "temporary_file_path", None)
    if callable(existing):
        yield existing()
        return
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with handle:
            if hasattr(fileobj, "seek"):
                fileobj.seek(0)
            if hasattr(fileobj, "chunks"):
                for chunk in fileobj.chunks():
                    handle.write(chunk)
            else:
                shutil.copyfileobj(fileobj, handle, length=MB)
        yield handle.name
    finally:
        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0)
            except (OSError, ValueError):
                pass
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def sha256_of_path(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(MB), b""):
            digest.update(block)
    return digest.hexdigest()


# --- images -------------------------------------------------------------------


def open_image(source) -> Image.Image:
    """Load an image fully, upright, as RGB or RGBA (first frame of a gif)."""
    try:
        if hasattr(source, "seek"):
            source.seek(0)
        with Image.open(source) as image:
            image.seek(0)
            image.load()
            image = ImageOps.exif_transpose(image)
            has_alpha = "A" in image.getbands() or (
                image.mode == "P" and "transparency" in image.info
            )
            return image.convert("RGBA" if has_alpha else "RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError(f"This file could not be read as an image ({exc}).") from exc
    finally:
        if hasattr(source, "seek"):
            try:
                source.seek(0)
            except (OSError, ValueError):
                pass


def fit_width(image: Image.Image, width: int) -> Image.Image:
    """Scale down to ``width`` keeping the aspect ratio. Never upscales."""
    if image.width <= width:
        return image
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.LANCZOS)


def crop_to_ratio(image: Image.Image, ratio_w: int, ratio_h: int) -> Image.Image:
    """Centre-crop to the given aspect ratio."""
    target = ratio_w / ratio_h
    current = image.width / image.height
    if abs(current - target) < 0.01:
        return image
    if current > target:
        new_width = max(1, round(image.height * target))
        left = (image.width - new_width) // 2
        return image.crop((left, 0, left + new_width, image.height))
    new_height = max(1, round(image.width / target))
    top = (image.height - new_height) // 2
    return image.crop((0, top, image.width, top + new_height))


def _webp(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="WEBP", quality=quality, method=4)
    return buffer.getvalue()


def encode_under(image: Image.Image, max_bytes: int) -> Encoded:
    """WebP-encode, lowering quality and then size until under ``max_bytes``."""
    current = image
    for _ in range(8):
        for quality in _QUALITIES:
            data = _webp(current, quality)
            if len(data) <= max_bytes:
                return Encoded(data, current.width, current.height)
        # Even the lowest quality is too big - this is noise or a huge frame.
        new_size = (max(1, round(current.width * 0.85)), max(1, round(current.height * 0.85)))
        current = current.resize(new_size, Image.LANCZOS)
    return Encoded(data, current.width, current.height)


def dominant_color(image: Image.Image) -> str:
    """The most common colour among a 5-colour reduction, as #RRGGBB."""
    small = image.convert("RGB").resize((64, 64), Image.BILINEAR)
    reduced = small.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    palette = reduced.getpalette() or []
    counts = sorted(reduced.getcolors() or [], reverse=True)
    if not counts or not palette:
        r, g, b = small.resize((1, 1)).getpixel((0, 0))
    else:
        index = counts[0][1]
        r, g, b = palette[index * 3 : index * 3 + 3]
    return f"#{r:02X}{g:02X}{b:02X}"


def build_wallpaper_images(image: Image.Image, *, with_hd=True) -> dict:
    """Thumbnail, preview, hd and (when the source is big enough) uhd.

    Returns ``{name: Encoded | None}`` plus ``dominant_color``.
    """
    rgb = image if image.mode == "RGB" else _flatten(image)
    renditions = {
        "thumbnail": encode_under(
            fit_width(crop_to_ratio(rgb, 9, 16), THUMBNAIL_WIDTH), THUMBNAIL_MAX_BYTES
        ),
        "preview": encode_under(fit_width(rgb, PREVIEW_WIDTH), PREVIEW_MAX_BYTES),
        "hd": None,
        "uhd": None,
    }
    if with_hd:
        renditions["hd"] = encode_under(fit_width(rgb, HD_WIDTH), HD_MAX_BYTES)
        if rgb.width >= UHD_WIDTH:
            renditions["uhd"] = encode_under(fit_width(rgb, UHD_WIDTH), UHD_MAX_BYTES)
    return {"renditions": renditions, "dominant_color": dominant_color(rgb)}


def build_phase_image(image: Image.Image) -> Encoded:
    return encode_under(fit_width(_flatten(image), PHASE_WIDTH), PHASE_MAX_BYTES)


def build_layer_image(image: Image.Image) -> Encoded:
    # Alpha is kept: every layer above the background must be see-through.
    return encode_under(fit_width(image, LAYER_WIDTH), LAYER_MAX_BYTES)


def build_cover(image: Image.Image) -> Encoded:
    cropped = crop_to_ratio(_flatten(image), *COVER_SIZE)
    if cropped.width > COVER_SIZE[0]:
        cropped = cropped.resize(COVER_SIZE, Image.LANCZOS)
    return encode_under(cropped, COVER_MAX_BYTES)


def composite_layers(images) -> Image.Image:
    """Flatten parallax layers (back to front) into one still for the cards."""
    images = list(images)
    base = images[0].convert("RGBA")
    for layer in images[1:]:
        layer = layer.convert("RGBA")
        if layer.size != base.size:
            layer = layer.resize(base.size, Image.LANCZOS)
        base = Image.alpha_composite(base, layer)
    return base.convert("RGB")


def _flatten(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (0, 0, 0))
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


# --- video ----------------------------------------------------------------------


def ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - any failure means "use the system one"
        found = shutil.which("ffmpeg")
        if found:
            return found
    raise ValidationError("ffmpeg is not available on the server, so videos cannot be processed.")


def _run_ffmpeg(args, *, capture_stdout=False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [ffmpeg_executable(), "-hide_banner", "-nostdin", *args],
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("Processing this video took too long.") from exc


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_STREAM = re.compile(r"Stream #\d+:\d+.*?: Video: .*?(\d{2,5})x(\d{2,5})")
_ROTATION = re.compile(r"rotat(?:e|ion)\s*(?:of)?\s*:?\s*(-?\d+(?:\.\d+)?)")


def probe_video(path: str) -> VideoInfo:
    """Width, height and duration, read from ffmpeg's stream summary."""
    result = _run_ffmpeg(["-i", path])
    text = result.stderr.decode("utf-8", "replace")
    stream = _VIDEO_STREAM.search(text)
    duration = _DURATION.search(text)
    if not stream or not duration:
        raise ValidationError("This file could not be read as a video.")
    width, height = int(stream.group(1)), int(stream.group(2))
    rotation = _ROTATION.search(text)
    if rotation and round(abs(float(rotation.group(1)))) % 180 == 90:
        width, height = height, width
    hours, minutes, seconds = duration.groups()
    duration_ms = round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)
    return VideoInfo(width, height, duration_ms, os.path.getsize(path))


def first_frame(path: str) -> Image.Image:
    """The first frame as a PIL image - the live wallpaper's poster."""
    result = _run_ffmpeg(
        ["-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_stdout=True,
    )
    if result.returncode != 0 or not result.stdout:
        raise ValidationError("Could not read a frame from this video.")
    return open_image(BytesIO(result.stdout))


def build_square(image: Image.Image, size: int, max_bytes: int) -> Encoded:
    """Centre-cropped square artwork (ringtone covers)."""
    square = crop_to_ratio(_flatten(image), 1, 1)
    if square.width > size:
        square = square.resize((size, size), Image.LANCZOS)
    return encode_under(square, max_bytes)


# --- audio ----------------------------------------------------------------------


@dataclass
class AudioInfo:
    format: str
    duration_ms: int
    size_bytes: int

    @property
    def info(self) -> dict:
        return {"format": self.format, "duration_ms": self.duration_ms, "size_bytes": self.size_bytes}


def probe_duration_ms(path: str, *, kind="audio") -> int:
    text = _run_ffmpeg(["-i", path]).stderr.decode("utf-8", "replace")
    duration = _DURATION.search(text)
    if not duration or (kind == "audio" and "Audio:" not in text):
        raise ValidationError("This file could not be read as audio.")
    hours, minutes, seconds = duration.groups()
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


def process_audio(path: str, max_seconds: int = AUDIO_MAX_SECONDS) -> tuple[bytes, AudioInfo]:
    """Re-encode to MP3 at a steady loudness, trimmed to ``max_seconds``.

    Loudness is normalised to about -14 LUFS (the spec's target) so every
    ringtone plays at the same volume. Anything longer than the limit is cut
    with a one-second fade-out rather than rejected.
    """
    source_ms = probe_duration_ms(path)
    if source_ms < AUDIO_MIN_SECONDS * 1000:
        raise ValidationError(f"Audio must be at least {AUDIO_MIN_SECONDS} seconds long.")
    filters = ["loudnorm=I=-14:TP=-1.5:LRA=11"]
    trimmed = source_ms > max_seconds * 1000
    if trimmed:
        filters.append(f"afade=t=out:st={max_seconds - 1}:d=1")
    handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    handle.close()
    try:
        args = ["-y", "-i", path, "-vn", "-map", "0:a:0"]
        if trimmed:
            args += ["-t", str(max_seconds)]
        args += ["-af", ",".join(filters), "-ac", "2", "-ar", "44100",
                 "-c:a", "libmp3lame", "-b:a", AUDIO_BITRATE, handle.name]
        result = _run_ffmpeg(args)
        if result.returncode != 0 or os.path.getsize(handle.name) == 0:
            raise ValidationError("This audio file could not be converted.")
        with open(handle.name, "rb") as output:
            data = output.read()
        duration_ms = probe_duration_ms(handle.name)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
    return data, AudioInfo("mp3", duration_ms, len(data))


def waveform(path: str, bars: int = WAVEFORM_BARS) -> list:
    """``bars`` loudness values from 0 to 100, drawn by the app's player."""
    from array import array

    result = _run_ffmpeg(
        ["-i", path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_stdout=True
    )
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if not samples:
        return [0] * bars
    size = max(1, len(samples) // bars)
    levels = []
    for index in range(bars):
        chunk = samples[index * size : (index + 1) * size]
        if not chunk:
            levels.append(0.0)
            continue
        levels.append((sum(s * s for s in chunk[::4]) / len(chunk[::4])) ** 0.5)
    peak = max(levels) or 1.0
    return [round(level / peak * 100) for level in levels]


# --- lottie ---------------------------------------------------------------------


def process_lottie(data: bytes) -> tuple[bytes, dict]:
    """Validate a Lottie JSON animation and minify it. Returns (bytes, info)."""
    import json

    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("This file is not valid JSON.") from exc
    if not isinstance(document, dict) or not {"v", "layers", "w", "h"} <= set(document):
        raise ValidationError("This JSON is not a Lottie animation (missing v, w, h or layers).")
    minified = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()
    if len(minified) > LOTTIE_MAX_BYTES:
        raise ValidationError(
            f"Lottie files must be at most {LOTTIE_MAX_BYTES // KB} KB "
            f"(this one is {len(minified) // KB} KB after minifying)."
        )
    frame_rate = float(document.get("fr") or 0)
    frames = float(document.get("op", 0)) - float(document.get("ip", 0))
    info = {
        "width": int(document["w"]),
        "height": int(document["h"]),
        "duration_ms": round(frames / frame_rate * 1000) if frame_rate > 0 and frames > 0 else None,
        "size_bytes": len(minified),
    }
    return minified, info


def optimise_video(path: str) -> bytes | None:
    """Drop the audio track and move the index to the front ("faststart").

    Stream copy only - no re-encode - so this takes well under a second and
    never changes picture quality. Returns None if ffmpeg cannot remux the
    file, in which case the original is kept.
    """
    handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    handle.close()
    try:
        result = _run_ffmpeg(
            ["-y", "-i", path, "-map", "0:v:0", "-c", "copy", "-an",
             "-movflags", "+faststart", handle.name]
        )
        if result.returncode != 0 or os.path.getsize(handle.name) == 0:
            return None
        with open(handle.name, "rb") as output:
            return output.read()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
