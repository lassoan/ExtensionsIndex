#!/usr/bin/env python3

"""
Python script for checking filenames in the ExtensionsIndex repository.
This replaces the functionality of check_filenames.sh.
"""

import sys
from pathlib import Path


def check_filenames():
    """Check for unexpected files in the repository."""
    # Get the repository root directory (parent of scripts directory)
    script_dir = Path(__file__).parent
    root_dir = script_dir.parent
    
    # Define allowed directories
    allowed_directories = {
        '.circleci',
        '.idea', 
        '.github',
        '.git',
        'ARCHIVE',
        'scripts',
        '.venv',          # Python virtual environment
        'venv',           # Alternative venv name
        '__pycache__',    # Python cache
        '.pytest_cache'   # Pytest cache
    }
    
    # Define allowed files (exact names)
    allowed_files = {
        '.pre-commit-config.yaml',
        '.prettierrc.js',
        '.git-blame-ignore-revs',
        'README.md'
    }
    
    # Define allowed file patterns (extensions)
    allowed_extensions = {
        '.json'
    }
    
    def is_file_allowed(file_path):
        """Check if a file is allowed based on name or extension."""
        # Check exact filename
        if file_path.name in allowed_files:
            return True
        
        # Check file extension
        if file_path.suffix in allowed_extensions:
            return True
        
        return False
    
    print("Looking for unexpected files")
    
    unexpected_files = []
    
    # Walk through all files and directories in the root
    for item in root_dir.iterdir():
        if item.is_dir():
            # Check if directory is allowed
            if item.name not in allowed_directories:
                unexpected_files.append(item.relative_to(root_dir))
        elif item.is_file():
            # Check if file is allowed
            if not is_file_allowed(item):
                unexpected_files.append(item.relative_to(root_dir))
    
    # Print unexpected files
    for unexpected_file in unexpected_files:
        print(unexpected_file)
    
    if unexpected_files:
        print("Looking for unexpected files - failed")
        return 1
    else:
        print("Looking for unexpected files - done")
        return 0


def main():
    """Main function."""
    exit_code = check_filenames()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
