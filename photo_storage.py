from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PHOTOS = 5
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
PHOTO_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.jpg$")


def save_uploaded_photos(files, destination, existing_count=0):
    selected = []
    for photo in files:
        if photo and photo.filename:
            selected.append(photo)
    upload_folder = Path(destination)
    upload_folder.mkdir(parents=True, exist_ok=True)
    saved_names = []
    for photo in selected:
        contents = photo.read()
        with Image.open(BytesIO(contents)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2400, 2400))
            converted = image.convert("RGB")
        name = f"{uuid4().hex}.jpg"
        converted.save(upload_folder / name, format="JPEG", quality=86)
        saved_names.append(name)
    return saved_names


def delete_uploaded_photos(filenames, destination):
    upload_folder = Path(destination)
    for filename in filenames:
        if not PHOTO_NAME_PATTERN.fullmatch(filename):
            continue
        (upload_folder / filename).unlink(missing_ok=True)
