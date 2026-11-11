const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

let win = null;
let dfrotz = null;
let outputBuffer = [];
let resolveCallback = null;
let responseTimer = null;

// Paths to bundled assets
const DFROTZ_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'dfrotz.exe')
  : path.join(__dirname, 'dfrotz.exe');
const STORY_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'PLANETFA.DAT')
  : path.join(__dirname, 'PLANETFA.DAT');

function startDfrotz() {
  if (dfrotz) {
    try { dfrotz.kill(); } catch (e) {}
  }
  dfrotz = spawn(DFROTZ_PATH, [STORY_PATH], {
    cwd: path.dirname(DFROTZ_PATH),
    windowsHide: true
  });

  outputBuffer = [];
  resolveCallback = null;
  responseTimer = null;

  const rl = readline.createInterface({
    input: dfrotz.stdout,
    crlfDelay: Infinity
  });

  rl.on('line', (line) => {
    outputBuffer.push(line);

    // dfrotz in pipe mode doesn't output a standalone ">" prompt.
    // The output for each command is:
    //   1. Status line: "> RoomName ... Score: N ... Moves: N" (or " RoomName..." for first)
    //   2. Blank line
    //   3. Room name + description text
    //   4. (silence — waits for next command)
    //
    // We detect end-of-response by watching for a quiet period:
    // after we see the status line, if no new lines arrive for 400ms,
    // we consider the response complete.
    if (resolveCallback) {
      // Clear any existing timer and set a new one
      if (responseTimer) clearTimeout(responseTimer);
      responseTimer = setTimeout(() => {
        if (resolveCallback) {
          const cb = resolveCallback;
          resolveCallback = null;
          responseTimer = null;
          const output = outputBuffer.join('\n');
          outputBuffer = [];
          cb(output);
        }
      }, 400);
    }
  });

  dfrotz.stderr.on('data', (data) => {});

  dfrotz.on('error', (err) => {
    console.error('dfrotz error:', err);
  });
}

function parseOutput(raw, command) {
  const lines = raw.split('\n').map(l => l.replace(/\r/g, ''));
  let roomName = '', score = 0, moves = 0;
  let statusIdx = -1;

  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('Score:') && lines[i].includes('Moves:')) {
      statusIdx = i;
      const scoreMatch = lines[i].match(/Score:\s*(\d+)/);
      const movesMatch = lines[i].match(/Moves:\s*(\d+)/);
      if (scoreMatch) score = parseInt(scoreMatch[1]);
      if (movesMatch) moves = parseInt(movesMatch[1]);
      const clean = lines[i].replace(/^>\s*/, '').replace(/\s*Score:.*$/, '').trim();
      if (clean) roomName = clean;
      break;
    }
  }

  // Extract body text after the first status line, up to the next prompt.
  // Preserve empty lines (paragraph breaks) within the body.
  let bodyLines = [];
  let inBody = false;
  for (let i = statusIdx + 1; i < lines.length; i++) {
    const stripped = lines[i].trim();
    if (stripped === '>' || stripped === '> ') break;
    if (!inBody && stripped === '') continue; // skip leading blanks
    inBody = true;
    bodyLines.push(lines[i]);
  }
  // Trim trailing empty lines
  while (bodyLines.length > 0 && bodyLines[bodyLines.length - 1].trim() === '') {
    bodyLines.pop();
  }
  const body = bodyLines.join('\n').trim();

  return {
    command: command,
    room: roomName,
    score: score,
    moves: moves,
    text: body
  };
}

function sendCommand(cmd) {
  return new Promise((resolve) => {
    if (!dfrotz || dfrotz.exitCode !== null) {
      startDfrotz();
    }
    // Clear any previous output and set up for this command.
    // The timeout-based reader will collect lines and fire after 400ms of silence.
    outputBuffer = [];
    if (responseTimer) { clearTimeout(responseTimer); responseTimer = null; }
    resolveCallback = resolve;
    try {
      dfrotz.stdin.write(cmd + '\n');
    } catch (e) {
      startDfrotz();
      dfrotz.stdin.write(cmd + '\n');
    }
    // Overall timeout (15s) in case dfrotz hangs
    setTimeout(() => {
      if (resolveCallback) {
        resolveCallback = null;
        if (responseTimer) { clearTimeout(responseTimer); responseTimer = null; }
        resolve({ command: cmd, room: '', score: 0, moves: 0, text: '(timeout waiting for dfrotz response)' });
      }
    }, 15000);
  }).then(raw => {
    if (typeof raw === 'string') return parseOutput(raw, cmd);
    return raw;
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 500,
    backgroundColor: '#06060a',
    title: 'Planetfall — Visual Mapper',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.loadFile('index.html');
  win.setMenuBarVisibility(false);
}

app.whenReady().then(() => {
  startDfrotz();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (dfrotz) try { dfrotz.kill(); } catch (e) {}
  if (process.platform !== 'darwin') app.quit();
});

// IPC handlers
ipcMain.handle('send-command', async (event, cmd) => {
  return await sendCommand(cmd);
});

ipcMain.handle('get-map', async () => {
  const dataPath = app.isPackaged
    ? path.join(process.resourcesPath, 'planetfall_data.json')
    : path.join(__dirname, 'planetfall_data.json');
  const data = fs.readFileSync(dataPath, 'utf-8');
  return JSON.parse(data);
});

ipcMain.handle('restart-game', async () => {
  startDfrotz();
  return { status: 'restarted' };
});
