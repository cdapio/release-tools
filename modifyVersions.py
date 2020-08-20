from subprocess import call
from os import path
import subprocess
import os
import glob
import re
import shutil
import sys
import argparse

# Contants for controlling level of output and interaction with filesystem
quiteMode = False
workspaceFolder = 'workspace'  # Update the entry in the .gitignore if this folder name is changed
outputPRsFilename = "PRsToApprove.txt"
reposFilename = "repos.txt"

# Regex patterns needed for updating versions
gitmodulesRegex = r"url = [\.\.\/]*(.*)$\n^.*branch = [\.\.\/]*(.*)$"
pomVersionSnapshotRegex = r"<version>([\d\.]*)-SNAPSHOT</version>"
pomVersionRegex = r"<version>([\d\.]*)</version>"
pomCDAPVersionRegex = r"<cdap.version>(.*)</cdap.version>"
pomVersionSub = "<version>\\1</version>"

# Init variables needed later on
submoduleRepos = {}
releaseBranchMap = {}
repoBranchMap = {}

reposFilePath = path.join(os.getcwd(), reposFilename)
reposFile = open(reposFilePath)
repos = reposFile.read().split("\n")
repos = [r.strip("\n\t ") for r in repos if len(r.strip("\n\t ")) > 0]
reposFile.close()


def getFullRepoName(partialRepo):
    """
    Returns the full repo path given the repo name
    Ex. getFullRepoName('cdap') -> 'cdapio/cdap'
    """
    for r in repos:
        if r.endswith(partialRepo):
            return r
    return partialRepo


def repoNameToPath(repoName):
    """ Extracts the repo name from a full path """

    return repoName.split("/")[-1]


def getUserReponse(prompt):
    """ Helper function to get a yes/no response from the user """

    resp = input(prompt+'\n')
    while resp.lower() not in ['y', 'n']:
        print("Invalid option.")
        resp = input(prompt+'\n')
    return resp == 'y'


def getRepoPath(repo):
    """ Returns the filesystem path for the given repo """

    return path.join(os.getcwd(), workspaceFolder, repoNameToPath(repo))


def cloneRepo(repo):
    """
    Clones a given repo into the workspace folder.
    If the repo is already cloned then this function clears all local changes and pulls the newest from remote
    """

    repoPath = getRepoPath(repo)
    # If the repo already exists then just clear any local changes
    if path.exists(repoPath):
        commands = []
        commands.append('cd "%s"' % repoPath)
        commands.append("git reset --hard")
        commands = [c + " > /dev/null" if quiteMode else c for c in commands]
        call(" && ".join(commands), shell=True)
    else:
        if not repo.endswith(".git"):
            repo += ".git"

        call("mkdir %s; cd %s && git clone git@github.com:%s" % (workspaceFolder, workspaceFolder, repo), shell=True)


def getAllBranches(repo):
    """ Helper function that gets a full list of all remote branches for a given repo """

    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    commands.append('git ls-remote --heads origin')
    out = subprocess.check_output(" && ".join(commands), shell=True).decode('utf-8')
    lines = out.split("\n")
    branchNames = [line.split('\t')[1].replace('refs/heads/', '')for line in lines if '\t' in line]
    return branchNames


