import sys
import os

# Add parent directory to path so we can import from it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parse import parse_pob_file

def main():
    """CLI entry point for parser"""
    if len(sys.argv) < 2:
        print("Usage: python -m app.parser <file>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    build = parse_pob_file(filepath)
    
    print(f"Build Class: {build.class_name}")
    print(f"Ascendancy: {build.ascendancy}")
    print(f"Level: {build.level}")
    print(f"Skills: {len(build.skill_groups)}")
    print(f"Items: {len(build.items)}")
    
    # Show first few skills
    print("\nSkills:")
    for i, skill in enumerate(build.skill_groups[:5]):
        print(f"  {i+1}. {skill.name} (Level: {skill.level}, Quality: {skill.quality})")

if __name__ == "__main__":
    main()