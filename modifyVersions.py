from subprocess import call
from os import path
import subprocess
import os
import glob
import re
import sys
import argparse
import git

# Contants for controlling level of output and interaction with filesystem
quiteMode = False
workspaceFolder = 'workspace_versions'  # Update the entry in the .gitignore if this folder name is changed
outputPRsFilename = "PRsToApprove.txt"
reposFilename = "repos.txt"

# Regex patterns needed for updating versions
pomVersionSnapshotRegex = r"<version>([\d\.]*)-SNAPSHOT</version>"
pomVersionRegex = r"<version>([\d\.]*)</version>"
pomCDAPVersionRegex = r"<cdap.version>(.*)</cdap.version>"
pomVersionSub = "<version>\\1</version>"

# Init variables needed later on
submoduleRepos = {}
releaseBranchMap = {}


reposFilePath = path.join(os.getcwd(), reposFilename)
reposFile = open(reposFilePath)
repos = reposFile.read().split("\n")
repos = [r.strip("\n\t ") for r in repos if len(r.strip("\n\t ")) > 0]
reposFile.close()





def getUserResponse(prompt):
    """ Helper function to get a yes/no response from the user """

    resp = input(prompt+'\n')
    while resp.lower() not in ['y', 'n']:
        print("Invalid option.")
        resp = input(prompt+'\n')
    return resp == 'y'


def getRepoPath(repo):
    """ Returns the filesystem path for the given repo """

    return path.join(os.getcwd(), workspaceFolder, git.repoNameToPath(repo))

def removeSnapshot(repo, version):
    """
    Removes -SNAPSHOT from all pom.xml files in a given repo in the given release branch.
    Steps taken by this function:
        1. Convert version to release branch name, either using name construction or the releaseBranchMap generated earlier on
        2. Checkout release branch
        3. Create temp branch for changes
        4. Update all pom files to remove -SNAPSHOT, display warning if a pom file already has a non-SNAPSHOT version
        5. Check that pom is not depending on a SNAPSHOT version of CDAP, display a warning if it is
        6. Commit changes and create a PR
        7. Add a link to the PR in the outputPRsFilename file
    """

    git.cloneRepo(repo)
    releaseBranch = ""
    if repo in releaseBranchMap:
        releaseBranch = releaseBranchMap[repo]
    else:
        versionParts = version.split(".")
        releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])
    
    message="Removing SNAPSHOT for repo '%s' in branch '%s'" % (repo, releaseBranch)
    printHeader(message)

    # Checkout to correct branches
    git.checkoutBranch(repo, releaseBranch)
    changeBranch = "release-remove-snapshot-%s" % version.replace('.', '')
    git.checkoutBranch(repo, changeBranch, createBranch=True)

    # Get all pom files
    pomFilePaths = glob.glob("/%s/**/pom.xml" % getRepoPath(repo), recursive=True)
    totalChanges = 0
    for pomFile in pomFilePaths:
        pom = ""
        prettyPomFile = pomFile.replace(os.getcwd(), '')
        with open(pomFile) as pf:
            pom = pf.read()

        # Replace remove -SNAPSHOT from version field
        beforeLength = len(pom)
        pom = re.sub(pomVersionSnapshotRegex, pomVersionSub, pom, 1, re.MULTILINE)
        afterLength = len(pom)

        # Check if the pom file was changed
        totalChanges += afterLength-beforeLength
        if beforeLength == afterLength:
            # If the pom file was not changed that means the version is already a non-SNAPSHOT version, ask the user if thats expected
            currentVersion = re.search(pomVersionRegex, pom, re.MULTILINE).groups()[0]
            skipPom = getUserResponse(
                "POM file ('%s') is already set to a non-SNAPSHOT version (%s), is this expected? (Y/n)" % (prettyPomFile, currentVersion))
            if skipPom:
                continue

            # If the user does not skip this pom file then we cannot recover, we need to quit and delete the repo to undo all changes so far
            print("This script expects all repos to be in the SNAPSHOT stage. All changes to this repo (%s) will be reverted and it will be skipped." % repo)
            git.deleteLocalRepo(repo)
            return

        # Check if the CDAP dependancy is a SNAPSHOT version
        cdapVersionMatch = re.search(pomCDAPVersionRegex, pom, re.MULTILINE)
        if cdapVersionMatch:
            cdapVersion = cdapVersionMatch.groups()[0]
            if cdapVersion.endswith("-SNAPSHOT"):
                print("POM file ('%s') depends on a SNAPSHOT version of CDAP(%s). This is not allowed." % (prettyPomFile, cdapVersion))
                autoUpdate = getUserResponse("Would you like to remove the SNAPSHOT from the CDAP version dependancy? "
                                            + "If you are unsure please consult the team, you can skip this repo for now by responding with 'N'. (Y/n)")

                # If the user does not want to update this dependacy then we need to skip this repo, we do not allow a release with a SNAPSHOT dependancy
                if not autoUpdate:
                    git.deleteLocalRepo(repo)
                    return
                newCdapVersion = '<version>%s</version>' % cdapVersion.replace("-SNAPSHOT", '')
                pom = re.sub(pomCDAPVersionRegex, newCdapVersion, pom, 1, re.MULTILINE)

        with open(pomFile, 'w') as pf:
            pf.write(pom)

    # If no changes were made to this repo then delete the branch and return
    if totalChanges == 0:
        print("No changes were made to repo '%s'...deleting local branch and continuing. No PR will be generated for this repo." % repo)
        git.checkoutBranch(releaseBranch)
        git.deleteBranch(changeBranch)
        return

    # Create PR
    git.addAndCommit(repo, "-A", "Removed SNAPSHOT from pom files.")
    git.pushAndCreatePR(repo, "[RELEASE-%s] Remove SNAPSHOTs" % version,
                    "This is an automated PR to remove -SNAPSHOT from artifact versions to prepare for release.", changeBranch, releaseBranch)

