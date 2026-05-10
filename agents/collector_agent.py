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
Return ONLY a valid JSON object. Do not include markdown formatting, backticks, or extra text.

Rules for extraction:
- amount: (float) the numeric value of the transaction.
- type: (string) exactly one of ["expense", "income", "emi", "investment"].
- category: (string) a short 1-2 word category (e.g., "Food", "Transport", "Salary", "Rent", "Groceries", "Car Loan").
- description: (string) a brief summary of the transaction.

User Message: "{user_input}"
"""

async def parse_transaction_text(user_input: str) -> dict | None:
    """Use Gemini to parse natural language transaction text, with fallback models."""
    prompt = PROMPT_TEMPLATE.format(user_input=user_input)
    
    for model_name in FALLBACK_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = await model.generate_content_async(prompt)
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
    logger.error("All 5 Gemini fallback models failed", user_input=user_input)
    return None
