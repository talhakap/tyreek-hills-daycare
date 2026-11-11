/**
 * Rebuilds the league website a minute or two after the pick trades Google Sheet changes.
 *
 * This is a Google Apps Script. It does NOT run on the website or on GitHub; it lives in
 * the Google account that owns the sheet. Setup (once): README section 7, "Updating the site
 * as soon as the sheet changes".
 *
 * How it works: every edit to the sheet records the time. Once a minute, if the sheet was
 * edited and nobody has touched it for QUIET_SECONDS, it asks GitHub to run the
 * "Build and deploy site" workflow. Typing a whole row therefore starts one rebuild, not ten.
 */

const SHEET_ID = '1rO1M3gcxyONofya-22ujY3cV-U7MOZwbMLYcsHT9NWw';
const REPO = 'talhakap/tyreek-hills-daycare';
const WORKFLOW = 'build.yml';
const BRANCH = 'main';
const QUIET_SECONDS = 60;

/** Run this once (select "setup" above, then Run). Safe to run again. */
function setup() {
  if (!getToken_()) {
    throw new Error('First add the GitHub key: Project Settings (gear icon) > Script properties > ' +
                    'Add script property, name GITHUB_TOKEN, value = the key.');
  }
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('recordEdit').forSpreadsheet(SHEET_ID).onEdit().create();
  ScriptApp.newTrigger('recordEdit').forSpreadsheet(SHEET_ID).onChange().create();  // inserted/deleted rows, pastes
  ScriptApp.newTrigger('rebuildIfChanged').timeBased().everyMinutes(1).create();
  startBuild_();
  console.log('All set. A test rebuild was started: check the Actions tab on GitHub.');
}

/** Called by Google whenever someone edits the sheet. */
function recordEdit() {
  PropertiesService.getScriptProperties().setProperty('LAST_EDIT', String(Date.now()));
}

/** Called by Google once a minute. Starts a rebuild once the sheet has been quiet for a bit. */
function rebuildIfChanged() {
  const props = PropertiesService.getScriptProperties();
  const lastEdit = Number(props.getProperty('LAST_EDIT') || 0);
  const lastBuild = Number(props.getProperty('LAST_BUILD') || 0);
  if (!lastEdit || lastEdit <= lastBuild) return;                 // nothing new
  if (Date.now() - lastEdit < QUIET_SECONDS * 1000) return;       // still being edited
  startBuild_();
  props.setProperty('LAST_BUILD', String(Date.now()));            // only after GitHub accepted it
}

function startBuild_() {
  const url = 'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/dispatches';
  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + getToken_(),
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: BRANCH }),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  if (code !== 204) {
    const hint = code === 401 ? ' The GitHub key is wrong or has expired: make a new one and update GITHUB_TOKEN.'
      : code === 403 || code === 404 ? ' The key needs "Actions: Read and write" on ' + REPO + '.' : '';
    throw new Error('GitHub refused to start the rebuild (HTTP ' + code + ').' + hint);
  }
}

function getToken_() {
  return (PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN') || '').trim();
}
