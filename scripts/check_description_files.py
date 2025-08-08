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
def check_category(*_unused_args):
    pass


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

def main():
    parser = argparse.ArgumentParser(
        description='Validate extension description files.')
    parser.add_argument("--extension-descriptions-folder", help="Folder containing extension description files")
    parser.add_argument("--report-file", help="Write report to markdown file")
    parser.add_argument("extension_description_files", nargs='*', help="Extension JSON files to validate")
    args = parser.parse_args()

    extension_descriptions_folder = "."
    if args.extension_descriptions_folder:
        extension_descriptions_folder = args.extension_descriptions_folder

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
        if message_type is "error":
            plain_message_prefix = "FAIL: "
            markdown_message_prefix = "- :x: "
        elif message_type is "warning":
            plain_message_prefix = "WARNING: "
            markdown_message_prefix = "- :warning: "
        elif message_type is "success":
            plain_message_prefix = "PASS: "
            markdown_message_prefix = "- :white_check_mark: "
        print(f"{plain_message_prefix}{message}")
        if args.report_file:
            with open(args.report_file, 'a', encoding='utf-8') as f:
                f.write(f"{markdown_message_prefix}{message}\n")

    extension_description_checks = [
        ("Check category", check_category, {}),
        ("Check git repository name", check_git_repository_name, {}),
        ("Check SCM URL syntax", check_scm_url_syntax, {}),
        ]
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
            _log_message(f"Repository URL: {url}")
            _log_message("Parsed extension description file successfully", "success")
        except ExtensionParseError as exc:
            _log_message(f"Failed to parse extension description file: {exc}", "error")
            success = False
            continue

        for check_description, check, check_kwargs in extension_description_checks:
            try:
                check(extension_name, metadata, **check_kwargs)
                _log_message(f"{check_description} completed successfully", "success")
            except ExtensionCheckError as exc:
                _log_message(f"{check_description} failed: {exc}", "error")
                success = False

    try:
        _log_message("## Extension dependencies", "info")
        check_dependencies(extension_descriptions_folder)
        _log_message("Dependency check completed successfully", "success")
    except ExtensionDependencyError as exc:
        _log_message(f"Dependency check failed: {exc}", "error")
        success = False

    sys.exit(0 if success else 1)


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


if __name__ == "__main__":
    main()