def printHeader(message):
    print('\n'+'='*len(message))
    print(message)


def bumpVersionToSnapshot(repo, version):
    """
    Bumps versions of all pom.xml files to the next SNAPSHOT in a given repo in the given release branch.
    Steps taken by this function:
        1. Convert version to release branch name, either using name construction or the releaseBranchMap generated earlier on
        2. Checkout release branch
        3. Create temp branch for changes
        4. Update all pom files to next snapshot version ex. 6.1.4 -> 6.1.5-SNAPSHOT
            * Display warning if a pom file already has a SNAPSHOT version
        5. Check that pom is not depending on a SNAPSHOT version of CDAP, display a warning if it is
        6. Commit changes and create a PR
        7. Add a link to the PR in the outputPRsFilename file
    """
    
    git.cloneRepo(repo)
    releaseBranch = ""
    if repo in releaseBranchMap:
        releaseBranch = releaseBranchMap[repo]
    else:
        versionParts = version.split(".")
        releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])

    message="Bumping versions in repo '%s' on branch '%s'"%(repo, releaseBranch)
    printHeader(message)

    # Checkout branches
    git.checkoutBranch(repo, releaseBranch)
    changeBranch = "release-bump-versions-%s" % version.replace('.', '')
    git.checkoutBranch(repo, changeBranch, createBranch=True)

    changesMade = False
    firstValidVersion = None
    pomFilePaths = glob.glob("/%s/**/pom.xml" % getRepoPath(repo), recursive=True)
    for pomFile in pomFilePaths:
        pom = ""
        with open(pomFile) as pf:
            pom = pf.read()

        # Get current version
        currentVersion = re.search(pomVersionRegex, pom, re.MULTILINE).groups()[0]

        # Check if the version is already a SNAPSHOT version, that is not expected
        if '-SNAPSHOT' in currentVersion:
            # Give the user the option to skip this POM file if they expected this
            prettyPomFile = pomFile.replace(os.getcwd(), '')
            skipPom = getUserResponse(
                "POM file ('%s') is already set to a SNAPSHOT version (%s), is this expected? (Y/n)" % (prettyPomFile, currentVersion))
            if skipPom:
                continue

            # If the user chooses not to skip then we need to delete changes to this repo and skip it
            print("This script expects all repos to be in a non-SNAPSHOT stage. All changes to this repo (%s) will be reverted and it will be skipped." % repo)
            git.deleteLocalRepo(repo)
            return

        #Keep track of the first valid version we see for tagging step later on
        if firstValidVersion is None:
            firstValidVersion = currentVersion

        # Calculate new version and replace it
        currentVersionParts = currentVersion.split(".")
        currentVersionParts[-1] = str(int(currentVersionParts[-1])+1)
        newVersion = '<version>%s-SNAPSHOT</version>' % '.'.join(currentVersionParts)
        pom = re.sub(pomVersionRegex, newVersion, pom, 1, re.MULTILINE)
        changesMade = True
        with open(pomFile, 'w') as pf:
            pf.write(pom)

     # If no changes were made to this repo then delete the branch and return
    if not changesMade:
        print("No changes were made to repo '%s'...deleting local branch and continuing. No PR will be generated for this repo." % repo)
        git.checkoutBranch(releaseBranch)
        git.deleteBranch(changeBranch)
        return

    # Create PR
    git.addAndCommit(repo, "-A", "Bumped versions to next SNAPSHOT.")
    git.pushAndCreatePR(repo, "[RELEASE-%s] Bump to SNAPSHOT" % version,
                    "This is an automated PR to bump artifact versions to SNAPSHOT after a release is completed.", changeBranch, releaseBranch)

    #Tag the release branch in the repo with the current version
    git.checkoutBranch(repo, releaseBranch)
    git.tagRepo(repo, 'v'+firstValidVersion)
        

