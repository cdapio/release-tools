from subprocess import call
from os import path
import subprocess
import os
import shutil
import sys
import re

workspaceFolder = ""
quiteMode = True
repoBranchMap = {}
outputPRsFilename="outputPRs_tmp.txt"
repos = []
releaseBranchMap = {}
submoduleRepos = {}

gitmodulesRegex = r"url = [\.\.\/]*(.*)$\n^.*branch = [\.\.\/]*(.*)$"

def setWorkspaceFolder(folder):
    global workspaceFolder
    workspaceFolder=folder

def setQuiteMode(mode):
    global quiteMode
    quiteMode=mode

def setPROutputFilename(name):
    global outputPRsFilename
    outputPRsFilename=name

def setRepos(reposList):
    global repos
    repos=reposList

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
        commands.append("gh pr checkout %s" % branch)
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
            commands.append("gh pr view --web %s >> ../../%s" % (branch, outputPRsFilename))
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
            commands.append("gh pr close %s" % branch)
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
        commands.append('gh pr create --title "%s" --body "%s" --base %s --label automated-release >> ../../%s' %
                        (title, body, targetBranch, outputPRsFilename))
    else:
        commands.append("git push origin %s -f > /dev/null 2>&1" % currentBranch)
        commands.append('gh pr create --title "%s" --body "%s" --base %s --label automated-release' % (title, body, targetBranch))
    prLink = subprocess.check_output(" && ".join(commands), shell=True).decode('utf-8')
    if not outputURLToFile:
        return prLink  # Return PR URL

def tagRepo(repo, tag):
    print("Tagging repo %s with tag '%s'"%(repo, tag))
    commands = []
    repoPath = getRepoPath(repo)
    commands.append('cd "%s"' % repoPath)
    commands.append('git tag %s'%tag)
    commands.append('git push origin %s'%tag)
    code = call(" && ".join(commands), shell=True)
    if code != 0:
        print("Failed to tag repo, tag probably already exists")


def deleteLocalRepo(repo):
    """ Deletes the local copy of the repo to force-remove all local changes """

    print("Reverting repo %s" % repo)
    repoPath = getRepoPath(repo)
    shutil.rmtree(repoPath, ignore_errors=True)
    print("Revert of %s is complete, please resolve this issue and try again. You may edit the repos.txt file to only target this affected repo in future runs." % repo)

def mapBranchVersionsRecurse(version, repo="cdapio/cdap-build"):
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
        mapBranchVersionsRecurse(version, repo)

def mapBranchVersions(version, repo="cdapio/cdap-build"):
    mapBranchVersionsRecurse(version)
    return (releaseBranchMap, submoduleRepos)
