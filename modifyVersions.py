from subprocess import call
import subprocess
from os import path
import os
import glob
import re
import time
import shutil

releaseBranchMap = {}
repoBranchMap = {}

workspaceFolder = 'workspace'
reposFilePath = path.join(os.getcwd(), "repos.txt")
reposFile = open(reposFilePath)
repos = reposFile.read().split("\n")
repos = [r.strip("\n\t ") for r in repos if len(r.strip("\n\t ")) > 0]
reposFile.close()

gitmodulesRegex = r"url = [\.\.\/]*(.*)$\n^.*branch = [\.\.\/]*(.*)$"
pomVersionSnapshotRegex = r"<version>([\d\.]*)-SNAPSHOT</version>"
pomVersionRegex = r"<version>([\d\.]*)</version>"
pomCDAPVersionRegex = r"<cdap.version>(.*)</cdap.version>"
pomVersionSub = "<version>\\1</version>"

submoduleRepos = {}

outputPRsFile = "PRsToApprove.txt"
quiteMode = True


def getFullRepoName(partialRepo):
    for r in repos:
        if r.endswith(partialRepo):
            return r
    return partialRepo


def repoNameToPath(repoName):
    return repoName.split("/")[-1]


def getUserReponse(prompt):
    resp = input(prompt+'\n')
    while resp.lower() not in ['y', 'n']:
        print("Invalid option.")
        resp = input(prompt+'\n')
    return resp == 'y'


def getRepoPath(repo):
    return path.join(os.getcwd(), workspaceFolder, repoNameToPath(repo))


def cloneRepo(repo):
    # If the repo already exists then just clear any local changes
    repoPath = getRepoPath(repo)
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
    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    commands.append('git ls-remote --heads origin')
    out = subprocess.check_output(" && ".join(
        commands), shell=True).decode('utf-8')
    lines = out.split("\n")
    branchNames = [line.split('\t')[1].replace('refs/heads/', '')
                   for line in lines if '\t' in line]
    return branchNames


def checkoutBranch(repo, branch, createBranch=False):
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
        # check if there is already a PR for this branch
        commands.clear()
        commands.append('cd "%s"' % repoPath)
        commands.append('BRANCH=`git rev-parse --abbrev-ref HEAD`')
        commands.append("../../gh pr checkout %s" % branch)
        commands.append("git checkout $BRANCH")
        commands = [
            c + " > /dev/null 2>&1" if quiteMode else c for c in commands]
        exitCode = call(" && ".join(commands), shell=True)
        # if exit code is zero that means there is a PR for this branch
        if exitCode == 0:
            print("A PR for this branch has already been created (maybe this script was already run for this release?)")
            print(
                "Please review the PR to determine if the correct changes are already present.")
            input("To view this PR in a browser, press Enter...")
            commands.clear()
            commands.append('cd "%s"' % repoPath)
            commands.append("../../gh pr view --web %s >> ../%s" %
                            (branch, outputPRsFile))
            commands = [c + "> /dev/null" if quiteMode else c
                        for c in commands]
            call(" && ".join(commands), shell=True)
            isPRCorrect = getUserReponse(
                "Does the PR contain the correct changes? (Y/n)")
            if isPRCorrect:
                print("Skipping re-processing this repo since correct PR already exists")
                raise Exception()

            # PR is incorrect, it should be closed
            print("Closing incorrect PR")
            commands.clear()
            commands.append('cd "%s"' % repoPath)
            commands.append("../../gh pr close %s" % branch)
            commands = [c + "> /dev/null" if quiteMode else c
                        for c in commands]
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
    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    commands.append('git add %s' % filesToAdd)
    commands.append('git commit -m "%s"' % commitMessage)
    commands = [c + " > /dev/null" if quiteMode else c for c in commands]
    call(" && ".join(commands), shell=True)


def pushAndCreatePR(repo, title, body, currentBranch, targetBranch, outputURLToFile=True):
    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    if outputURLToFile:
        commands.append("git push origin %s -f" % currentBranch)
        commands.append('../../gh pr create --title "%s" --body "%s" --base %s --label automated-release >> ../%s' %
                        (title, body, targetBranch, outputPRsFile))
    else:
        commands.append("git push origin %s -f > /dev/null 2>&1" %
                        currentBranch)
        commands.append('../../gh pr create --title "%s" --body "%s" --base %s --label automated-release' %
                        (title, body, targetBranch))
    prLink = subprocess.check_output(
        " && ".join(commands), shell=True).decode('utf-8')
    if not outputURLToFile:
        return prLink  # Return PR URL


