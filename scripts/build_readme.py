import os
import sys
import argparse
import json
import re
from pathlib import Path
import urllib.parse


def main():
    parser = argparse.ArgumentParser(description="Build README from the notes cache with flexible titles.")
    parser.add_argument(
        "--style", 
        choices=["generated", "course-name", "both"], 
        default="generated",
        help="Formatting style for the description column (default: generated)"
    )
    parser.add_argument(
        "--preview", 
        action="store_true", 
        help="Output to README_preview.md instead of overwriting README.md"
    )
    args = parser.parse_args()

    lib_dir = Path(__file__).resolve().parent.parent
    readme_path = lib_dir / "README.md"
    cache_path = lib_dir / "scripts" / "notes_cache.json"
    output_path = lib_dir / ("README_preview.md" if args.preview else "README.md")

    # Safety checks
    if not cache_path.exists():
        print(f"ERROR: Cache file not found at {cache_path}. Run index_library.py first!")
        sys.exit(1)
        
    if not readme_path.exists():
        print(f"ERROR: README.md not found at {readme_path}")
        sys.exit(1)

    # Load cache
    try:
        with open(cache_path, "r") as f:
            cache = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to parse cache file: {e}")
        sys.exit(1)

    # Read original README.md
    with open(readme_path, "r") as f:
        readme_lines = f.readlines()

    new_lines = []
    updated_count = 0

    # Regex to match markdown table rows: | **Code** | Name | Description |
    row_pattern = re.compile(r'^\|\s*\*\*(.*?)\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$')

    for line in readme_lines:
        match = row_pattern.match(line.strip())
        
        if match:
            raw_code = match.group(1).strip()
            course_name = match.group(2).strip()
            old_desc = match.group(3).strip()
            
            clean_code = raw_code.replace(" ", "").upper()
            
            # Filter out table header rows or markdown formatting rows
            if clean_code in ["COURSECODE", "---", ":---", ":---:"]:
                new_lines.append(line)
                continue

            if clean_code in cache:
                data = cache[clean_code]
                gen_title = data.get("generated_title", "").strip()
                detailed_summary = data.get("detailed_summary", "").strip()
                official_name = data.get("official_name", "").strip()
                high_level_modules = data.get("high_level_modules", detailed_summary).strip()
                md5_hashes = data.get("md5_hashes", {})

                # Determine URL-encoded link target
                link_target = ""
                if len(md5_hashes) == 1:
                    file_path = list(md5_hashes.keys())[0]
                    safe_path = "/".join(urllib.parse.quote(part) for part in Path(file_path).parts)
                    link_target = f"./{safe_path}"
                elif len(md5_hashes) > 1:
                    file_path = list(md5_hashes.keys())[0]
                    parent_dir = Path(file_path).parent
                    safe_path = "/".join(urllib.parse.quote(part) for part in parent_dir.parts)
                    link_target = f"./{safe_path}"

                # Choose formatting style
                if args.style == "generated":
                    formatted_desc = f"**{gen_title}:** {detailed_summary}"
                elif args.style == "course-name":
                    formatted_desc = high_level_modules
                elif args.style == "both":
                    formatted_desc = f"**[Gen: {gen_title}] / [UBC: {raw_code} Core Concepts]:** {high_level_modules}"
                else:
                    formatted_desc = old_desc

                # Reconstruct line, making raw_code a link and maintaining course_name
                if link_target:
                    new_line = f"| [**{raw_code}**]({link_target}) | {course_name} | {formatted_desc} |\n"
                else:
                    new_line = f"| **{raw_code}** | {course_name} | {formatted_desc} |\n"
                new_lines.append(new_line)
                updated_count += 1
            else:
                # No cached data found for this course, keep the original line
                new_lines.append(line)
        else:
            # Not a course table row (headers, empty lines, section titles), keep exactly as is
            new_lines.append(line)

    # Write the compiled README contents
    with open(output_path, "w") as f:
        f.writelines(new_lines)

    print(f"\nSuccess! Rebuilt {output_path.name}")
    print(f"Updated {updated_count} course descriptions from your notes cache using style: '{args.style}'")
    if args.preview:
        print("Please check README_preview.md to inspect the results.")

if __name__ == "__main__":
    main()
