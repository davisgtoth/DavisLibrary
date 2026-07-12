import os
import sys
import json
from pathlib import Path
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List

class CleanedCourseSummary(BaseModel):
    course_code: str = Field(description="The clean course code (e.g. 'MATH152').")
    cleaned_summary: str = Field(description="A clean, semi-colon separated list of specific topics and concepts from the notes, with all conversational boilerplate completely removed.")

class BatchCleanResponse(BaseModel):
    courses: List[CleanedCourseSummary]

def main():
    lib_dir = Path(__file__).resolve().parent.parent
    cache_path = lib_dir / "scripts" / "notes_cache.json"
    
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)
        
    client = genai.Client()
    
    if not cache_path.exists():
        print(f"ERROR: Cache file {cache_path} not found.")
        sys.exit(1)
        
    with open(cache_path, "r") as f:
        cache = json.load(f)
        
    print("Loaded notes_cache.json. Preparing summaries for cleanup...")
    
    # Format the input for the LLM
    input_list = []
    for code, data in cache.items():
        input_list.append({
            "course_code": code,
            "detailed_summary": data["detailed_summary"]
        })
        
    prompt = f"""
    You are a text processing assistant. You are given a JSON array containing course codes and detailed summaries of student notes.
    
    For each course, clean up its 'detailed_summary' to follow these rules:
    1. Convert it into a clean, semicolon-separated list of topics, concepts, models, and equations.
    2. Strip all conversational filler and boilerplate sentences entirely. For example, remove starting phrases like 'These notes cover', 'The notes detail', 'Key concepts include', 'It also covers', 'It introduces', or transitions like 'They also detail'.
    3. Start directly with the first technical concept.
    4. Do not lose actual technical terms, equations, or theorems. Maintain the technical depth.
    5. Clean up grammar so it reads as a clean list. Do not end with a period.
    
    Here is the input data:
    {json.dumps(input_list, indent=2)}
    
    Return the result matching the response schema.
    """
    
    print("Calling Gemini API to clean all descriptions in a single text request...")
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BatchCleanResponse,
                temperature=0.1
            )
        )
        
        result = json.loads(response.text)
        
        updated_count = 0
        for item in result.get("courses", []):
            code = item.get("course_code", "").replace(" ", "").upper()
            cleaned = item.get("cleaned_summary", "").strip()
            
            if code in cache:
                # Basic sanity check: remove trailing period if any
                if cleaned.endswith("."):
                    cleaned = cleaned[:-1]
                cache[code]["detailed_summary"] = cleaned
                updated_count += 1
                print(f"  -> Cleaned {code}: {cleaned[:60]}...")
                
        # Write back to cache file
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=4)
            
        print(f"\nSuccessfully cleaned {updated_count} course summaries in notes_cache.json.")
        
    except Exception as e:
        print(f"ERROR: Failed to run clean-up API call: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
