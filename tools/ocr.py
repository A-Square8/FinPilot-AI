import io
import structlog
import pytesseract
from PIL import Image, ImageFilter, ImageEnhance
from telegram import PhotoSize

logger = structlog.get_logger()


async def extract_text_from_photo(photo: PhotoSize, bot) -> str | None:
    """Download a Telegram photo, preprocess it, and extract text via Tesseract OCR."""
    try:
        file = await bot.get_file(photo.file_id)
        byte_array = await file.download_as_bytearray()
        image = Image.open(io.BytesIO(bytes(byte_array)))
        processed = _preprocess_image(image)
        text = pytesseract.image_to_string(processed, lang="eng")
        cleaned = text.strip()
        logger.info("OCR extraction complete", chars_extracted=len(cleaned))
        return cleaned if cleaned else None
    except Exception as e:
        logger.error("OCR extraction failed", error=str(e))
        return None


def _preprocess_image(image: Image.Image) -> Image.Image:
    """Convert to grayscale, boost contrast and sharpness for better OCR accuracy."""
    image = image.convert("L")
    image = ImageEnhance.Contrast(image).enhance(2.0)
    image = ImageEnhance.Sharpness(image).enhance(2.0)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    return image
