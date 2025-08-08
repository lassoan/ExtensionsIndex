#!/usr/bin/env python

"""
Python 3.x CLI for validating extension description files with enhanced reporting.
"""

import argparse
import json
import os
import sys
import textwrap
import urllib.request
import urllib.parse as urlparse
import subprocess
import re
import tempfile
import shutil
from datetime import datetime
from functools import wraps
from pathlib import Path

try:
    from joblib import Parallel, delayed, parallel_backend
except ImportError:
    raise SystemExit(
        "joblib not available: "
        "consider installing it running 'pip install joblib'"
    ) from None

class ExtensionDependencyError(RuntimeError):
    """Exception raised when a particular extension description file failed to be parsed.
    """
    def __init__(self, error_list):
        self.error_list = error_list

    def __str__(self):
        return "\n".join(self.error_list)

class ExtensionParseError(RuntimeError):
    """Exception raised when a particular extension description file failed to be parsed.
    """
    def __init__(self, extension_name, details):
        self.extension_name = extension_name
        self.details = details

    def __str__(self):
        return self.details

class ExtensionCheckError(RuntimeError):
    """Exception raised when a particular extension check failed.
    """
    def __init__(self, extension_name, check_name, details):
        self.extension_name = extension_name
        self.check_name = check_name
        self.details = details

    def __str__(self):
        return self.details


def require_metadata_key(metadata_key, value_required=True):
    check_name = "require_metadata_key"

    def dec(fun):
        @wraps(fun)
        def wrapped(*args, **kwargs):
            extension_name = args[0]
            metadata = args[1]
            if metadata_key not in metadata.keys():
                raise ExtensionCheckError(extension_name, check_name, "%s key is missing" % metadata_key)
            if value_required and metadata[metadata_key] is None:
                raise ExtensionCheckError(extension_name, check_name, "%s value is not set" % metadata_key)
            return fun(*args, **kwargs)
        return wrapped
    return dec


def parse_json(ext_file_path):
    """Parse a Slicer extension description file.
    :param ext_file_path: Path to a Slicer extension description file (.json).
    :return: Dictionary of extension metadata.
    """
    with open(ext_file_path) as input_file:
        try:
            return json.load(input_file)
        except json.JSONDecodeError as exc:
            extension_name = os.path.splitext(os.path.basename(ext_file_path))[0]
            raise ExtensionParseError(
                extension_name,
                textwrap.dedent("""
                Failed to parse '%s': %s
                """ % (ext_file_path, exc)))


@require_metadata_key("category")
def check_category(extension_name, metadata):
    category = metadata["category"]
    if category not in ACCEPTED_EXTENSION_CATEGORIES:
        raise ExtensionCheckError(extension_name, "check_category", f"Category '{category}' is unknown. Consider using any of the known extensions instead: {', '.join(ACCEPTED_EXTENSION_CATEGORIES)}")

@require_metadata_key("scm_url")
def check_scm_url_syntax(extension_name, metadata):
    check_name = "check_scm_url_syntax"

    if "://" not in metadata["scm_url"]:
        raise ExtensionCheckError(extension_name, check_name, "scm_url do not match scheme://host/path")

    supported_schemes = ["git", "https"]
    scheme = urlparse.urlsplit(metadata["scm_url"]).scheme
    if scheme not in supported_schemes:
        raise ExtensionCheckError(
            extension_name, check_name,
            "scm_url scheme is '%s' but it should by any of %s" % (scheme, supported_schemes))


@require_metadata_key("scm_url")
def check_git_repository_name(extension_name, metadata):
    """See https://www.slicer.org/wiki/Documentation/Nightly/Developers/FAQ#Should_the_name_of_the_source_repository_match_the_name_of_the_extension_.3F
    """
    check_name = "check_git_repository_name"

    repo_name = os.path.splitext(urlparse.urlsplit(metadata["scm_url"]).path.split("/")[-1])[0]

    if repo_name in REPOSITORY_NAME_CHECK_EXCEPTIONS:
        return

    if "slicer" not in repo_name.lower():

        variations = [prefix + repo_name for prefix in ["Slicer-", "Slicer_", "SlicerExtension-", "SlicerExtension_"]]

        raise ExtensionCheckError(
            extension_name, check_name,
            textwrap.dedent("""
            extension repository name is '%s'. Please, consider changing it to 'Slicer%s' or any of
            these variations %s.
            """ % (
                repo_name, repo_name, variations)))

