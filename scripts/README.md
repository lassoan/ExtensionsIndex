# Extension Validation Scripts

This directory contains scripts for validating Slicer extension description files and repository structure.

## Scripts

### `check_description_files.py`
The original validation script that provides console output.

### `check_description_files_with_report.py`
Enhanced validation script with markdown report generation capabilities.

### `test_filenames.py`
Unit tests for filename validation using Python's unittest framework.

## Features

### Extension Description Validation
The validation scripts check for:

1. **Category Check**: Ensures all extensions have a valid `category` field
2. **SCM URL Syntax**: Validates that the `scm_url` field has proper format (git:// or https://)
3. **Repository Name**: Checks if repository names follow Slicer naming conventions (should contain "slicer")

### Repository Structure Validation
The filename validation checks for:

1. **Allowed Directories**: Only permits specific directories (`.github`, `scripts`, `ARCHIVE`, etc.)
2. **Allowed Files**: Only permits specific files (`README.md`, `.pre-commit-config.yaml`, etc.)
3. **JSON Extensions**: Ensures only `.json` files exist in the repository root

## Usage

### Extension Description Validation

#### Console Output (Original Format)
```bash
python check_description_files_with_report.py --output-format console ExtensionName.json [...]
```

#### Markdown Report
```bash
python check_description_files_with_report.py --output-format markdown --output-file report.md ExtensionName.json [...]
```

#### Check Dependencies
```bash
python check_description_files_with_report.py --check-dependencies /path/to/extensions/directory ExtensionName.json [...]
```

### Repository Structure Validation

## GitHub Actions

The repository includes several GitHub Action workflows:

### Extension Validation Workflow (`.github/workflows/extension-validation.yml`)
- Runs on every push and pull request
- Uses Python 3.12 environment
- Includes filename validation as a pre-check
- Generates a markdown validation report
- Posts the report as a comment on pull requests
- Uploads the report as an artifact
- Displays the report in the GitHub Actions summary

### Test Workflow (`.github/workflows/tests.yml`)
- Runs filename and extension validation tests
- Separate jobs for better parallelization
- Uses console output for faster execution
- Runs daily at 3 AM UTC

### Lint Workflow (`.github/workflows/lint.yml`)
- Runs pre-commit hooks for code formatting
- Uses Python 3.9 for compatibility

### Features of the GitHub Action Reports

1. **Summary Statistics**: Shows total extensions checked, number with errors, etc.
2. **Detailed Error Reports**: For each extension with issues, shows:
   - Clickable repository link
   - Categorized error messages (Category, SCM URL, Repository Name)
3. **Check Results Table**: Summary table showing pass/fail counts for each validation type
4. **Automatic PR Comments**: Updates existing comments instead of creating new ones
5. **Filename Validation**: Ensures repository structure follows expected patterns

## Dependencies

- `joblib`: For parallel processing of validation checks

Install with:
```bash
pip install -r requirements.txt
```

## Repository Name Exceptions

Some extensions are exempt from the repository naming convention check. These are listed in the `REPOSITORY_NAME_CHECK_EXCEPTIONS` list in both scripts.
