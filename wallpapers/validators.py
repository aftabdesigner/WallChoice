"""Upload rules for wallpaper media."""

from django.core.validators import FileExtensionValidator

# Source uploads: images for every still type, mp4 for live and fluid loops.
# gif is still accepted so rows uploaded before Phase 1 stay valid; its first
# frame is what the renditions are made from.
ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif", "mp4"]

validate_media_extension = FileExtensionValidator(allowed_extensions=ALLOWED_EXTENSIONS)

# Thumbnails, posters, covers: still images only.
ALLOWED_THUMBNAIL_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]

validate_thumbnail_extension = FileExtensionValidator(
    allowed_extensions=ALLOWED_THUMBNAIL_EXTENSIONS
)

# Ringtone audio and charging sounds; converted to MP3 on upload.
validate_audio_extension = FileExtensionValidator(
    allowed_extensions=["mp3", "m4a", "aac", "wav", "ogg", "flac"]
)

# Charging animations and their preview loops.
validate_animation_extension = FileExtensionValidator(allowed_extensions=["json", "mp4"])

# 24 Hours phases and parallax layers. Layers need alpha, so png/webp matter.
validate_image_extension = FileExtensionValidator(allowed_extensions=ALLOWED_THUMBNAIL_EXTENSIONS)
