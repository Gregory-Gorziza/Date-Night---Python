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
    selected = [photo for photo in files if photo and photo.filename]
    if existing_count + len(selected) > MAX_PHOTOS:
        raise ValueError(f"Você pode manter até {MAX_PHOTOS} fotos no perfil.")

    upload_folder = Path(destination)
    upload_folder.mkdir(parents=True, exist_ok=True)
    saved_names = []
    try:
        for photo in selected:
            contents = photo.read(MAX_PHOTO_BYTES + 1)
            if not contents:
                raise ValueError("Um dos arquivos enviados está vazio.")
            if len(contents) > MAX_PHOTO_BYTES:
                raise ValueError("Cada foto deve ter no máximo 5 MB.")

            try:
                with Image.open(BytesIO(contents)) as image:
                    if image.format not in {"JPEG", "PNG", "WEBP"}:
                        raise ValueError("Envie apenas fotos JPG, PNG ou WebP válidas.")
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise ValueError("A resolução de cada foto deve ser menor que 25 megapixels.")
                    image.verify()
                with Image.open(BytesIO(contents)) as image:
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((2400, 2400))
                    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                        foreground = image.convert("RGBA")
                        background = Image.new("RGB", foreground.size, "white")
                        background.paste(foreground, mask=foreground.getchannel("A"))
                        converted = background
                    else:
                        converted = image.convert("RGB")
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValueError("Envie apenas fotos JPG, PNG ou WebP válidas.") from None

            name = f"{uuid4().hex}.jpg"
            converted.save(upload_folder / name, format="JPEG", quality=86, optimize=True)
            saved_names.append(name)
    except Exception:
        delete_uploaded_photos(saved_names, upload_folder)
        raise
    return saved_names


def delete_uploaded_photos(filenames, destination):
    upload_folder = Path(destination)
    for filename in filenames:
        if not PHOTO_NAME_PATTERN.fullmatch(filename):
            continue
        (upload_folder / filename).unlink(missing_ok=True)