def checkoutBranch(repo, branch, createBranch=False):
    """ Checks a given repo onto a given branch, also has the ability to create a new branch.
    A lot of error checking/handling occurs in this function to prevent the script from getting
    into a bad state (ex. crashloop because the branch it is trying to create already exists)

    When creating a branch this function does the following:
        1. Check if the branch can be created locally
        2. Check if the branch already exists in remote
        3. If (1) succeeds and (2) is false then we are done.
        4. Getting to this step means we are in a bad state, we should delete the current branches to unblock but first:
            a. Check if there is already a PR open for this branch
                * If yes, then ask the user to confirm if it contains the correct changes
                    - If the changes are correct then we can skip this whole repo, raise an exception to exit
                    - If the changes are incorrect then close the PR and proceed with the next bullet point
                * If no, then we can safely delete this branch without losing any work
        5. Delete the remote branch, if it exists in remote
        6. Delete the local branch, if the local checkout failed
        7. Call checkoutBranch again to retry now that everything is cleaned up
        """

    global repoBranchMap

    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    if createBranch:
        repoBranchMap[repo] = getAllBranches(repo)
        commands.append("git checkout -b %s" % branch)
    else:
        commands.append("git checkout %s" % branch)
        commands.append("git pull --all")
    commands = [c + " > /dev/null 2>&1" if quiteMode else c for c in commands]

    createBranchExitCode = call(" && ".join(commands), shell=True)
    existsInRemote = branch in repoBranchMap[repo] if createBranch else False

    # This usually means the branch already exists, this would happen if the user re-ran the script after stopping it halfway
    if (createBranchExitCode != 0 or existsInRemote) and createBranch:
        print("Failed to create branch '%s' in repo '%s', a branch with that name already exists" % (
            branch, repo))
        # Check if there is already a PR for this branch
        commands.clear()
        commands.append('cd "%s"' % repoPath)
        commands.append('BRANCH=`git rev-parse --abbrev-ref HEAD`')
        commands.append("../../gh pr checkout %s" % branch)
        commands.append("git checkout $BRANCH")
        commands = [
            c + " > /dev/null 2>&1" if quiteMode else c for c in commands]
        exitCode = call(" && ".join(commands), shell=True)

        # If exit code is zero that means there is a PR for this branch
        if exitCode == 0:
            print("A PR for this branch has already been created (maybe this script was already run for this release?)")
            print("Please review the PR to determine if the correct changes are already present.")
            input("To view this PR in a browser, press Enter...")
            commands.clear()
            commands.append('cd "%s"' % repoPath)
            commands.append("../../gh pr view --web %s >> ../../%s" % (branch, outputPRsFilename))
            commands = [c + "> /dev/null" if quiteMode else c for c in commands]
            call(" && ".join(commands), shell=True)
            isPRCorrect = getUserReponse("Does the PR contain the correct changes? (Y/n)")
            if isPRCorrect:
                print("Skipping re-processing this repo since correct PR already exists")
                raise Exception()

            # PR is incorrect, it should be closed
            print("Closing incorrect PR")
            commands.clear()
            commands.append('cd "%s"' % repoPath)
            commands.append("../../gh pr close %s" % branch)
            commands = [c + "> /dev/null" if quiteMode else c for c in commands]
            call(" && ".join(commands), shell=True)

        # If there is no PR or the PR is not correct, delete the branch and try again
        print("Deleting existing branch and recreating it...")
        code = 0
        if existsInRemote:
            code += deleteBranch(repo, branch, deleteInRemote=True)
        if createBranchExitCode != 0:
            code += deleteBranch(repo, branch)

        if code != 0:
            print("ERROR: failed to delete branch '%s' in repo '%s'. Please resolve this issue manually and rerun the script" % (
                branch, repo))
            raise RuntimeError()
        if createBranchExitCode != 0:
            checkoutBranch(repo, branch, createBranch)


def deleteBranch(repo, branch, deleteInRemote=False):
    """ Deletes a branch in a given repo in either local or remote """

    commands = []
    repoPath = getRepoPath(repo)
    commands.append('cd "%s"' % repoPath)
    if deleteInRemote:
        commands.append("git push origin --delete %s" % branch)
    else:
        commands.append("git branch -D %s" % branch)
    commands = [c + " > /dev/null" if quiteMode else c for c in commands]
    return call(" && ".join(commands), shell=True)


def addAndCommit(repo, filesToAdd, commitMessage):
    """ Adds given files in a given repo and commits the changes """

    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    commands.append('git add %s' % filesToAdd)
    commands.append('git commit -m "%s"' % commitMessage)
    commands = [c + " > /dev/null" if quiteMode else c for c in commands]
    call(" && ".join(commands), shell=True)


def pushAndCreatePR(repo, title, body, currentBranch, targetBranch, outputURLToFile=True):
    """
    Pushes the commited changes in a given repo and creates a PR with the given title and body.
    By default a link to the PR will be saved to a file, if outputURLToFile is set to false the link will be returned by this function
    """

    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    if outputURLToFile:
        commands.append("git push origin %s -f" % currentBranch)
        commands.append('../../gh pr create --title "%s" --body "%s" --base %s --label automated-release >> ../%s' %
                        (title, body, targetBranch, outputPRsFilename))
    else:
        commands.append("git push origin %s -f > /dev/null 2>&1" % currentBranch)
        commands.append('../../gh pr create --title "%s" --body "%s" --base %s --label automated-release' % (title, body, targetBranch))
    prLink = subprocess.check_output(
        " && ".join(commands), shell=True).decode('utf-8')
    if not outputURLToFile:
        return prLink  # Return PR URL


