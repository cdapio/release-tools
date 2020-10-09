# CDAP Release Tools
## Prerequisites
Ensure you have the following components installed before proceeding:
* Python 3 (with pip)
* Git
* Run [setup.sh](/setup.sh)

## Introduction
This repository contains tools for automating the tedious and error-prone tasks that are a part of CDAP OSS Release. So far the following processes have be automated:

1. Bumping of artifact versions before and after release
1. Generating Release Notes from JIRA tickets
1. Collecting copyright licenses for third-party dependencies

Each automated process has a section that outlines how to use the scripts and the expected results

## Bump Versions
The **[modifyVersions](/modifyVersions.py)** script automatically bumps versions in release branches across all repos listed in [repos.txt](/repos.txt) and creates PRs against those branches for review. The script is most autonomous except for when it encounters an error, at which point it will confirm with the user what the correct path forward is. Examples of errors that require user intervention:
* PR for this change already exists
* POM file versions are not in the expected format
* Release branch is depending on a SNAPSHOT version of CDAP

### Usage
The script expects two arguments:

`modifyVersions.py [version] {remove_snapshot, bump_to_snapshot}`

* **version:** The version string for the current release. This will be used to determine which release branches should be changed (ex. 6.1.4)
* **remove_snapshot**, **bump_to_version** or **update_submodules**: This determines which operation should be performed: 

  * **remove_snapshot**:  This should be run **before** a release. This will ensure all `-SNAPSHOT` versions are updated to the next non-SNAPSHOT version in preparation for building and releasing. This operation also runs update_submodules to ensure everything is synced.
  * **bump_to_snapshot**:  This should be run **after** a release. This will ensure all versions are updated to the next `-SNAPSHOT` version to allow development to continue on that branch for the next patch release. This operation also runs update_submodules to ensure everything is synced.
  * **update_submodules**:  This can be run at any time. This will update the submodules in all repos to use the newest commit from their respective repo and branch. 

## Generate Release Notes
The **[generateReleaseNotes](/generateReleaseNotes.py)** script automatically extracts Release Notes from all JIRA tickets targeted for this release and compiles the result into a reStructuredText file (`.rst`). A small example of the generated rst file can be seen below:

```

New Features
------------
- `CDAP-16690 <https://issues.cask.co/browse/CDAP-16690>`_ - Added revamped preview tab with new Record view for large schemas.
Improvements
------------
- `CDAP-16668 <https://issues.cask.co/browse/CDAP-16668>`_ - Adding support for creating autoscale dataproc cluster.
- `CDAP-16682 <https://issues.cask.co/browse/CDAP-16682>`_ - When backend is slow to respond to requests from UI, we now show a snackbar saying there's a delay.

Bug Fixes
---------
- `CDAP-12499 <https://issues.cask.co/browse/CDAP-12499>`_ - Clarified error message for when branches of a conditional are used as inputs to the same node.
```

### Usage
The script expects two argument with one optional flag:

`generateReleaseNotes.py [version] [username] [--output OUTPUT]`

* **version:** The version string for the current release. Only JIRA tickets with a "Fix Version" matching this version will be retrieved (ex. 6.1.4)
* **username:** The username to use for authenticating with JIRA to fetch the release notes. You will be promoted for the password once the script is running. 
* **--output** (optional): Specify an output path/file for the generated release notes. Default behavior is to generate a `releaseNotes.rst` file in the current directory.

## Collecting Third-Party Copyright Licenses
The **[generateLicenses](/generateLicenses.py)** script automatically collects third-party dependency copyright licenses and creates a PR against the cdap repository to place them in the [COPYRIGHT folder](https://github.com/cdapio/cdap/tree/develop/cdap-distributions/src/COPYRIGHT). This script does not guarantee that all licenses will be automatically collected, it is a best-effort approach. The script generates two summary files:

* **summary.tsv**: which contains a summary of all dependencies seen by the script (even if the license could not be automatically fetched). This is a tab separated file that contains the dependency name, a link to the source code and the name of the copyright license it uses. 

* **missingSummary.tsv**: which contains the details for dependencies that could not be processed automatically. This almost always occurs because the license could not found at the source code URL. The best way to resolve this issue is to manually find the correct GitHub repo (or direct link to the license file) and add it to the **[artifactToRepoMap file](/artifactToRepoMap.csv)**. Be sure to remove the version number from the artifact name before adding it to the mapping file. Once the mapping file is updated you can rerun the script to generate all licenses.

### Usage
The script expects one argument with one optional flag:

`generateLicenses.py [version] [--output-path OUTPUT_PATH]`

* **version:** The version string for the current release. Only JIRA tickets with a "Fix Version" matching this version will be retrieved (ex. 6.1.4)
* **--output-path** (optional): Alternate path to use for generated summary files. Default is the current directory. 


