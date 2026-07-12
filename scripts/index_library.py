import os
import sys
import time
import json
import hashlib
import re
from pathlib import Path
import pypdfium2 as pdfium
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Dict

# Define structured output schema for the batch response
class CourseAnalysis(BaseModel):
    course_code: str = Field(description="The clean course code matching the input in uppercase (e.g. 'MATH152', 'PHYS350').")
    generated_title: str = Field(description="A succinct, punchy topic overview for the specific topics covered in this course (max 3-5 words). Avoid generic names like 'Reference Page'.")
    detailed_summary: str = Field(description="A 1-2 sentence description detailing the specific formulas, theorems, and concepts actually written and visible in the notes.")

class BatchAnalysisResponse(BaseModel):
    courses: List[CourseAnalysis] = Field(description="List of analyzed courses in this batch.")

def calculate_md5(file_path: Path) -> str:
    """Calculate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_course_files(lib_dir: Path) -> Dict[str, Dict[str, str]]:
    """Scan directory and return a dict of {CLEAN_CODE: {filename: md5}}."""
    depts = ["APSC", "CHEM", "CPEN", "ELEC", "ENPH", "LATN", "MATH", "MECH", "PHYS"]
    pdf_files = list(lib_dir.rglob("*.pdf"))
    
    course_to_files = {}
    for file_path in pdf_files:
        # Ignore any files in the scripts directory
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
            if clean_code not in course_to_files:
                course_to_files[clean_code] = {}
            
            # Store filename and calculate MD5
            rel_path = file_path.relative_to(lib_dir)
            course_to_files[clean_code][str(rel_path)] = calculate_md5(file_path)
            
    return course_to_files

def parse_readme_courses(readme_path: Path) -> Dict[str, Dict]:
    """Parse the README to find all course rows and extract code and official name."""
    courses = {}
    if not readme_path.exists():
        return courses
        
    row_pattern = re.compile(r'^\|\s*\*\*(.*?)\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$')
    with open(readme_path, "r") as f:
        for line in f:
            match = row_pattern.match(line.strip())
            if match:
                raw_code = match.group(1).strip()
                course_name = match.group(2).strip()
                clean_code = raw_code.replace(" ", "").upper()
                
                # Filter out table headers or markdown separators
                if clean_code in ["COURSECODE", "---", ":---", ":---:"]:
                    continue
                    
                courses[clean_code] = {
                    "raw_code": raw_code,
                    "official_name": course_name
                }
    return courses

def render_pdf_to_jpegs(pdf_paths: List[Path]) -> List[bytes]:
    """Render all pages of given PDFs to low-res JPEG bytes using pypdfium2."""
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
    lib_dir = Path(__file__).resolve().parent.parent
    scripts_dir = lib_dir / "scripts"
    readme_path = lib_dir / "README.md"
    cache_path = scripts_dir / "notes_cache.json"
    
    # Ensure API Key is set
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)
        
    client = genai.Client()
    
    # 1. Parse official names from README
    print("Parsing README.md...")
    readme_courses = parse_readme_courses(readme_path)
    print(f"Found {len(readme_courses)} courses listed in README.")
    
    # 2. Scan workspace for note PDF files and calculate current MD5 checksums
    print("Scanning workspace for note PDFs...")
    workspace_files = get_course_files(lib_dir)
    print(f"Found notes files for {len(workspace_files)} courses in workspace.")
    
    # 3. Load cache
    cache = {}
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
            print(f"Loaded existing cache with {len(cache)} courses.")
        except Exception as e:
            print(f"WARNING: Failed to parse cache file: {e}. Starting fresh.")
            cache = {}
            
    # 4. Check which courses are dirty (need analysis)
    dirty_courses = []
    for clean_code, info in readme_courses.items():
        # Get files associated with this course in the workspace
        files_in_workspace = workspace_files.get(clean_code, {})
        
        # If there are no files for this course in workspace, skip it
        if not files_in_workspace:
            print(f"  Skipping {clean_code} - no PDF files found in directory.")
            continue
            
        # Determine if cache is valid
        is_dirty = False
        if clean_code not in cache:
            is_dirty = True
        else:
            cached_info = cache[clean_code]
            cached_md5s = cached_info.get("md5_hashes", {})
            # If files or checksums differ, mark dirty
            if cached_md5s != files_in_workspace:
                is_dirty = True
                
        if is_dirty:
            dirty_courses.append({
                "code": clean_code,
                "official_name": info["official_name"],
                "files": [lib_dir / path_str for path_str in files_in_workspace.keys()],
                "md5_hashes": files_in_workspace
            })
            
    print(f"\n{len(dirty_courses)} courses need to be analyzed.")
    
    if not dirty_courses:
        print("All courses are up to date in the cache. Run build_readme.py to rebuild the README if needed.")
        return
        
    # 5. Process dirty courses in batches of 5
    BATCH_SIZE = 5
    batches = [dirty_courses[i:i + BATCH_SIZE] for i in range(0, len(dirty_courses), BATCH_SIZE)]
    
    for b_idx, batch in enumerate(batches, start=1):
        print(f"\n--- Processing Batch {b_idx}/{len(batches)} (Contains {len(batch)} courses) ---")
        
        contents = [
            "You are analyzing handwritten reference sheets, formula pages, and course notes from a student in the Engineering Physics program at the University of British Columbia (UBC).\n\n"
            "For each course in the list below, we have provided all page images of their note files. "
            "Please examine the note images carefully. Ground your analysis strictly on what is written and drawn in these note pages.\n\n"
            "Here are the courses in this batch:\n"
        ]
        
        for course in batch:
            contents[0] += f"- Code: {course['code']} | Official Name: {course['official_name']}\n"
            
        contents[0] += (
            "\nNow, for each course, we present its page images. Please match the notes to the corresponding course code.\n"
        )
        
        # Prepare page images
        has_pages = False
        for course in batch:
            print(f"  Rendering all pages for {course['code']} ({len(course['files'])} PDF files)...")
            course_pages = render_pdf_to_jpegs(course["files"])
            print(f"    Total pages rendered: {len(course_pages)}")
            
            if course_pages:
                has_pages = True
                contents.append(f"\n--- Notes for Course Code: {course['code']} (Official Name: {course['official_name']}) ---")
                for page_idx, jpeg_bytes in enumerate(course_pages):
                    contents.append(f"Page {page_idx + 1}:")
                    contents.append(types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"))
                    
        if not has_pages:
            print("  WARNING: No pages could be rendered for any course in this batch. Skipping batch.")
            continue
            
        prompt = """
        For each course code in this batch, examine its associated note pages and extract:
        1. 'generated_title': A succinct, punchy topic overview (3-5 words, e.g. 'Lagrangian & Hamiltonian Dynamics', 'Fourier Analysis & PDEs'). Avoid generic names like 'Reference Sheets' or 'Physics Notes'.
        2. 'detailed_summary': A 1-2 sentence description detailing the specific formulas, theorems, and concepts actually written and visible in the note pages. Do not assume or hallucinate topics that are not visible or referenced in the notes, even if they are standard for the course code.
        
        Return the result matching the response schema.
        """
        contents.append(prompt)
        
        # Call API with retry mechanism
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
                        response_schema=BatchAnalysisResponse,
                        temperature=0.1
                    )
                )
                
                result = json.loads(response.text)
                
                # Save batch results to local cache
                for course_detail in result.get("courses", []):
                    code = course_detail.get("course_code", "").replace(" ", "").upper()
                    gen_title = course_detail.get("generated_title", "")
                    summary = course_detail.get("detailed_summary", "")
                    
                    # Find matching course in batch to get metadata
                    matching_course = next((c for c in batch if c["code"] == code), None)
                    if matching_course:
                        cache[code] = {
                            "md5_hashes": matching_course["md5_hashes"],
                            "official_name": matching_course["official_name"],
                            "generated_title": gen_title,
                            "detailed_summary": summary
                        }
                        print(f"  -> Cache updated for {code}: {gen_title}")
                    else:
                        print(f"  WARNING: Received result for {code} which was not in this batch!")
                
                # Write cache file immediately after successful batch
                with open(cache_path, "w") as f:
                    json.dump(cache, f, indent=4)
                print(f"  Successfully cached batch {b_idx} to {cache_path.name}")
                
                success = True
                break
                
            except Exception as e:
                print(f"  ERROR calling API: {e}")
                if attempt < max_retries - 1:
                    wait_time = backoff_times[attempt]
                    print(f"  Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                else:
                    print(f"  Batch {b_idx} failed permanently after {max_retries} attempts.")
                    
        # Sleep to observe RPM limits
        if b_idx < len(batches):
            print("Pacing API request: Sleeping for 15 seconds to respect rate limits...")
            time.sleep(15)
            
    print("\nIndexing complete! Cache generated successfully.")

if __name__ == "__main__":
    main()