def deleteLocalRepo(repo):
    print("Reverting repo %s" % repo)
    repoPath = getRepoPath(repo)
    shutil.rmtree(repoPath)
    print("Revert of %s is complete, please resolve this issue and try again. You may edit the repos.txt file to only target this affected repo in future runs." % repo)


def removeSnapshot(repo, version):
    cloneRepo(repo)
    releaseBranch = ""
    if repo in releaseBranchMap:
        releaseBranch = releaseBranchMap[repo]
    else:
        versionParts = version.split(".")
        releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])
    print("Removing SNAPSHOT for repo '%s' in branch '%s'" %
          (repo, releaseBranch))
    checkoutBranch(repo, releaseBranch)
    changeBranch = "release-remove-snapshot-%s" % version.replace('.', '')
    checkoutBranch(repo, changeBranch, createBranch=True)
    pomFilePaths = glob.glob(os.getcwd() + "/%s/**/pom.xml" %
                             repoNameToPath(repo), recursive=True)
    totalChanges = 0
    for pomFile in pomFilePaths:
        pom = ""
        with open(pomFile) as pf:
            pom = pf.read()
        prettyPomFile = pomFile.replace(os.getcwd(), '')
        beforeLength = len(pom)
        pom = re.sub(pomVersionSnapshotRegex,
                     pomVersionSub, pom, 1, re.MULTILINE)
        afterLength = len(pom)
        totalChanges += afterLength-beforeLength
        if beforeLength == afterLength:
            currentVersion = re.search(
                pomVersionRegex, pom, re.MULTILINE).groups()[0]
            skipPom = getUserReponse(
                "POM file ('%s') is already set to a non-SNAPSHOT version (%s), is this expected? (Y/n)" % (prettyPomFile, currentVersion))
            if skipPom:
                continue

            print("This script expects all repos to be in the SNAPSHOT stage. All changes to this repo (%s) will be reverted and it will be skipped." % repo)
            deleteLocalRepo(repo)
            return

        cdapVersionMatch = re.search(pomCDAPVersionRegex, pom, re.MULTILINE)
        if cdapVersionMatch:
            cdapVersion = cdapVersionMatch.groups()[0]
            if cdapVersion.endswith("-SNAPSHOT"):
                print("POM file ('%s') depends on a SNAPSHOT version of CDAP(%s). This is not allowed." % (
                    prettyPomFile, cdapVersion))
                autoUpdate = getUserReponse(
                    "Would you like to remove the SNAPSHOT from the CDAP version dependancy? If you are unsure please consult the team, you can skip this repo for now by responding with 'N'. (Y/n)")
                if not autoUpdate:
                    deleteLocalRepo(repo)
                    return
                newCdapVersion = '<version>%s</version>' % cdapVersion.replace(
                    "-SNAPSHOT", '')
                pom = re.sub(pomCDAPVersionRegex, newCdapVersion,
                             pom, 1, re.MULTILINE)

        with open(pomFile, 'w') as pf:
            pf.write(pom)
    # If no changes were made to this repo then delete the branch
    if totalChanges == 0:
        print("No changes were made to repo '%s'...deleting local branch and continuing. No PR will be generated for this repo." % repo)
        checkoutBranch(releaseBranch)
        deleteBranch(changeBranch)
        return

    addAndCommit(repo, "*pom.xml", "Removed SNAPSHOT from pom files.")
    pushAndCreatePR(repo, "[RELEASE-%s] Remove SNAPSHOTs" % version,
                    "This is an automated PR to remove -SNAPSHOT from artifact versions to prepare for release.", changeBranch, releaseBranch)