def deleteLocalRepo(repo):
    """ Deletes the local copy of the repo to force-remove all local changes """

    print("Reverting repo %s" % repo)
    repoPath = getRepoPath(repo)
    shutil.rmtree(repoPath)
    print("Revert of %s is complete, please resolve this issue and try again. You may edit the repos.txt file to only target this affected repo in future runs." % repo)


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

    cloneRepo(repo)
    releaseBranch = ""
    if repo in releaseBranchMap:
        releaseBranch = releaseBranchMap[repo]
    else:
        versionParts = version.split(".")
        releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])
    print("Removing SNAPSHOT for repo '%s' in branch '%s'" % (repo, releaseBranch))

    # Checkout to correct branches
    checkoutBranch(repo, releaseBranch)
    changeBranch = "release-remove-snapshot-%s" % version.replace('.', '')
    checkoutBranch(repo, changeBranch, createBranch=True)

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
            skipPom = getUserReponse(
                "POM file ('%s') is already set to a non-SNAPSHOT version (%s), is this expected? (Y/n)" % (prettyPomFile, currentVersion))
            if skipPom:
                continue

            # If the user does not skip this pom file then we cannot recover, we need to quit and delete the repo to undo all changes so far
            print("This script expects all repos to be in the SNAPSHOT stage. All changes to this repo (%s) will be reverted and it will be skipped." % repo)
            deleteLocalRepo(repo)
            return

        # Check if the CDAP dependancy is a SNAPSHOT version
        cdapVersionMatch = re.search(pomCDAPVersionRegex, pom, re.MULTILINE)
        if cdapVersionMatch:
            cdapVersion = cdapVersionMatch.groups()[0]
            if cdapVersion.endswith("-SNAPSHOT"):
                print("POM file ('%s') depends on a SNAPSHOT version of CDAP(%s). This is not allowed." % (prettyPomFile, cdapVersion))
                autoUpdate = getUserReponse("Would you like to remove the SNAPSHOT from the CDAP version dependancy? "
                                            + "If you are unsure please consult the team, you can skip this repo for now by responding with 'N'. (Y/n)")

                # If the user does not want to update this dependacy then we need to skip this repo, we do not allow a release with a SNAPSHOT dependancy
                if not autoUpdate:
                    deleteLocalRepo(repo)
                    return
                newCdapVersion = '<version>%s</version>' % cdapVersion.replace("-SNAPSHOT", '')
                pom = re.sub(pomCDAPVersionRegex, newCdapVersion, pom, 1, re.MULTILINE)

        with open(pomFile, 'w') as pf:
            pf.write(pom)

    # If no changes were made to this repo then delete the branch and return
    if totalChanges == 0:
        print("No changes were made to repo '%s'...deleting local branch and continuing. No PR will be generated for this repo." % repo)
        checkoutBranch(releaseBranch)
        deleteBranch(changeBranch)
        return

    # Create PR
    addAndCommit(repo, "-A", "Removed SNAPSHOT from pom files.")
    pushAndCreatePR(repo, "[RELEASE-%s] Remove SNAPSHOTs" % version,
                    "This is an automated PR to remove -SNAPSHOT from artifact versions to prepare for release.", changeBranch, releaseBranch)


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

    cloneRepo(repo)
    releaseBranch = ""
    if repo in releaseBranchMap:
        releaseBranch = releaseBranchMap[repo]
    else:
        versionParts = version.split(".")
        releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])

    # Checkout branches
    checkoutBranch(repo, releaseBranch)
    changeBranch = "release-bump-versions-%s" % version.replace('.', '')
    checkoutBranch(repo, changeBranch, createBranch=True)

    changesMade = False
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
            skipPom = getUserReponse(
                "POM file ('%s') is already set to a SNAPSHOT version (%s), is this expected? (Y/n)" % (prettyPomFile, currentVersion))
            if skipPom:
                continue

            # If the user chooses not to skip then we need to delete changes to this repo and skip it
            print("This script expects all repos to be in a non-SNAPSHOT stage. All changes to this repo (%s) will be reverted and it will be skipped." % repo)
            deleteLocalRepo(repo)
            return

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
        checkoutBranch(releaseBranch)
        deleteBranch(changeBranch)
        return

    # Create PR
    addAndCommit(repo, "-A", "Bumped versions to next SNAPSHOT.")
    pushAndCreatePR(repo, "[RELEASE-%s] Bump to SNAPSHOT" % version,
                    "This is an automated PR to bump artifact versions to SNAPSHOT after a release is completed.", changeBranch, releaseBranch)


