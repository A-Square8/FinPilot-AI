import io
import structlog
import pdfplumber

logger = structlog.get_logger()

async def extract_text_from_pdf(pdf_bytes: bytes) -> str | None:
    """Extract all text from a PDF file in memory using pdfplumber."""
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        text_content = []
        
        # Open PDF from bytes
        with pdfplumber.open(pdf_file) as pdf:
            # We only extract the first 3 pages max to prevent abuse/long processing times
            for page in pdf.pages[:3]:
                page_text = page.extract_text()
                if page_text:
                    text_content.append(page_text)
                    
        extracted_text = "\n".join(text_content).strip()
        logger.info("PDF extraction complete", chars_extracted=len(extracted_text))
        
        return extracted_text if extracted_text else None
    except Exception as e:
        logger.error("PDF extraction failed", error=str(e))
        return None
