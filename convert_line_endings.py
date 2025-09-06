#!/usr/bin/env python3
"""Convert all text files in the project to Unix line endings (LF)."""

import os
import glob

def convert_to_unix_line_endings(filepath):
    """Convert a file from CRLF to LF line endings."""
    try:
        # Read the file
        with open(filepath, 'rb') as f:
            content = f.read()
        
        # Replace CRLF with LF
        content = content.replace(b'\r\n', b'\n')
        
        # Write back
        with open(filepath, 'wb') as f:
            f.write(content)
        
        print(f"Converted: {filepath}")
        return True
    except Exception as e:
        print(f"Error converting {filepath}: {e}")
        return False

def main():
    """Convert all project files to Unix line endings."""
    # Get the project root directory
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    # Define file patterns to convert
    patterns = [
        '**/*.py',
        '**/*.sh',
        '**/*.sql',
        '**/*.yaml',
        '**/*.yml',
        '**/*.txt',
        '**/*.md',
        '**/requirements.txt',
        'setup.py'
    ]
    
    converted_count = 0
    
    for pattern in patterns:
        # Use recursive glob to find all matching files
        for filepath in glob.glob(os.path.join(project_root, pattern), recursive=True):
            # Skip the logs directory
            if 'logs' in filepath:
                continue
            
            if os.path.isfile(filepath):
                if convert_to_unix_line_endings(filepath):
                    converted_count += 1
    
    print(f"\nTotal files converted: {converted_count}")

if __name__ == '__main__':
    main()
