import json
import google.generativeai as genai
from config.settings import settings
import structlog

logger = structlog.get_logger()

# Configure Gemini
genai.configure(api_key=settings.gemini_api_key)

# The best 5 fast, text-out models based on your limits list
FALLBACK_MODELS = [
    'gemini-2.5-flash',
    'gemini-3.1-flash-lite',
    'gemini-2.0-flash',
    'gemini-3-flash-preview',
    'gemini-flash-latest'
]

PROMPT_TEMPLATE = """
You are an expert financial assistant. Extract the transaction details from the user's message.
Return ONLY a single valid JSON object. Do not include markdown formatting, backticks, or extra text.

Rules for extraction:
- amount: (float) the final transaction amount. IMPORTANT: If there is a "Total", "Grand Total", or "Amount Paid" in the receipt, use that exact value. Do NOT sum individual items if a total is explicitly stated (to avoid double-counting tax/tips). ONLY sum items if there is no total provided.
- type: (string) exactly one of ["expense", "income", "emi", "investment"].
- category: (string) a short 1-2 word category (e.g., "Food", "Transport", "Salary", "Rent", "Groceries", "Car Loan").
- description: (string) a brief summary of the transaction. If there are multiple items, combine their names (e.g. "Lassi, Samosa").
- txn_date: (string) the date of the transaction in "YYYY-MM-DD" format, if explicitly mentioned in the text. If no date is found, omit this field entirely.
- error: (string) ONLY include this field if the text is completely unrelated to finance (like a random selfie, normal conversation, or garbage text). Example: {"error": "No financial transaction found in this text."}

User Message: "{user_input}"
"""

async def parse_transaction_text(user_input: str) -> dict | None:
    """Use Gemini to parse natural language transaction text, with fallback models."""
    prompt = PROMPT_TEMPLATE + f'\nUser Message: "{user_input}"'
    return await _run_gemini_parsing(prompt)

async def parse_transaction_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict | None:
    """Use Gemini to parse a receipt image directly, bypassing Tesseract."""
    prompt = PROMPT_TEMPLATE + '\nUser Message: "Please extract the transaction details from this image."'
    image_part = {
        "mime_type": mime_type,
        "data": image_bytes
    }
    return await _run_gemini_parsing([prompt, image_part])

async def _run_gemini_parsing(contents) -> dict | None:
    """Helper to run the Gemini fallback loop for text or multimodal contents."""
    
    for model_name in FALLBACK_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = await model.generate_content_async(contents)
            text = response.text.strip()
            
            # Clean up possible markdown code blocks from Gemini response
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
                
            data = json.loads(text.strip())
            
            # If Gemini mistakenly returns a list (e.g., `[{...}]`), take the first item
            if isinstance(data, list):
                if len(data) > 0:
                    data = data[0]
                else:
                    return None
                    
            return data
            
        except Exception as e:
            logger.warning(f"Gemini model {model_name} failed. Switching to backup...", error=str(e))
            continue # Try the next model in the list
            
    # If all 5 models fail
    logger.error("All 5 Gemini fallback models failed")
    return None