def updateSubmodules(version):
    """ This function updates submodules in hydrator-plugins and cdap-build and creates PR for them """

    submoduleRepos = ['cdapio/hydrator-plugins','cdapio/cdap-ui','cdapio/cdap-build']
    for repo in submoduleRepos:

        # Construct release branch name and checkout the branches
        releaseBranch = ""
        if repo in releaseBranchMap:
            releaseBranch = releaseBranchMap[repo]
        else:
            versionParts = version.split(".")
            releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])

        printHeader("Updating submodules in %s"%repo)
        print("Setting up for submodule update in repo '%s'" % repo)
        git.cloneRepo(repo)
        git.checkoutBranch(repo, releaseBranch)
        changeBranch = 'release-update-submodules-%s' % version.replace('.', '')

        # Try to create the branch for changes
        try:
            git.checkoutBranch(repo, changeBranch, createBranch=True)
        except RuntimeError as e:  # This means there was some unrecoverable error
            sys.stderr.write("ERROR: Branch creation failed, cannot update submodules")
            return
        except Exception as e:  # This means there is already a PR and it has the correct changes
            input("Press Enter once the PR is reviewed and merged...")
            continue

        # Run the update and confirm that at least one submodule was updated
        if updateModulesAndCheck(repo):
            print("Creating PR...")
            git.addAndCommit(repo, '-A', "Updated submodules for release")
            url = git.pushAndCreatePR(repo, "[RELEASE-%s] Update submodules" % version,
                                  "This is an automated PR to update submodules in preperation for release.", changeBranch, releaseBranch, outputURLToFile=False)
            print("PR for updating submodules in %s: %s" % (repo, url))
            input("Press Enter once the PR is reviewed and merged...")
        else:
            git.deleteLocalRepo(changeBranch)  # If no changes were made then no need to create a PR, just delete the repo to undo changes


def updateModulesAndCheck(repo):
    """ Helper function to perform submodule update and check that the update was successful """

    # Update modules

    message="Attempting to update submodules in repo '%s'" % repo
    printHeader(message)

    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    commands.append('git submodule update')  # This resets the submodules if there are some modifications from a previous attempt
    commands.append('git submodule update --init --recursive --remote')
    call(" && ".join(commands), shell=True)

    # Check git status to make sure at least one module was updated
    commands.clear()
    commands.append('cd "%s"' % repoPath)
    commands.append("git status")
    statusText = subprocess.check_output(" && ".join(commands), shell=True).decode('utf-8')

    # If there were no submodules updated
    if 'nothing to commit, working tree clean' in statusText:
        # If nothing was updated give the user the option to retry this operation (incase they forgot to merge the PRs for the submodules)
        print('No submodule changes were detected. If this is not expected then please ensure all PRs for submodules were merged. This repo depends on the following submodules: \n%s' %
              '\n'.join(submoduleRepos[repo]))
        prompt = "Would you like to (r)etry updating the submodules or (s)kip this repo? (R/s)"
        resp = input(prompt+'\n')
        while resp.lower() not in ['r', 's']:
            print("Invalid option.")
            resp = input(prompt+'\n')
        if resp == 'r':
            return updateModulesAndCheck(repo)
        print("Skipping submodule update for repo '%s'...local repo will be deleted to clean up" % repo)
        return False

    print("Update successful.")
    return True

def parseArgs():
    """ Parse command line arguments """

    parser = argparse.ArgumentParser(
        description='Script for automatically updating versions and submodules across all repos in preperation for a release.')

    parser.add_argument('version',
                        type=str,
                        help='Version string of this release. Ex. 6.1.4')

    parser.add_argument('operation',
                        choices=['remove_snapshot', 'bump_to_snapshot', 'update_submodules'],
                        help='remove_snapshot will update all versions to the next non-SNAPSHOT version (ex. 6.1.4-SNAPSHOT -> 6.1.4). '
                        + 'bump_to_snapshot will update all versions to the SNAPSHOT version (ex. 6.1.4 -> 6.1.5-SNAPSHOT)')

    parser.add_argument('-v', '--verbose',
                        action='store_true',
                        help='log all command outputs')

    args = parser.parse_args()
    return args


def main():
    global quiteMode, releaseBranchMap, submoduleRepos
    args = parseArgs()
    quiteMode = not args.verbose
    if path.exists(outputPRsFilename):
        os.remove(outputPRsFilename)
    version = args.version
    git.setWorkspaceFolder(workspaceFolder)
    git.setQuiteMode(quiteMode)
    git.setPROutputFilename(outputPRsFilename)
    git.setRepos(repos)
    releaseBranchMap, submoduleRepos = git.mapBranchVersions(version)
    if args.operation != 'update_submodules':
        for repo in repos:
            try:
                if args.operation == 'remove_snapshot':
                    removeSnapshot(repo, version)
                else:
                    bumpVersionToSnapshot(repo, version)
            except Exception as e:
                continue  # Error logging should have been done before getting to this stage

        print("PRs for approval:")
        call("cat %s" % outputPRsFilename, shell=True)

        input("Please review and merge all PRs listed above then press Enter to proceed with updating submodules")
    updateSubmodules(version)


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
