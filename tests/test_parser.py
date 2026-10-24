import pytest
from app.parser.parse import parse_pob_file
import os

def test_parser_exists():
    """Basic test that parser module exists"""
    assert True

def test_parse_fixture():
    """Test parsing of fixture files"""
    # Test with the first fixture file
    fixture_path = "tests/fixtures/build1.txt"
    
    if os.path.exists(fixture_path):
        build = parse_pob_file(fixture_path)
        # Basic assertions to ensure parsing doesn't crash
        assert build is not None
        assert build.class_name is not None
        print(f"Parsed build: {build.class_name}, level {build.level}")
    else:
        pytest.skip("Fixture file not found")