def clone_repository(scm_url, scm_revision):
    """Clone a git repository to a temporary directory."""
    temp_dir = tempfile.mkdtemp(prefix="extension_check_")
    try:
        if scm_revision:
            subprocess.run(
                ['git', 'clone', scm_url, temp_dir],
                check=True, capture_output=True, text=True, timeout=120)
            subprocess.run(
                ['git', 'checkout', scm_revision],
                cwd=temp_dir,
                check=True, capture_output=True, text=True, timeout=30)
        else:
            subprocess.run(
                ['git', 'clone', '--depth', '1', scm_url, temp_dir],
                check=True, capture_output=True, text=True, timeout=60)
        return temp_dir
    except subprocess.TimeoutExpired as e:
        raise ExtensionCheckError("unknown", "clone_repository", f"Git clone operation timed out: {e}")
    except subprocess.CalledProcessError as e:
        raise ExtensionCheckError("unknown", "clone_repository", f"Failed to clone repository: {e.stderr.strip() if e.stderr else 'Unknown git error'}")
    except FileNotFoundError:
        raise ExtensionCheckError("unknown", "clone_repository", "Git command not found. Please ensure git is installed and in PATH")

def check_extension_repository_content(extension_name, metadata, cloned_repository_folder=None):
    """Check if the top-level CMakeLists.txt file project name matches the extension name."""
    check_name = "check_extension_repository_content"

    # Look for CMakeLists.txt in the cloned repository
    if not cloned_repository_folder:
        raise ExtensionCheckError(
            extension_name, check_name,
            "Repository is not available.")
    cmake_file_path = os.path.join(cloned_repository_folder, "CMakeLists.txt")
    if not os.path.isfile(cmake_file_path):
        raise ExtensionCheckError(
            extension_name, check_name,
            "CMakeLists.txt file not found in repository root")
    
    # Read and parse CMakeLists.txt
    try:
        with open(cmake_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            cmake_content = f.read()
    except Exception as e:
        raise ExtensionCheckError(
            extension_name, check_name,
            f"Failed to read CMakeLists.txt: {str(e)}")
    
    # Parse CMakeLists.txt to find project() declaration
    # Look for patterns like: project(ExtensionName) or project(ExtensionName VERSION ...)
    # Handle multi-line project declarations and various whitespace
    project_pattern = r'project\s*\(\s*([^\s\)\n\r]+)'
    matches = re.findall(project_pattern, cmake_content, re.IGNORECASE | re.MULTILINE)
    
    if not matches:
        raise ExtensionCheckError(
            extension_name, check_name,
            "No project() declaration found in CMakeLists.txt")
    
    cmake_project_name = matches[0].strip().strip('"').strip("'")
    
    # Check if the project name matches the extension name
    if cmake_project_name != extension_name:
        raise ExtensionCheckError(
            extension_name, check_name,
            f"CMakeLists.txt project name '{cmake_project_name}' does not match extension name '{extension_name}'")

def check_dependencies(directory):
    import os
    required_extensions = {}  # for each extension it contains a list of extensions that require it
    available_extensions = []
    for filename in os.listdir(directory):
        f = os.path.join(directory, filename)
        if not os.path.isfile(f) or not filename.endswith(".json"):
            continue
        extension_name, extension = os.path.splitext(os.path.basename(filename))
        if extension != ".json":
            continue
        try:
            extension_description = parse_json(f)
        except ExtensionParseError as exc:
            print(exc)
            continue
        available_extensions.append(extension_name)
        if 'depends' not in extension_description:
            continue
        dependencies = extension_description['depends']
        for dependency in dependencies:
            if dependency in required_extensions:
                required_extensions[dependency].append(extension_name)
            else:
                required_extensions[dependency] = [extension_name]
    print(f"Checked dependency between {len(available_extensions)} extensions.")
    errors_found = []
    for extension in required_extensions:
        if extension in available_extensions:
            # required extension is found
            continue
        required_by_extensions = ', '.join(required_extensions[extension])
        errors_found.append(f"{extension} extension is not found. It is required by extension: {required_by_extensions}.")
    if errors_found:
        raise ExtensionDependencyError(errors_found)

def print_categories(directory):
    import os
    extensions_for_categories = {}  # for each category it contains a list of extensions
    for filename in os.listdir(directory):
        f = os.path.join(directory, filename)
        if not os.path.isfile(f) or not filename.endswith(".json"):
            continue
        extension_name, extension = os.path.splitext(os.path.basename(filename))
        if extension != ".json":
            continue
        try:
            extension_description = parse_json(f)
        except ExtensionParseError as exc:
            print(exc)
            continue
        category = extension_description.get("category", "")
        if not category:
            continue
        if extensions_for_categories.get(category) is None:
            extensions_for_categories[category] = []
        extensions_for_categories[category].append(extension_name)
    print(f"[\n{'\n'.join(f'    "{category}",' for category in sorted(extensions_for_categories.keys()))}\n]")

def main():
    parser = argparse.ArgumentParser(
        description='Validate extension description files.')
    parser.add_argument("--extension-descriptions-folder", help="Folder containing extension description files")
    parser.add_argument("--report-file", help="Write report to markdown file")
    parser.add_argument("extension_description_files", nargs='*', help="Extension JSON files to validate")
    parser.add_argument("--print-categories", action='store_true',
                        help="Print categories of extensions in the specified folder")
    args = parser.parse_args()

    extension_descriptions_folder = "."
    if args.extension_descriptions_folder:
        extension_descriptions_folder = args.extension_descriptions_folder

    if args.print_categories:
        print_categories(extension_descriptions_folder)
        return 0

    success = True

    if args.report_file:
        if not args.report_file.endswith(".md"):
            raise ValueError("Report file must have .md extension")
        with open(args.report_file, 'w', encoding='utf-8') as f:
            # Clear the report file
            f.write("")

    def _log_message(message, message_type=None):
        plain_message_prefix = ""
        markdown_message_prefix = ""
        if message_type == "error":
            plain_message_prefix = "FAIL: "
            markdown_message_prefix = "- ❌ "
        elif message_type == "warning":
            plain_message_prefix = "WARNING: "
            markdown_message_prefix = "- ⚠️ "
        elif message_type == "success":
            plain_message_prefix = "PASS: "
            markdown_message_prefix = "- ✅ "
        print(f"{plain_message_prefix}{message}")
        if args.report_file:
            with open(args.report_file, 'a', encoding='utf-8') as f:
                f.write(f"{markdown_message_prefix}{message}\n")

    failed_extensions = set()
    for file_path in args.extension_description_files:
        file_extension = os.path.splitext(file_path)[1]
        if file_extension != '.json':
            # not an extension description file, ignore it
            print(f"Skipping {file_path} (not a .json file)")
            continue
        full_path = os.path.join(extension_descriptions_folder, file_path)
        if not os.path.isfile(full_path):
            # not a file in the extensions descriptions folder, ignore it
            print(f"Skipping {file_path} (not a file in the extensions descriptions folder)")
            continue
        extension_name = os.path.splitext(os.path.basename(file_path))[0]
        _log_message(f"## Extension: {extension_name}")
        try:
            metadata = parse_json(file_path)
            url = metadata.get("scm_url", "").strip()
            _log_message(f"Repository URL: {url}\n")
        except ExtensionParseError as exc:
            _log_message(f"Failed to parse extension description file: {exc}", "error")
            success = False
            failed_extensions.add(extension_name)
            continue

        cloned_repository_folder = None
        try:
            cloned_repository_folder = clone_repository(metadata["scm_url"], metadata.get("scm_revision", ""))
            print(f"Cloned repository to {cloned_repository_folder}")

            # Log the top-level CMakeLists.txt file content
            cmake_file_path = os.path.join(cloned_repository_folder, "CMakeLists.txt")
            if os.path.isfile(cmake_file_path):
                with open(cmake_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    cmake_content = f.read()
                _log_message(f"Top-level CMakeLists.txt content:\n```\n{cmake_content}```\n")
            
            # Log the LICENSE.txt file content
            license_file_path = os.path.join(cloned_repository_folder, "LICENSE.txt")
            license_file_found = None
            if os.path.isfile(license_file_path):
                license_file_found = license_file_path
            else:
                # Check for other common license file names
                alternative_names = ["LICENSE", "License.txt", "license.txt", "COPYING", "COPYING.txt"]
                for alt_name in alternative_names:
                    alt_path = os.path.join(cloned_repository_folder, alt_name)
                    if os.path.isfile(alt_path):
                        license_file_found = alt_path
                        break
            
            if license_file_found:
                try:
                    with open(license_file_found, 'r', encoding='utf-8', errors='ignore') as f:
                        license_content = f.read()
                    license_filename = os.path.basename(license_file_found)
                    if len(license_content) > 1000:
                        license_content = license_content[:1000] + "\n...\n"
                    _log_message(f"License file ({license_filename}) content:\n```\n{license_content}```\n")
                except Exception as e:
                    _log_message(f"Failed to read license file: {str(e)}", "error")
                    success = False
            else:
                _log_message("No license file found in repository root", "error")
                success = False

        except ExtensionCheckError as exc:
            _log_message(f"Failed to clone repository: {exc}", "error")
            success = False
            failed_extensions.add(extension_name)

        extension_description_checks = [
            ("Check category", check_category, {}),
            ("Check git repository name", check_git_repository_name, {}),
            ("Check SCM URL syntax", check_scm_url_syntax, {}),
            ("Check repository content", check_extension_repository_content, {"cloned_repository_folder": cloned_repository_folder}),
            ]
        for check_description, check, check_kwargs in extension_description_checks:
            try:
                details = check(extension_name, metadata, **check_kwargs)
                _log_message(f"{check_description} completed successfully", "success")
                if details:
                    _log_message(details)
            except ExtensionCheckError as exc:
                _log_message(f"{check_description} failed: {exc}", "error")
                failed_extensions.add(extension_name)
                success = False

        # Clean up temporary directory
        if cloned_repository_folder and os.path.exists(cloned_repository_folder):
            try:
                shutil.rmtree(cloned_repository_folder)
            except Exception:
                print(f"Failed to clean up cloned repository folder: {cloned_repository_folder}")

    if args.extension_description_files and len(args.extension_description_files) > 1:
        _log_message("## Extensions test summary")
        _log_message(f"Checked {len(args.extension_description_files)} extension description files.")
        if failed_extensions:
            _log_message(f"Checks failed for {len(failed_extensions)} extensions: {', '.join(failed_extensions)}", "error")

    try:
        _log_message("## Extension dependencies")
        check_dependencies(extension_descriptions_folder)
        _log_message("Dependency check completed successfully", "success")
    except ExtensionDependencyError as exc:
        _log_message(f"Dependency check failed: {exc}", "error")
        success = False

    return 0 if success else 1


REPOSITORY_NAME_CHECK_EXCEPTIONS = [
    "3DMetricTools",
    "ai-assisted-annotation-client",
    "aigt",
    "AnglePlanes-Extension",
    "AnomalousFiltersExtension",
    "BoneTextureExtension",
    "CarreraSlice",
    "ChangeTrackerPy",
    "CMFreg",
    "CurveMaker",
    "DatabaseInteractorExtension",
    "dcmqi",
    "DSC_Analysis",
    "EasyClip-Extension",
    "ErodeDilateLabel",
    "FilmDosimetryAnalysis",
    "GelDosimetryAnalysis",
    "GyroGuide",
    "iGyne",
    "ImageMaker",
    "IntensitySegmenter",
    "MeshStatisticsExtension",
    "MeshToLabelMap",
    "ModelClip",
    "MONAILabel",
    "mpReview",
    "NeedleFinder",
    "opendose3d",
    "OsteotomyPlanner",
    "PBNRR",
    "PedicleScrewSimulator",
    "PercutaneousApproachAnalysis",
    "PerkTutor",
    "PET-IndiC",
    "PETLiverUptakeMeasurement",
    "PETTumorSegmentation",
    "PickAndPaintExtension",
    "PkModeling",
    "PortPlacement",
    "Q3DCExtension",
    "QuantitativeReporting",
    "ResectionPlanner",
    "ScatteredTransform",
    "Scoliosis",
    "SegmentationAidedRegistration",
    "SegmentationReview",
    "SegmentRegistration",
    "ShapePopulationViewer",
    "ShapeRegressionExtension",
    "ShapeVariationAnalyzer",
    "SkullStripper",
    "SNRMeasurement",
    "SPHARM-PDM",
    "T1Mapping",
    "TCIABrowser",
    "ukftractography",
    "VASSTAlgorithms",
]

ACCEPTED_EXTENSION_CATEGORIES = [
    "Active Learning",
    "Analysis",
    "Auto3dgm",
    "BigImage",
    "Cardiac",
    "Chest Imaging Platform",
    "Conda",
    "Converters",
    "DICOM",
    "DSCI",
    "Developer Tools",
    "Diffusion",
    "Examples",
    "Exporter",
    "FTV Segmentation",
    "Filtering",
    "Filtering.Morphology",
    "Filtering.Vesselness",
    "Holographic Display",
    "IGT",
    "Informatics",
    "Netstim",
    "Neuroimaging",
    "Nuclear Medicine",
    "Orthodontics",
    "Osteotomy Planning",
    "Otolaryngology",
    "Photogrammetry",
    "Pipelines",
    "Planning",
    "Printing",
    "Quantification",
    "Radiotherapy",
    "Registration",
    "Remote",
    "Rendering",
    "SPHARM",
    "Segmentation",
    "Sequences",
    "Shape Analysis",
    "Shape Regression",
    "Shape Visualization",
    "Simulation",
    "SlicerCMF",
    "SlicerMorph",
    "Spectral Imaging",
    "Supervisely",
    "Surface Models",
    "SurfaceLearner",
    "Tomographic Reconstruction",
    "Tracking",
    "Tractography",
    "Training",
    "Ultrasound",
    "Utilities",
    "Vascular Modeling Toolkit",
    "Virtual Reality",
    "VisSimTools",
    "Web System Tools",
    "Wizards",
]

if __name__ == "__main__":
    main()
