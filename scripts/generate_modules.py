import os
import sys
import json
from pathlib import Path
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List

class CourseModuleSummary(BaseModel):
    course_code: str = Field(description="The clean course code (e.g. 'PHYS408').")
    high_level_modules: str = Field(description="A semicolon-separated list of exactly 3-5 high-level course modules/topics (2-4 words each, capitalized) representing the main units of the course notes.")

class BatchModuleResponse(BaseModel):
    courses: List[CourseModuleSummary]

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
        
    print("Loaded notes_cache.json. Preparing summaries for high-level module extraction...")
    
    input_list = []
    for code, data in cache.items():
        input_list.append({
            "course_code": code,
            "official_name": data["official_name"],
            "detailed_summary": data["detailed_summary"]
        })
        
    prompt = f"""
    You are a curriculum design assistant. You are given a JSON array containing course codes, names, and detailed lists of concepts from student notes.
    
    For each course, summarize the detailed concepts into exactly 3-5 high-level course modules or topics.
    
    Rules:
    1. Output a single string with 3-5 topics separated by semicolons (e.g. 'Gaussian Wave and Fourier Optics; Polarization Optics; Cavity Optics; Laser Physics').
    2. Each topic should be a high-level unit/module (2-4 words maximum, capitalized appropriately).
    3. The topics must represent the core areas of the course notes provided in 'detailed_summary'. Do not list granular formulas or names (e.g. use 'Ethical Theories' instead of 'utilitarianism, duty-based, rights-based ethics').
    4. Start directly with the first module. Do not add full sentences or conversational padding.
    
    Here is the input data:
    {json.dumps(input_list, indent=2)}
    
    Return the result matching the response schema.
    """
    
    print("Calling Gemini API to extract high-level modules in a single text request...")
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BatchModuleResponse,
                temperature=0.1
            )
        )
        
        result = json.loads(response.text)
        
        updated_count = 0
        for item in result.get("courses", []):
            code = item.get("course_code", "").replace(" ", "").upper()
            modules = item.get("high_level_modules", "").strip()
            
            if code in cache:
                # Sanity check: remove trailing period
                if modules.endswith("."):
                    modules = modules[:-1]
                cache[code]["high_level_modules"] = modules
                updated_count += 1
                print(f"  -> {code}: {modules}")
                
        # Write back to cache file
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=4)
            
        print(f"\nSuccessfully saved {updated_count} course modules to notes_cache.json.")
        
    except Exception as e:
        print(f"ERROR: Failed to run modules extraction API call: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
