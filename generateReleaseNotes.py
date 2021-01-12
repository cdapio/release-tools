import subprocess
import jira
import os
import sys
import argparse
from urllib.parse import quote

jiraURL = 'https://cdap.atlassian.net/'

class ReleaseNote():
    """ Class to hold data for a release note """

    def __init__(self, id, releaseNote, issueType):
        self.id = id
        self.releaseNote = releaseNote if releaseNote.endswith('.') else releaseNote+'.'  # Clean up string
        self.issueType = issueType
        self.link = '%sbrowse/%s' % (jiraURL, id)

    def toString(self):
        return '- `%s <%s>`_ - %s' % (self.id, self.link, self.releaseNote)


def createHeader(text):
    """ Create an RST style section header    """

    return ['', text, '-'*len(text)]


def parseArgs():
    """ Parse command line arguments """

    parser = argparse.ArgumentParser(
        description='Script for automatically pulling tickets for current release from CDAP JIRA and compile them into a release notes file.')

    parser.add_argument('version',
                        type=str,
                        help='Version string to generate release notes for. Ex. 6.1.4')
    
    parser.add_argument('username',
                        type=str,
                        help='The username to use for authenticating with JIRA to fetch the release notes. You will be promoted for an API token once the script is running.',
                        )

    parser.add_argument('-o', '--output',
                        type=str,
                        help='File to output release notes to, defaults to releaseNotes.rst in the current directory')



    args = parser.parse_args()
    if args.username is None and not args.overrideUser :
        sys.stderr.write("ERROR: Username is a required paramater, please specify a JIRA username to use for fetching tickets.\n")
        sys.exit(1)
    return args

def main():
    """ Main function that does all the work """

    # Parse command args and setup constants
    args = parseArgs()
    version = args.version
    issueFilter = 'project in (CDAP, "CDAP Plugins") AND fixVersion = %s AND "Release Notes" is not EMPTY' % version
    issueFilterNoReleaseNotes = 'project in (CDAP, "CDAP Plugins") AND fixVersion = %s AND "Release Notes" is EMPTY' % version
    issueFields = 'status,resolution,issuetype,Release Notes'

    # Getting the password depending if we are overriding the user
    jiraAgentUsername = args.username
    jiraAgentPassword = input("Enter API token created by JIRA user '%s': "%jiraAgentUsername)

    # Try to init agent
    try:
        agent = jira.JIRA(jiraURL, basic_auth=(jiraAgentUsername, jiraAgentPassword))
    except Exception as e:
        errorMessage = e
        try:
            errorMessage = e.response.content.decode("utf-8") #Get the response out of the JiraError
        except Exception as r:
            pass
        sys.stderr.write("ERROR: Failed to login to JIRA using account '%s': %s\n"%(jiraAgentUsername,errorMessage))
        return 1

    print("DEBUG: JIRA Agent created successfully!")
    print("DEBUG: Searching for JIRA tickets with 'Fix Version = %s'" % version)
    searchResults = agent.search_issues(issueFilter, maxResults=1000, fields=issueFields, json_result=True)
    noReleaseNotesResults = agent.search_issues(issueFilterNoReleaseNotes, maxResults=1000, fields=issueFields, json_result=True)

    print("DEBUG: Found %d issues with release notes for version %s" % (searchResults['total'], version))

    # Release notes grouped by type
    releaseNotes = {'New Feature': [], 'Improvement': [], 'Bug': [], 'Task': [], 'Sub-task': []}
    for issue in searchResults['issues']:

        issueFields = issue['fields']
        note = issueFields['customfield_10300'].strip()
        id = issue['key']

        # Print warnings if the tickets arent marked as Fixed and Closed which they should be at this stage of the release
        if issueFields['resolution'] is None or issueFields['resolution']['name'] != 'Fixed':
            print('WARN: Issue %s is not marked as Fixed!' % id)
        if issueFields['status'] is None or issueFields['status']['name'] != 'Closed':
            print('WARN: Issue %s is not marked as Closed!' % id)

        issueType = issueFields['issuetype']['name']
        if issueType not in releaseNotes:
            releaseNotes[issueType] = []

        # Add ReleaseNote object to dict under correct issueType
        releaseNotes[issueType].append(ReleaseNote(id, note, issueType))

    releaseNotesOrder = ['New Feature', 'Improvement', 'Bug']  # Order that the sections will appear in the doc
    releaseNotesPrettyName = {'New Feature': 'New Features', 'Improvement': 'Improvements', 'Bug': 'Bug Fixes'}  # Better names for each issueType
    contentLines = []
    for issueType in releaseNotesOrder:
        contentLines = contentLines + createHeader(releaseNotesPrettyName[issueType])

        # Sort the issues by their ID so they appear in sorted order in the final doc
        sortedNotes = sorted(releaseNotes[issueType], key=lambda releaseNote: releaseNote.id)
        if len(sortedNotes) == 0:
            contentLines.append("No changes.")
            continue
        for note in sortedNotes:
            contentLines.append(note.toString())

    # Save all results to file
    contentLines = [line+'\n' for line in contentLines]
    filename = 'releaseNotes.rst'
    if args.output:
        filename = args.output
        filename += '.rst' if not filename.endswith('.rst') else ""
    outputFile = open(filename, 'w')
    outputFile.writelines(contentLines)
    outputFile.close()

    
    if noReleaseNotesResults['total'] > 0:
        issueId = noReleaseNotesResults['issues'][0]['key']
        url = '%sbrowse/%s?jql=%s'%(jiraURL, issueId, quote(issueFilterNoReleaseNotes))
        print("\nWARN: Found %d tickets with Fix Version %s but no release notes!"%(noReleaseNotesResults['total'], version))
        print("WARN: Go to this URL to see the issues without release notes: %s"%url)

    
    print("DEBUG: Done! Generated release notes in file '%s'" % filename)
    return 0


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
