import sys
sys.path.insert(0, '.')
from app.parser.parse import parse_pob_file

# Look directly at lines of the first item

with open('tests/fixtures/build1.txt', 'r') as f:
    code = f.read().strip()
    
# Decode manually to see raw data
import base64
import zlib
code = code.replace('-', '+').replace('_', '/')
missing_padding = len(code) % 4
if missing_padding:
    code += '=' * (4 - missing_padding)
    
compressed_data = base64.urlsafe_b64decode(code)
xml_content = zlib.decompress(compressed_data, wbits=15)

import xml.etree.ElementTree as ET
root = ET.fromstring(xml_content.decode('utf-8'))

items_elem = root.find('Items')
item_elements = items_elem.findall('Item')

# Look at first item text content
first_item = item_elements[0]
print(f"First item ID: {first_item.get('id')}")
print("First item text:")
print(repr(first_item.text))

print("\nBreaking into lines:")
lines = [line.strip() for line in (first_item.text or '').split('\n') if line.strip()]
for i, line in enumerate(lines):
    print(f"{i}: '{line}'")
    
# Now let's manually see the filters
headers_to_filter = [
    'Requires Class:', 'Sockets:', 'LevelReq:', 'Implicits:',
    'Unique ID:', 'Quality:', 'Radius:', 'Cluster Jewel Skill:',
    'Cluster Jewel Node Count:', 'ArmourBasePercentile:', 
    'EvasionBasePercentile:', 'Item Level:', 'Limited to:',
    'Requires:', 'Allocates:', 'Passives in', 'Only affects', 
    'Passive Skills in', 'Searing Exarch Item', 'Eater of Worlds Item',
    'Foulborn Item', 'Fractured Item', 'Catalyst:', 'CatalystQuality:'
]

print("\nChecking lines against filters:")
for i, line in enumerate(lines):
    stripped = line.strip()
    is_header = any(stripped.startswith(header) for header in headers_to_filter)
    print(f"{i}: '{stripped}' -> Is header: {is_header}")