import sys
sys.path.insert(0, '.')
from app.parser.parse import parse_pob_file

# Debug one specific file to see what's going on  
build = parse_pob_file('tests/fixtures/build1.txt')

print('First few items and their mods:')
for i, item in enumerate(build.items[:3]):
    print(f'Item {i+1}: {item.name}')
    for mod in item.mods:
        if 'Requires' in mod:
            print(f'  *** FOUND REQUIRES LINE: \"{mod}\"')
    print()