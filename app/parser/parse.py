import base64
import zlib
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict
from app.parser.models import Build, SkillGem, Item

def decode_pob_code(code: str) -> str:
    """Decode a Path of Building code"""
    # Replace characters for base64 URL-safe decoding
    code = code.replace('-', '+').replace('_', '/')
    
    # Add padding if necessary
    missing_padding = len(code) % 4
    if missing_padding:
        code += '=' * (4 - missing_padding)
    
    # Base64 decode and decompress with zlib
    compressed_data = base64.urlsafe_b64decode(code)
    xml_content = zlib.decompress(compressed_data, wbits=15)  # wbits=15 for raw zlib
    
    return xml_content.decode('utf-8')

def parse_build_from_xml(xml_content: str) -> Build:
    """Parse build information from XML content"""
    
    root = ET.fromstring(xml_content)
    
    # Extract basic build info from Build element
    build_elem = root.find('Build')
    if build_elem is None:
        raise ValueError("No Build element found in XML")
    
    build_data = {
        'class_name': build_elem.get('className') or '',
        'ascendancy': build_elem.get('ascendClassName'),
        'level': int(build_elem.get('level') or 1),
        'skill_groups': [],
        'items': [],
        'tree': {},
        'config': {}
    }
    
    # Parse Skills
    skills_elem = root.find('Skills')
    if skills_elem is not None:
        skill_sets = skills_elem.findall('SkillSet')
        # Process skill sets
        for skill_set in skill_sets:
            skills = skill_set.findall('Skill')
            for skill in skills:
                gem_elem = skill.find('Gem')
                if gem_elem is not None:
                    skill_gem = SkillGem(
                        name=gem_elem.get('nameSpec') or gem_elem.get('gemId', '').split('/')[-1],
                        level=int(gem_elem.get('level') or 1),
                        quality=int(gem_elem.get('quality') or 0),
                        enabled=gem_elem.get('enabled', 'false').lower() == 'true',
                        is_support='support' in gem_elem.get('nameSpec', '').lower(),
                        is_main_skill=False  # We'll set this after identifying main skill
                    )
                    build_data['skill_groups'].append(skill_gem)
    
    # Parse Items 
    items_elem = root.find('Items')
    if items_elem is not None:
        item_elements = items_elem.findall('Item')
        for item in item_elements:
            item_id = item.get('id', '')
            # Extract rarity, name, base, and mods from the text content
            item_text = item.text or ''
            lines = [line.strip() for line in item_text.split('\n') if line.strip()]
            
            rarity = 'UNKNOWN'
            name = 'Unknown Item' 
            base = 'Unknown Base'
            mods = []
            
            for i, line in enumerate(lines):
                if line.startswith('Rarity:'):
                    rarity = line.replace('Rarity:', '').strip()
                elif line and not line.startswith('Unique ID:') and not line.startswith('Item Level:') and not line.startswith('Limited to:') and not line.startswith('Requires Class:') and not line.startswith('Implicits:') and not line.startswith('<ModRange') and not line.startswith('Corrupted'):
                    if i == 1:  # Second line is typically the name
                        name = line
                    elif i == 2:  # Third line might be base
                        base = line
            
            item_obj = Item(
                slot=item_id,  # We don't currently have precise slot info from this structure
                rarity=rarity,
                name=name,
                base=base,
                mods=mods
            )
            build_data['items'].append(item_obj)
    
    # Create the Build object
    build = Build(**build_data)
    
    return build

def parse_pob_file(filepath: str) -> Build:
    """Parse a PoB file and return build information"""
    with open(filepath, 'r') as f:
        code = f.read().strip()
    
    # Decode the code
    xml_content = decode_pob_code(code)
    
    # Parse XML content 
    return parse_build_from_xml(xml_content)

def main():
    """CLI entry point for parser"""
    import sys
    
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