def bumpVersionToSnapshot(repo, version):
    cloneRepo(repo)
    releaseBranch = ""
    if repo in releaseBranchMap:
        releaseBranch = releaseBranchMap[repo]
    else:
        versionParts = version.split(".")
        releaseBranch = "release/%s.%s" % (versionParts[0], versionParts[1])
    checkoutBranch(repo, releaseBranch)
    changeBranch = "release-bump-versions-%s" % version.replace('.', '')
    checkoutBranch(repo, changeBranch, createBranch=True)
    pomFilePaths = glob.glob(os.getcwd() + "/%s/**/pom.xml" %
                             repoNameToPath(repo), recursive=True)
    for pomFile in pomFilePaths:
        pom = ""
        with open(pomFile) as pf:
            pom = pf.read()
        currentVersion = re.search(
            pomVersionRegex, pom, re.MULTILINE).groups()[0]
        if '-SNAPSHOT' in currentVersion:
            prettyPomFile = pomFile.replace(os.getcwd(), '')
            skipPom = getUserReponse(
                "POM file ('%s') is already set to a SNAPSHOT version (%s), is this expected? (Y/n)" % (prettyPomFile, currentVersion))
            if skipPom:
                continue

            print("This script expects all repos to be in a non-SNAPSHOT stage. All changes to this repo (%s) will be reverted and it will be skipped." % repo)
            deleteLocalRepo(repo)
            return

        currentVersionParts = currentVersion.split(".")
        currentVersionParts[-1] = str(int(currentVersionParts[-1])+1)
        newVersion = '<version>%s-SNAPSHOT</version>' % '.'.join(
            currentVersionParts)

        pom = re.sub(pomVersionRegex, newVersion, pom, 1, re.MULTILINE)

        with open(pomFile, 'w') as pf:
            pf.write(pom)
    addAndCommit(repo, "*pom.xml", "Bumped versions to next SNAPSHOT.")
    pushAndCreatePR(repo, "[RELEASE-%s] Bump to SNAPSHOT" % version,
                    "This is an automated PR to bump artifact versions to SNAPSHOT after a release is completed.", changeBranch, releaseBranch)


def updateSubmodules(version):
    submoduleRepos = ['cdapio/hydrator-plugins', 'cdapio/cdap-build']
    for repo in submoduleRepos:
        releaseBranch = ""
        if repo in releaseBranchMap:
            releaseBranch = releaseBranchMap[repo]
        else:
            versionParts = version.split(".")
            releaseBranch = "release/%s.%s" % (
                versionParts[0], versionParts[1])
        print("Setting up for submodule update in repo '%s'" % repo)
        cloneRepo(repo)
        checkoutBranch(repo, releaseBranch)
        changeBranch = 'release-update-submodules-%s' % version.replace(
            '.', '')
        try:
            checkoutBranch(repo, changeBranch, createBranch=True)
        except RuntimeError as e:
            continue
        except Exception as e:
            input("Press Enter once the PR is reviewed and merged...")
            continue

        if updateModulesAndCheck(repo):
            addAndCommit(repo, '-A', "Updated submodules for release")
            url = pushAndCreatePR(repo, "[RELEASE-%s] Update submodules" % version,
                                  "This is an automated PR to update submodules in preperation for release.", changeBranch, releaseBranch, outputURLToFile=False)
            print("PR for updating submodules in %s: %s" % (repo, url))
            input("Press Enter once the PR is reviewed and merged...")
        else:
            deleteLocalRepo(changeBranch)


def updateModulesAndCheck(repo):
    print("Attempting to update submodules in repo '%s'" % repo)
    repoPath = getRepoPath(repo)
    commands = []
    commands.append('cd "%s"' % repoPath)
    commands.append('git submodule update')
    commands.append('git submodule update --init --recursive --remote')
    call(" && ".join(commands), shell=True)

    commands.clear()
    commands.append('cd "%s"' % repoPath)
    commands.append("git status")
    statusText = subprocess.check_output(
        " && ".join(commands), shell=True).decode('utf-8')

    # If there were no submodules updated
    if 'nothing to commit, working tree clean' in statusText:
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
    print("Scanning repo '%s' for submodules" % repo)
    global releaseBranchMap
    versionParts = version.split(".")
    branch = "release/%s.%s" % (versionParts[0], versionParts[1])
    if repo in releaseBranchMap:
        branch = releaseBranchMap[repo]

    cloneRepo(repo)
    checkoutBranch(repo, branch)
    gitModulesPath = path.join(getRepoPath(repo), ".gitmodules")
    if not path.exists(gitModulesPath):
        return

    modulesFile = open(gitModulesPath)
    moduleContents = modulesFile.read()
    modulesFile.close()

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


ver = "6.1.4"
if path.exists(outputPRsFile):
    os.remove(outputPRsFile)
mapBranchVersions(ver)

# for repo in repos:
#     try:
#         bumpVersionToSnapshot(repo, ver)
#     except Exception as e:
#         print(e)
updateSubmodules(ver)
print("PRs for approval:")
call("cat %s" % outputPRsFile, shell=True)
