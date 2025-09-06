#!/usr/bin/env python3
"""
Convert all Python files to use Linux line endings (LF)
"""
import os
import glob

def convert_to_lf(filepath):
    """Convert a file to use LF line endings"""
    with open(filepath, 'rb') as f:
        content = f.read()
    
    # Replace CRLF with LF
    content = content.replace(b'\r\n', b'\n')
    
    with open(filepath, 'wb') as f:
        f.write(content)
    
    print(f"Converted: {filepath}")

def main():
    # Get all Python files in the current directory
    python_files = [
        'synclet.py',
        'config.py', 
        'database.py',
        'magento_api.py'
    ]
    
    for filename in python_files:
        if os.path.exists(filename):
            convert_to_lf(filename)
    
    # Also convert requirements.txt and README.md
    if os.path.exists('requirements.txt'):
        convert_to_lf('requirements.txt')
    
    if os.path.exists('README.md'):
        convert_to_lf('README.md')
    
    print("\nAll files converted to Linux line endings (LF)")

if __name__ == '__main__':
    main()
