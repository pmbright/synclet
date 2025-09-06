#!/bin/bash
# Fix line endings for all text files in the synclet project

echo "Fixing line endings..."

# Find all text files and convert CRLF to LF
find /opt/synclet -type f \( -name "*.sh" -o -name "*.py" -o -name "*.sql" -o -name "*.yaml" -o -name "*.txt" -o -name "*.md" -o -name "*.cfg" -o -name "*.ini" \) -exec dos2unix {} \;

echo "Line endings fixed!"
