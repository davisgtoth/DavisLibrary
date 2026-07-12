import os
import sys
import time
import json
import hashlib
from pathlib import Path
import pypdfium2 as pdfium
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Dict

class CourseAnalysis(BaseModel):
    course_code: str = Field(description="The clean course code in uppercase (e.g. 'PHYS350').")
    generated_title: str = Field(description="A succinct, punchy topic overview for the specific topics covered in this course (max 3-5 words). Avoid generic names.")
    detailed_summary: str = Field(description="A 1-2 sentence description detailing the specific formulas, theorems, and concepts actually written and visible in the notes.")

def calculate_md5(file_path: Path) -> str:
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_course_files(lib_dir: Path, target_code: str) -> Dict[str, str]:
    depts = ["APSC", "CHEM", "CPEN", "ELEC", "ENPH", "LATN", "MATH", "MECH", "PHYS"]
    pdf_files = list(lib_dir.rglob("*.pdf"))
    
    files_dict = {}
    for file_path in pdf_files:
        if "scripts" in file_path.parts:
            continue
            
        parent = file_path.parent.name
        grandparent = file_path.parent.parent.name
        
        course_code = None
        if grandparent in depts:
            course_code = parent
        elif parent in depts:
            course_code = file_path.stem
            
        if course_code:
            clean_code = course_code.replace(" ", "").upper()
            if clean_code == target_code:
                rel_path = file_path.relative_to(lib_dir)
                files_dict[str(rel_path)] = calculate_md5(file_path)
                
    return files_dict

def render_pdf_to_jpegs(pdf_paths: List[Path]) -> List[bytes]:
    jpeg_bytes_list = []
    for pdf_path in pdf_paths:
        try:
            doc = pdfium.PdfDocument(str(pdf_path))
            for i in range(len(doc)):
                page = doc[i]
                image = page.render(scale=1.5).to_pil()
                import io
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=60)
                jpeg_bytes_list.append(out.getvalue())
        except Exception as e:
            print(f"  ERROR rendering {pdf_path.name}: {e}")
    return jpeg_bytes_list

def main():
    target_courses = ["CPEN312", "ELEC221", "PHYS250", "PHYS304", "PHYS403", "PHYS408"]
    
    lib_dir = Path(__file__).resolve().parent.parent
    cache_path = lib_dir / "scripts" / "notes_cache.json"
    
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)
        
    client = genai.Client()
    
    # Load cache
    if not cache_path.exists():
        print(f"ERROR: Cache file {cache_path} not found.")
        sys.exit(1)
        
    with open(cache_path, "r") as f:
        cache = json.load(f)
        
    print(f"Loaded existing cache. Re-analyzing {len(target_courses)} courses individually...")
    
    for idx, code in enumerate(target_courses, start=1):
        print(f"\n--- Re-analyzing {code} ({idx}/{len(target_courses)}) ---")
        
        # Find official name from cache
        if code in cache:
            official_name = cache[code]["official_name"]
        else:
            print(f"WARNING: Course {code} not found in cache. Cannot resolve official name.")
            continue
            
        # Get files
        files_dict = get_course_files(lib_dir, code)
        if not files_dict:
            print(f"ERROR: No note PDFs found in directory for {code}.")
            continue
            
        file_paths = [lib_dir / rel_path for rel_path in files_dict.keys()]
        print(f"  Found {len(file_paths)} PDF(s) for {code}. Rendering pages...")
        
        jpeg_pages = render_pdf_to_jpegs(file_paths)
        print(f"  Total pages rendered: {len(jpeg_pages)}")
        
        if not jpeg_pages:
            print(f"  ERROR: No pages could be rendered for {code}.")
            continue
            
        # Build clean individual request contents
        contents = [
            f"You are analyzing handwritten reference sheets, formula pages, and course notes from a student in the Engineering Physics program at the University of British Columbia (UBC).\n\n"
            f"Specifically, you are analyzing the notes for Course Code: {code} (Official Name: {official_name}).\n\n"
            f"Please examine the note images carefully. Ground your analysis strictly on what is written and drawn in these note pages.\n"
        ]
        
        for p_idx, jpeg_bytes in enumerate(jpeg_pages):
            contents.append(f"Page {p_idx + 1}:")
            contents.append(types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"))
            
        prompt = """
        Examine the associated note pages and extract:
        1. 'generated_title': A succinct, punchy topic overview (3-5 words, e.g. 'Lagrangian & Hamiltonian Dynamics', 'Fourier Analysis & PDEs'). Avoid generic names like 'Reference Sheets' or 'Physics Notes'.
        2. 'detailed_summary': A 1-2 sentence description detailing the specific formulas, theorems, and concepts actually written and visible in the note pages. Do not assume or hallucinate topics that are not visible or referenced in the notes, even if they are standard for the course code.
        
        Return the result matching the response schema.
        """
        contents.append(prompt)
        
        # Call API
        max_retries = 3
        backoff_times = [15, 30, 60]
        success = False
        
        for attempt in range(max_retries):
            try:
                print(f"  Calling Gemini API (Attempt {attempt+1}/{max_retries})...")
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=CourseAnalysis,
                        temperature=0.1
                    )
                )
                
                course_detail = json.loads(response.text)
                gen_title = course_detail.get("generated_title", "")
                summary = course_detail.get("detailed_summary", "")
                
                # Update cache
                cache[code] = {
                    "md5_hashes": files_dict,
                    "official_name": official_name,
                    "generated_title": gen_title,
                    "detailed_summary": summary
                }
                
                # Save cache immediately
                with open(cache_path, "w") as f:
                    json.dump(cache, f, indent=4)
                    
                print(f"  -> Cache updated for {code}:")
                print(f"     Title: {gen_title}")
                print(f"     Summary: {summary}")
                success = True
                break
                
            except Exception as e:
                print(f"  ERROR calling API for {code}: {e}")
                if attempt < max_retries - 1:
                    wait_time = backoff_times[attempt]
                    print(f"  Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                else:
                    print(f"  Failed permanently to re-analyze {code}.")
                    
        # Sleep to observe RPM limits
        if idx < len(target_courses) and success:
            print("Pacing: Sleeping 15 seconds to respect RPM limits...")
            time.sleep(15)
            
    print("\nReindexing complete! Targeted cache entries are updated.")

if __name__ == "__main__":
    main()