def updateSubmodules(version):
    """ This function updates submodules in hydrator-plugins and cdap-build and creates PR for them """

    submoduleRepos = ['cdapio/hydrator-plugins', 'cdapio/cdap-build']
    for repo in submoduleRepos:

        # Construct release branch name and checkout the branches
        releaseBranch = ""
        if repo in releaseBranchMap:
            releaseBranch = releaseBranchMap[repo]
        else:
            versionParts = version.split(".")
            releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])
        print("Setting up for submodule update in repo '%s'" % repo)
        cloneRepo(repo)
        checkoutBranch(repo, releaseBranch)
        changeBranch = 'release-update-submodules-%s' % version.replace('.', '')

        # Try to create the branch for changes
        try:
            checkoutBranch(repo, changeBranch, createBranch=True)
        except RuntimeError as e:  # This means there was some unrecoverable error
            sys.stderr.write("ERROR: Branch creation failed, cannot update submodules")
            return
        except Exception as e:  # This means there is already a PR and it has the correct changes
            input("Press Enter once the PR is reviewed and merged...")
            continue

        # Run the update and confirm that at least one submodule was updated
        if updateModulesAndCheck(repo):
            addAndCommit(repo, '-A', "Updated submodules for release")
            url = pushAndCreatePR(repo, "[RELEASE-%s] Update submodules" % version,
                                  "This is an automated PR to update submodules in preperation for release.", changeBranch, releaseBranch, outputURLToFile=False)
            print("PR for updating submodules in %s: %s" % (repo, url))
            input("Press Enter once the PR is reviewed and merged...")
        else:
            deleteLocalRepo(changeBranch)  # If no changes were made then no need to create a PR, just delete the repo to undo changes


def updateModulesAndCheck(repo):
    """ Helper function to perform submodule update and check that the update was successful """

    # Update modules
    print("Attempting to update submodules in repo '%s'" % repo)
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

    print("Update successful")
    return True


def mapBranchVersions(version, repo="cdapio/cdap-build"):
    """ 
    This generates repo-to-branch mappings for release branches. This is required because the version of 
    CDAP (ex. 6.1.4) is not the same as the versions for the other repos that are bundled with it.

    For example, the source code for CDAP 6.1.4 is in the cdap repo in the release/6.1 branch. The hydrator plugins
    for this version are in the hydrator-plugins repo in the release/2.3 branch. We need a mapping that tells us which
    branch should be targetted for each repo.

    This is accomplished by examining the .gitsubmodules file in the cdap-build repo in the corresponding release branch.
    The branches in cdap-build follow the convention 'release/Major.Minor' so given a version string we can find the correct branch.
    From there we can recursively visit the repos that appear in the submodules file and construct a mapping
    """

    # Construct/fetch release branch name
    print("Scanning repo '%s' for submodules" % repo)
    global releaseBranchMap
    versionParts = version.split(".")
    branch = "release/%s.%s" % (versionParts[0], versionParts[1])
    if repo in releaseBranchMap:
        branch = releaseBranchMap[repo]

    # Clone repo and checkout to release branch
    cloneRepo(repo)
    checkoutBranch(repo, branch)
    gitModulesPath = path.join(getRepoPath(repo), ".gitmodules")
    if not path.exists(gitModulesPath):
        return

    modulesFile = open(gitModulesPath)
    moduleContents = modulesFile.read()
    modulesFile.close()

    # Use RegEx to extract all repo names and corresponding release branches from the submodules file
    repoQueue = []
    matches = re.finditer(gitmodulesRegex, moduleContents, re.MULTILINE)
    for matchNum, match in enumerate(matches, start=1):
        newRepo, newBranch = match.groups()
        newRepo = getFullRepoName(newRepo.replace(".git", ""))
        releaseBranchMap[newRepo] = newBranch
        repoQueue.append(newRepo)

    submoduleRepos[repo] = repoQueue
    print("Found %d submodules" % len(repoQueue))
    for repo in repoQueue:
        mapBranchVersions(version, repo)


def parseArgs():
    """ Parse command line arguments """

    parser = argparse.ArgumentParser(
        description='Script for automatically updating versions and submodules across all repos in preperation for a release.')

    parser.add_argument('version',
                        type=str,
                        help='Version string of this release. Ex. 6.1.4')

    parser.add_argument('operation',
                        choices=['remove_snapshot', 'bump_to_snapshot'],
                        help='remove_snapshot will update all versions to the next non-SNAPSHOT version (ex. 6.1.4-SNAPSHOT -> 6.1.4). '
                        + 'bump_to_snapshot will update all versions to the SNAPSHOT version (ex. 6.1.4 -> 6.1.5-SNAPSHOT)')

    parser.add_argument('-v', '--verbose',
                        action='store_true',
                        help='log all command outputs')

    args = parser.parse_args(['6.1.4', 'bump_to_snapshot'])
    return args


def main():
    global quiteMode
    args = parseArgs()
    quiteMode = not args.verbose
    if path.exists(outputPRsFilename):
        os.remove(outputPRsFilename)
    version = args.version
    mapBranchVersions(version)
    bumpVersionToSnapshot('cdap-solutions/dre', version)
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
    updateSubmodules(version)


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
