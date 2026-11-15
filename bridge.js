/* bridge.js - connects dxarrangernew1.html to the Python engine (arranger_app.py).
 * Loaded automatically by the app; your HTML file itself is never changed.
 *
 * Button ids (data-id) it controls:
 *   G-1..G-16        P1..P16 folder shortcuts: tap = list that folder's styles on the LCD,
 *                    right-click or long-press = choose which folder the button opens
 *   NAV-UP/DOWN/LEFT/RIGHT move in the file list, NAV-ENTER loads the highlighted style
 *   I-A..I-D         Intro   (armed while stopped, plays from the next bar while running)
 *   V-A..V-D         Main A-D (with its fill-in, if AFH is on)
 *   BR-1             Break (Fill In BA)
 *   EN-1..EN-3       Ending A-C
 *   SY-1 / SY-2 / STOP   start / stop / stop
 *   AFH              Auto Fill-in on/off (my assumption for "A.FH" - tell me if it means something else)
 *   PERC-1..6, PERC2-1..6   percussion pads (notes set in config.json)
 *   MASTER + NAV2 -/+       master volume
 *   #bpm tempo box   tempo
 * VOL-1..6 = part volumes (Rhythm sub, Rhythm, Bass, Chord 1, Chord 2, Pad, Phrase: see vol_groups in config.json).
 * P-1..P-4 = Multi Pads (MENU > STYLES > MULTIPAD loads files into them).
 * MENU > PLUGINS & VST: host VST3 instruments and route parts to them (full page: /plugins).
 * Not connected yet: C.M.
 * While the LCD is on the CREATE page, the panel's own beat maker works as before.
 */
(function () {
  'use strict';

  const SECTION = {
    'I-A': ['intro', 'A'], 'I-B': ['intro', 'B'], 'I-C': ['intro', 'C'], 'I-D': ['intro', 'D'],
    'V-A': ['main', 'A'], 'V-B': ['main', 'B'], 'V-C': ['main', 'C'], 'V-D': ['main', 'D'],
    'BR-1': ['break'], 'EN-1': ['ending', 'A'], 'EN-2': ['ending', 'B'], 'EN-3': ['ending', 'C'],
    'SY-1': ['start'], 'SY-2': ['stop'], 'STOP': ['stop'], 'AFH': ['autofill']
  };
  const NEEDS_SECTION = {   // which style section a button needs (to grey it out when missing)
    'I-A': 'Intro A', 'I-B': 'Intro B', 'I-C': 'Intro C', 'I-D': 'Intro D',
    'V-A': 'Main A', 'V-B': 'Main B', 'V-C': 'Main C', 'V-D': 'Main D',
    'BR-1': 'Fill In BA', 'EN-1': 'Ending A', 'EN-2': 'Ending B', 'EN-3': 'Ending C'
  };
  const LAMPS = Object.keys(NEEDS_SECTION).concat(['SY-1', 'AFH']);
  const SECTION_LAMP = NEEDS_SECTION;          // intro / main / break / ending buttons

  const lampCss = document.createElement('style');
  lampCss.textContent = `
    .btn.avail .bulb, .btn.avail.active .bulb {
      background: linear-gradient(180deg, #e9f7ff 0%, #9bd8ff 35%, #4fb0ff 65%, #1f78d8 100%);
      box-shadow: inset 0 0 1px rgba(255,255,255,0.9), 0 0 4px 1px rgba(90,185,255,0.95), 0 0 14px 4px rgba(60,160,255,0.5);
    }
    .btn.playing .bulb, .btn.playing.active .bulb {
      background: linear-gradient(180deg, #fff1d6 0%, #ffc066 30%, #ff9a1f 60%, #e06f00 100%);
      box-shadow: inset 0 0 1px rgba(255,255,255,0.9), 0 0 5px 2px rgba(255,150,30,1), 0 0 16px 5px rgba(255,130,0,0.55);
    }
    @keyframes beat-pulse-orange {
      0%       { box-shadow: inset 0 0 1px rgba(255,255,255,0.9), 0 0 6px 2px rgba(255,160,40,1), 0 0 22px 8px rgba(255,130,0,0.7); }
      20%,100% { box-shadow: inset 0 0 1px rgba(255,255,255,0.6), 0 0 3px 1px rgba(255,150,30,0.75), 0 0 10px 3px rgba(255,130,0,0.35); }
    }`;
  document.head.appendChild(lampCss);

  let st = { loaded: false };
  let bridgeSlot = null;      // which P button the file list belongs to
  let slotFiles = [];         // real file names in that folder
  let titleText = '';
  let lastRev = -1;
  let fromServer = false;
  let longPressed = false;
  let pressTimer = null;
  let toastText = '', toastUntil = 0;
  let lastPadError = '';
  let padMode = false;        // the LCD file list is showing the MULTIPAD folder
  let kitMode = false;        // the LCD file list is showing the DRUMKITS

  // Pages that used to open a browser tab open a second window when the panel runs in its own window.
  function openPage(p) {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_page) window.pywebview.api.open_page(p);
    else window.open(p, '_blank');
  }

  async function api(path, body) {
    try {
      const opt = body === undefined ? undefined :
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      const r = await fetch(path, opt);
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  const inCreate = () => typeof lcdCreate !== 'undefined' && lcdCreate.classList.contains('visible');
  const $id = id => document.querySelector('[data-id="' + id + '"]');

  // ---- small status line (bottom-left) ------------------------------------
  const badge = document.createElement('div');
  badge.style.cssText = 'position:fixed;left:8px;bottom:6px;z-index:99999;font:12px/1.3 Consolas,monospace;' +
    'color:#9fe;background:rgba(0,0,0,.65);padding:3px 8px;border-radius:4px;max-width:70vw;pointer-events:none';
  document.body.appendChild(badge);

  function toast(text, ms) { toastText = text; toastUntil = Date.now() + (ms || 3500); renderBadge(); }

  function renderBadge() {
    let t;
    if (Date.now() < toastUntil) t = toastText;
    else if (!st || st.synth === undefined) t = 'ENGINE: not connected (is arranger_app.py running?)';
    else if (st.error) t = 'ERROR: ' + st.error;
    else if (st.loaded) t = st.name.toUpperCase() + '  |  ' + (st.running ? (st.section || '').toUpperCase() : 'STOPPED') +
      '  |  ' + Math.round(st.bpm) + ' BPM  |  ' + st.synth.toUpperCase();
    else t = st.message || 'ENGINE READY';
    badge.textContent = t;
    badge.style.color = (st && st.error) ? '#f99' : '#9fe';
  }

  // ---- LCD file list ------------------------------------------------------
  function applyTitle() {
    if ((!bridgeSlot && !padMode && !kitMode) || typeof lcdFiles === 'undefined') return;
    const s = lcdFiles.querySelector('.lcd-files-status');
    if (s) s.textContent = titleText;
  }

  const origRender = window.renderFileBrowser;
  if (origRender) {
    window.renderFileBrowser = function () {
      origRender.apply(this, arguments);
      if (kitMode && fileType === 'user') { titleText = kitStatus(); markActiveKit(); }
      if ((bridgeSlot || padMode || kitMode) && fileType === 'user') applyTitle();
    };
  }
  const origRoot = window.showRootMenu;
  if (origRoot) {
    window.showRootMenu = function () { bridgeSlot = null; padMode = false; kitMode = false; return origRoot.apply(this, arguments); };
  }
  const folderInput = document.getElementById('userFolderInput');
  if (folderInput) folderInput.addEventListener('change', () => { bridgeSlot = null; padMode = false; kitMode = false; });

  async function openSlot(slot) {
    const r = await api('/api/folder?slot=' + slot);
    if (!r || r.error) { toast('Could not read folder P' + slot); return; }
    bridgeSlot = slot;
    padMode = false; kitMode = false;
    slotFiles = r.files;
    titleText = 'P' + slot + '  ' + r.folder_name.toUpperCase();
    userFiles = r.labels.slice();
    openFileBrowser('user', 0);
    if (!r.files.length) {
      lcdFiles.innerHTML = '<div class="lcd-files-status">' + titleText + ' - EMPTY</div>';
      toast('Put .STY files in: ' + r.path, 7000);
    } else {
      applyTitle();
    }
  }

  async function chooseFolder(slot) {
    toast('Choose the folder for P' + slot + ' in the window that opened...', 8000);
    const r = await api('/api/setfolder', { slot: slot });
    if (r && r.ok) { toast('P' + slot + ' now opens: ' + r.path, 6000); openSlot(slot); }
    else toast('Folder not changed.');
  }

  // ---- clicks -------------------------------------------------------------
  // Capture phase: when a style is loaded, the engine buttons are handled here and the panel's
  // built-in beat maker does not also run.
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-id]');
    if (!el) return;
    const id = el.dataset.id;
    if (/^G-\d+$/.test(id) && longPressed) {
      longPressed = false; ev.stopImmediatePropagation(); ev.preventDefault(); return;
    }
    if (inCreate()) return;
    const cmd = SECTION[id];
    if (!cmd) return;
    if (id === 'AFH') {                        // works with or without a style
      ev.stopImmediatePropagation(); ev.preventDefault();
      api('/api/cmd', { op: 'autofill' }); return;
    }
    if (!st.loaded) return;                    // no style yet: the panel's own behaviour
    ev.stopImmediatePropagation(); ev.preventDefault();
    if (NEEDS_SECTION[id] && st.available && st.available.indexOf(NEEDS_SECTION[id]) < 0) {
      toast('This style has no ' + NEEDS_SECTION[id]); return;
    }
    api('/api/cmd', { op: cmd[0], arg: cmd[1] });
  }, true);

  // P1..P16: open that folder (runs after the panel's own handler, so ours wins)
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-id^="G-"]');
    if (!el || inCreate()) return;
    const slot = Number(el.dataset.id.slice(2));
    if (slot >= 1 && slot <= 16) openSlot(slot);
  });

  // ENTER loads the highlighted style: from a P folder, or an INBUILT slot you imported earlier
  const imported = {};                         // INBUILT slot number -> file name stored on the PC
  document.addEventListener('click', async ev => {
    if (!ev.target.closest('[data-id="NAV-ENTER"]')) return;
    if (typeof lcdFiles === 'undefined' || !lcdFiles.classList.contains('visible')) return;
    if (bridgeSlot && fileType === 'user') {
      const name = slotFiles[filePage * filesPerPage + fileRow * 2 + fileColumn];
      if (!name) return;
      toast('Loading ' + name + ' ...', 4000);
      const r = await api('/api/load', { slot: bridgeSlot, name: name });
      if (!r || r.error) toast('Could not load ' + name);
    } else if (fileType === 'inbuilt') {
      const file = imported[filePage * filesPerPage + fileRow * 2 + fileColumn + 1];
      if (!file) return;                       // empty slot: the panel asks to import
      toast('Loading ' + file + ' ...', 4000);
      const r = await api('/api/load', { slot: 0, name: file });
      if (!r || r.error) toast('Could not load ' + file);
    }
  });

  // A style chosen in the panel's import box (or from a phone) is sent to the PC and loaded
  const importInput = document.getElementById('styleImportInput');
  if (importInput) {
    importInput.addEventListener('change', async () => {
      const file = importInput.files[0];
      const slot = Number(importInput.dataset.slot);
      if (!file) return;
      toast('Sending ' + file.name + ' to the engine ...', 5000);
      let r = null;
      try {
        r = await (await fetch('/api/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file })).json();
      } catch (e) { /* handled below */ }
      if (r && r.ok) { imported[slot] = r.file; toast('Imported ' + r.file + '. Press the start button.', 6000); }
      else toast('Import failed: ' + ((r && r.error) || 'engine not reachable'), 6000);
    }, true);                                  // capture: read the file before the panel clears the input
  }

  // right-click / long-press on a P button = choose its folder
  document.addEventListener('contextmenu', ev => {
    const el = ev.target.closest('[data-id^="G-"]');
    if (!el) return;
    ev.preventDefault();
    chooseFolder(Number(el.dataset.id.slice(2)));
  });
  document.addEventListener('pointerdown', ev => {
    const g = ev.target.closest('[data-id^="G-"]');
    if (g && ev.pointerType !== 'mouse') {
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => { longPressed = true; chooseFolder(Number(g.dataset.id.slice(2))); }, 800);
    }
    const p = ev.target.closest('[data-id^="PERC"]');          // percussion pads: play on touch
    if (p && /^PERC2?-\d$/.test(p.dataset.id) && !inCreate()) {
      api('/api/cmd', { op: 'perc', arg: p.dataset.id });
      p.classList.add('active'); setTimeout(() => p.classList.remove('active'), 140);
    }
  }, true);
  ['pointerup', 'pointercancel'].forEach(n => document.addEventListener(n, () => clearTimeout(pressTimer), true));

  // ---- EFFECTS menu -----------------------------------------------------------
  // MENU > EFFECTS: each effect shows ON / OFF, NAV-ENTER (or a tap) switches it; the last entry switches all off.
  const FX_ITEMS = [['geq', 'GRAPHIC EQ'], ['eq3m', 'EQ3-M'], ['ezeq', 'EZ EQ'],
                    ['studio', 'STUDIO REVERB'], ['spring', 'SPRING REVERB'], ['drive', 'BL DRIVE']];
  if (typeof menuPages !== 'undefined' && !Object.getOwnPropertyDescriptor(menuPages, 'effects')) {
    Object.defineProperty(menuPages, 'effects', {
      configurable: true, enumerable: true,
      get() {
        const fx = (typeof st !== 'undefined' && st && st.fx) || {};
        return FX_ITEMS.map(([id, label]) => label +
          (fx[id] ? ' <span class="act">ON &#10003;</span>' : ' <span style="opacity:.55">OFF</span>'))
          .concat(['ALL EFFECTS OFF']);
      }
    });
  }
  let prevMenuPage = '';                                      // NAV-LEFT from EFFECTS returns with EFFECTS selected
  document.addEventListener('click', ev => { if (ev.target.closest('[data-id="NAV-LEFT"]')) prevMenuPage = menuPage; }, true);
  document.addEventListener('click', ev => {
    if (ev.target.closest('[data-id="NAV-LEFT"]') && prevMenuPage === 'effects' && menuPage === 'root') {
      menuIndex = 2; renderLcdMenu();
    }
  });
  async function toggleFx(i) {
    const item = FX_ITEMS[i];
    const r = await api('/api/cmd', { op: 'fx', arg: item ? item[0] : 'alloff' });
    if (!r || !r.fx) { toast('Engine not reachable', 3000); return; }
    st.fx = r.fx;
    renderLcdMenu();
    toast(item ? item[1] + (r.fx[item[0]] ? ' ON' : ' OFF') : 'ALL EFFECTS OFF', 2500);
  }

  // ---- MULTIPAD ---------------------------------------------------------------
  // MENU > STYLES > MULTIPAD lists the Multipad folder on the LCD. First entry = add files from this device.
  // P-1..P-4 pick the target pad while the list is open (otherwise they play the pads); ENTER loads the highlighted file.
  const PAD_ADD = '+ ADD FILES';
  let padFiles = [], padTarget = 1, padNames = ['', '', '', ''];
  const inPad = () => padMode && typeof lcdFiles !== 'undefined' && lcdFiles.classList.contains('visible');
  const padTitle = () => 'MULTIPAD  >PAD ' + padTarget + (padNames[padTarget - 1] ? ' (' + padNames[padTarget - 1] + ')' : ' (EMPTY)') +
    '  P1-P4 CHOOSE, ENTER LOADS';

  if (typeof menuPages !== 'undefined') {                 // add MULTIPAD as the 3rd entry of the STYLES page
    const d = Object.getOwnPropertyDescriptor(menuPages, 'styles');
    if (d && d.get) Object.defineProperty(menuPages, 'styles', {
      configurable: true, enumerable: true,
      get() { return d.get.call(menuPages).concat(['MULTIPAD']); }
    });
  }
  const origOpenMenu = window.openSelectedMenu;
  if (origOpenMenu) {
    window.openSelectedMenu = function () {
      if (menuPage === 'styles' && menuIndex === 2) { openPadBrowser(); return; }
      if (menuPage === 'root' && menuIndex === 2) { menuPage = 'effects'; menuIndex = 0; renderLcdMenu(); return; }
      if (menuPage === 'effects') { toggleFx(menuIndex); return; }
      return origOpenMenu.apply(this, arguments);
    };
  }

  async function openPadBrowser(keepTarget) {
    const r = await api('/api/padfolder');
    if (!r || r.error) { toast('Could not read the Multipad folder'); return; }
    bridgeSlot = null; padMode = true; kitMode = false;
    padFiles = r.files;
    padNames = r.pads.map(p => p.name);
    if (!keepTarget) padTarget = 1;
    titleText = padTitle();
    userFiles = [PAD_ADD].concat(r.labels);
    openFileBrowser('user', 0);
    applyTitle();
    if (!r.files.length) toast('Use + ADD FILES, or put .wav .mp3 .pad .mid files in: ' + r.path, 8000);
  }

  const padInput = document.createElement('input');
  padInput.type = 'file'; padInput.multiple = true; padInput.accept = '.wav,.mp3,.pad,.mid,.midi';
  padInput.style.display = 'none';
  document.body.appendChild(padInput);
  padInput.addEventListener('change', async () => {
    const files = Array.from(padInput.files);
    let ok = 0;
    for (const f of files) {
      toast('Sending ' + f.name + ' ...', 8000);
      try {
        const r = await (await fetch('/api/padupload?name=' + encodeURIComponent(f.name), { method: 'POST', body: f })).json();
        if (r && r.ok) ok++; else toast('Not added: ' + ((r && r.error) || f.name), 6000);
      } catch (e) { toast('Engine not reachable', 5000); }
    }
    padInput.value = '';
    if (ok) { toast(ok + ' file(s) added to the Multipad folder', 4000); openPadBrowser(true); }
  });

  document.addEventListener('click', async ev => {          // ENTER inside the MULTIPAD list
    if (!ev.target.closest('[data-id="NAV-ENTER"]') || !inPad()) return;
    const i = filePage * filesPerPage + fileRow * 2 + fileColumn;
    if (i === 0) { padInput.click(); return; }
    const name = padFiles[i - 1];
    if (!name) return;
    toast('Loading ' + name + ' onto pad ' + padTarget + ' ...', 4000);
    const r = await api('/api/padload', { name: name, slot: padTarget });
    if (!r || !r.ok) { toast('Could not load ' + name + ((r && r.error) ? ': ' + r.error : ''), 6000); return; }
    padNames = r.pads.map(p => p.name);
    toast('PAD ' + padTarget + (r.names.length > 1 ? '..' + (padTarget + r.names.length - 1) : '') + ' = ' + r.names.join(', '), 5000);
    if (r.names.length === 1) padTarget = padTarget % 4 + 1;    // next load goes to the next pad
    titleText = padTitle(); applyTitle();
  });

  document.addEventListener('click', ev => {                // P-1..P-4: choose the target while loading, else play
    const el = ev.target.closest('[data-id]');
    if (!el || !/^P-[1-4]$/.test(el.dataset.id)) return;
    const n = Number(el.dataset.id.slice(2));
    if (inPad()) { padTarget = n; titleText = padTitle(); applyTitle(); return; }
    if (inCreate()) return;                                  // CREATE page: the panel's own behaviour
    api('/api/cmd', { op: 'pad', arg: n });
  });

  // ---- PLUGINS & VST ----------------------------------------------------------------------------
  // MENU > PLUGINS & VST (6th entry): the rack of plugin slots. Slot > LOAD PLUGIN / OPEN WINDOW / ROUTE PARTS / LEVEL ...
  // The full-page manager (folders, per-channel routing, MIDI ports) opens from the last entry.
  const PL_PAGES = ['plugins', 'pslot', 'pload', 'proute'];
  const PL_PARTS = ['DRUMS', 'BASS', 'CHORD 1', 'CHORD 2', 'PAD', 'PHRASE', 'ALL CHANNELS'];
  let plSlot = 0, plCatalog = [], plSig = '';
  const esc = t => String(t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dim = t => ' <span style="opacity:.55">' + esc(t) + '</span>';
  const plSlots = () => (st.plugins && st.plugins.slots) || [];
  const plCur = () => plSlots()[plSlot] || { name: '', channels: [], parts: [], level: 100 };
  const plVisible = () => typeof lcdMenu !== 'undefined' && lcdMenu.classList.contains('visible') && PL_PAGES.indexOf(menuPage) >= 0;
  const goPl = (page, idx) => { menuPage = page; menuIndex = idx; renderLcdMenu(); };
  async function plApi(body) {
    const r = await api('/api/plugins', body);
    if (!r) toast('Engine not reachable', 3000);
    else if (r.error) toast(r.error, 7000);
    poll();
    return r;
  }

  if (typeof menuPages !== 'undefined') {
    const dRoot = Object.getOwnPropertyDescriptor(menuPages, 'root');
    if (dRoot && dRoot.get) Object.defineProperty(menuPages, 'root', {
      configurable: true, enumerable: true,
      get() { return dRoot.get.call(menuPages).concat(['PLUGINS &amp; VST']); }
    });
    const pages = {
      plugins() {
        const rows = plSlots().map(s => (s.i + 1) + '  ' + (s.loading ? 'LOADING...' : s.name ? esc(s.name) : '(EMPTY)') +
          (s.error ? ' <span style="color:#f99">!</span>' : '') +
          (s.name && s.channels.length ? dim(s.parts.length ? s.parts.join('+') : 'CH ' + s.channels.map(c => c + 1).join(',')) : ''));
        const sc = st.plugins && st.plugins.scanning;
        return rows.concat([
          sc ? 'SCANNING... ' + st.plugins.scan_done + '/' + st.plugins.scan_total : 'SCAN FOR PLUGINS',
          'PLAY ALL PARTS ' + (st.drums_only === false ? '<span class="act">ON &#10003;</span>' : dim('OFF (DRUMS ONLY)')),
          'PLUGIN MANAGER (FULL PAGE)']);
      },
      pslot() {
        const c = plCur();
        return ['SLOT ' + (plSlot + 1) + ': LOAD PLUGIN...', 'OPEN PLUGIN WINDOW', 'ROUTE PARTS...' + dim(c.parts.join('+')),
          'LEVEL -10', 'LEVEL +10' + dim('now ' + c.level + '%'), 'MUTE ' + (c.mute ? '<span class="act">ON</span>' : dim('OFF')),
          'SAVE SOUND', 'CLEAR SLOT'];
      },
      pload() {
        return plCatalog.length ? plCatalog.map(e => esc(e.name)) : ['(NO PLUGINS FOUND - SCAN FIRST)'];
      },
      proute() {
        const c = plCur();
        return PL_PARTS.map(p => p + ((c.parts.indexOf(p === 'ALL CHANNELS' ? 'ALL' : p) >= 0) ? ' <span class="act">&#10003;</span>' : ''));
      }
    };
    PL_PAGES.forEach(n => { if (!Object.getOwnPropertyDescriptor(menuPages, n)) Object.defineProperty(menuPages, n, { configurable: true, enumerable: true, get: pages[n] }); });
  }

  async function plEnter() {
    const n = plSlots().length;
    if (menuPage === 'plugins') {
      if (menuIndex < n) { plSlot = menuIndex; const e = plCur().error; goPl('pslot', 0); if (e) toast(e, 7000); }
      else if (menuIndex === n) { toast('Scanning for plugins...', 3000); plApi({ op: 'scan' }); }
      else if (menuIndex === n + 1) {
        const r = await plApi({ op: 'drums_only', value: st.drums_only === false });
        if (r && r.ok) { st.drums_only = !(st.drums_only === false); renderLcdMenu(); toast(st.drums_only ? 'DRUMS ONLY' : 'ALL PARTS - style reloads', 3500); }
      } else openPage('/plugins');
    } else if (menuPage === 'pslot') {
      const c = plCur();
      if (menuIndex === 0) {
        const d = await api('/api/plugins');
        plCatalog = ((d && d.catalog) || []).filter(e => e.instrument !== false && !e.error);
        goPl('pload', 0);
        if (!plCatalog.length) toast('Nothing found: press SCAN FOR PLUGINS, or add a folder in the manager.', 6000);
      } else if (menuIndex === 1) plApi({ op: 'editor', slot: plSlot });
      else if (menuIndex === 2) goPl('proute', 0);
      else if (menuIndex === 3) plApi({ op: 'set', slot: plSlot, level: Math.max(0, c.level - 10) });
      else if (menuIndex === 4) plApi({ op: 'set', slot: plSlot, level: Math.min(150, c.level + 10) });
      else if (menuIndex === 5) plApi({ op: 'set', slot: plSlot, mute: !c.mute });
      else if (menuIndex === 6) { await plApi({ op: 'savestate', slot: plSlot }); toast('Sound saved'); }
      else if (menuIndex === 7) { await plApi({ op: 'clear', slot: plSlot }); toast('Slot ' + (plSlot + 1) + ' cleared'); }
    } else if (menuPage === 'pload') {
      const e = plCatalog[menuIndex];
      if (!e) return;
      toast('Loading ' + e.name + ' ...', 5000);
      plApi({ op: 'load', slot: plSlot, path: e.path, name: e.name });
      goPl('pslot', 0);
    } else if (menuPage === 'proute') {
      plApi({ op: 'part', slot: plSlot, part: PL_PARTS[menuIndex] });
    }
  }
  const prevOpenPl = window.openSelectedMenu;
  window.openSelectedMenu = function () {
    if (menuPage === 'root' && menuIndex === 5) { goPl('plugins', 0); return; }
    if (PL_PAGES.indexOf(menuPage) >= 0) { plEnter(); return; }
    return prevOpenPl ? prevOpenPl.apply(this, arguments) : undefined;
  };
  document.addEventListener('click', ev => {                 // NAV-LEFT steps back one page inside PLUGINS & VST
    if (!ev.target.closest('[data-id="NAV-LEFT"]') || !plVisible()) return;
    ev.stopImmediatePropagation(); ev.preventDefault();
    if (menuPage === 'plugins') goPl('root', 5);
    else if (menuPage === 'pslot') goPl('plugins', plSlot);
    else if (menuPage === 'pload') goPl('pslot', 0);
    else goPl('pslot', 2);
  }, true);

  // ---- DRUMKITS ----------------------------------------------------------------------------------
  // MENU > STYLES > DRUMKITS lists the kits on the LCD exactly like a P folder lists styles: every kit is a folder of
  // .wav files (DRUMS\<kit>, or any folder you add), the LCD status line shows the highlighted kit's folder, ENTER loads
  // it (the old kit keeps playing until the new one is ready). First entry = add a folder, second = let the style choose.
  const KIT_ADD = '+ ADD KIT FOLDER', KIT_AUTO = 'AUTO (STYLE KIT)';
  let kitList = [], kitSig = '', kitWasLoading = false;
  const inKit = () => kitMode && typeof lcdFiles !== 'undefined' && lcdFiles.classList.contains('visible');
  const kitIndex = () => filePage * filesPerPage + fileRow * 2 + fileColumn;
  const shortPath = (t, n) => t.length > n ? '\u2026' + t.slice(t.length - n + 1) : t;

  const kitCss = document.createElement('style');          // a long folder path must not push the LCD layout around
  kitCss.textContent = '.lcd-files-status{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}';
  document.head.appendChild(kitCss);

  function kitStatus() {
    const d = st.drumkit || {};
    if (d.loading) return 'LOADING ' + String(d.loading.kit).toUpperCase() + '  ' + d.loading.done + '/' + d.loading.total;
    const i = kitIndex();
    if (i === 0) return 'DRUMKITS  ENTER = CHOOSE A FOLDER OF .WAV FILES';
    if (i === 1) return 'DRUMKITS  AUTO: THE STYLE PICKS THE KIT' + (d.manual ? '  (NOW: MANUAL)' : '  (ACTIVE)');
    const k = kitList[i - 2];
    if (!k) return 'DRUMKITS';
    return 'KIT ' + (i - 1) + '/' + kitList.length + '  ' + shortPath(k.label, 34).toUpperCase() + '  ' + k.files + ' WAV' +
      (k.active ? '  \u25cf ACTIVE' : '') + (k.extra ? '  (ADDED)' : '');
  }

  function markActiveKit() {                               // the panel's own "loaded" dot, on the kit that is playing
    if (typeof lcdFiles === 'undefined') return;
    lcdFiles.querySelectorAll('.lcd-file').forEach(el => {
      const k = kitList[filePage * filesPerPage + Number(el.dataset.index) - 2];
      el.classList.toggle('loaded', !!(k && k.active));
    });
  }

  async function kitFetch() {
    const r = await api('/api/drumfolder');
    if (!r || r.error) { toast((r && r.error) || 'Drumkit engine not reachable', 6000); return null; }
    kitList = r.kits;
    userFiles = [KIT_ADD, KIT_AUTO].concat(r.labels);
    return r;
  }

  async function openKitBrowser() {
    const r = await kitFetch();
    if (!r) return;
    bridgeSlot = null; padMode = false; kitMode = true;
    openFileBrowser('user', 0);
    const a = kitList.findIndex(k => k.active);            // start on the kit that is playing
    if (a >= 0) {
      const i = a + 2;
      filePage = Math.floor(i / filesPerPage); fileRow = Math.floor((i % filesPerPage) / 2); fileColumn = i % 2;
      renderFileBrowser();
    }
    if (!kitList.length) toast('Put a folder of .wav files in ' + r.root + ' or use + ADD KIT FOLDER', 8000);
  }

  async function kitRefresh() {                            // keep the list and the status line current, cursor stays
    if (!inKit()) return;
    if (await kitFetch()) renderFileBrowser();
  }

  function kitPoll() {                                     // called every poll: progress, finished, errors
    const d = st.drumkit;
    if (!d) return;
    const sig = JSON.stringify([d.ready, d.manual, d.loading && d.loading.done, d.error]);
    if (d.loading) kitWasLoading = true;
    if (!d.loading && kitWasLoading) {
      kitWasLoading = false;
      toast(d.error ? 'DRUMKIT: ' + d.error : 'KIT ' + String(d.ready).toUpperCase() + ': ' + d.mapped + ' NOTES READY' +
        (d.failed ? ' (' + d.failed + ' FILES FAILED)' : ''), 5000);
    }
    if (sig !== kitSig) { kitSig = sig; if (inKit()) { if (d.loading) { titleText = kitStatus(); applyTitle(); } else kitRefresh(); } }
  }

  document.addEventListener('click', async ev => {         // ENTER inside the DRUMKITS list
    if (!ev.target.closest('[data-id="NAV-ENTER"]') || !inKit()) return;
    const i = kitIndex(), hear = !st.running;               // hear a short kick / snare / hat when the beat is not playing
    let r;
    if (i === 0) {
      toast('Choose the kit folder in the window that opened...', 8000);
      r = await api('/api/drumkit', { op: 'allocate', audition: hear });
      if (r && r.cancelled) { toast('No folder chosen.'); return; }
    } else if (i === 1) {
      r = await api('/api/drumkit', { op: 'use_default', audition: hear });
    } else {
      const k = kitList[i - 2];
      if (!k) return;
      toast('Loading kit ' + k.name + ' ...', 4000);
      r = await api('/api/drumkit', { op: 'select', id: k.id, audition: hear });
    }
    if (!r || r.ok === false || r.error) toast((r && r.error) || 'Drumkit engine not reachable', 6000);
    kitRefresh();
  });

  document.addEventListener('contextmenu', async ev => {   // right-click a kit you added = remove it from the list
    const el = ev.target.closest('.lcd-file');
    if (!el || !inKit()) return;
    const k = kitList[filePage * filesPerPage + Number(el.dataset.index) - 2];
    if (!k || !k.extra) return;
    ev.preventDefault();
    if (!confirm('Remove "' + k.name + '" from the kit list?\n(The folder and its files are not deleted.)')) return;
    const r = await api('/api/drumkit', { op: 'forget', id: k.id });
    toast(r && r.ok === false ? r.error : 'Removed ' + k.name, 4000);
    kitRefresh();
  });

  if (typeof menuPages !== 'undefined') {                  // DRUMKITS = 4th entry of the STYLES page (after MULTIPAD)
    const dSt = Object.getOwnPropertyDescriptor(menuPages, 'styles');
    if (dSt && dSt.get) Object.defineProperty(menuPages, 'styles', {
      configurable: true, enumerable: true,
      get() { return dSt.get.call(menuPages).concat(['DRUMKITS']); }
    });
    const prevOpenKit = window.openSelectedMenu;
    window.openSelectedMenu = function () {
      if (menuPage === 'styles' && menuIndex === 3) { openKitBrowser(); return; }
      return prevOpenKit ? prevOpenKit.apply(this, arguments) : undefined;
    };
  }

  // ---- tempo and master volume -------------------------------------------
  const bpmBox = document.getElementById('bpm');
  if (bpmBox) {
    // One request at a time, always carrying the LATEST value: quick presses can no longer arrive out of order.
    let tempoBusy = false, tempoSent = null;
    const pushTempo = async () => {
      if (tempoBusy) return;
      tempoBusy = true;
      try {
        while (tempoSent !== Number(bpmBox.value)) {
          tempoSent = Number(bpmBox.value);
          await api('/api/cmd', { op: 'tempo', arg: tempoSent });
        }
      } finally { tempoBusy = false; }
    };
    bpmBox.addEventListener('input', () => {
      if (fromServer || !st.loaded) return;
      pushTempo();
    });
  }
  // MASTER and VOL-1..6 are changed from the LCD (NAV2 -/+); the panel then redraws that screen
  // through showComponentStatus(), so we send the new value from there.
  const origStatus = window.showComponentStatus;
  window.showComponentStatus = function (component) {
    if (origStatus) origStatus.apply(this, arguments);
    if (component === 'MASTER' && typeof masterVolume !== 'undefined') {
      api('/api/cmd', { op: 'master', arg: masterVolume });
    } else if (/^VOL-\d$/.test(component) && typeof componentVolumes !== 'undefined') {
      api('/api/cmd', { op: 'partvol', arg: component, value: componentVolumes[component] });
    }
  };

  // ---- RESET (tempo tap button), MUTE, EFX, MENU, registrations ------------------------------------------------
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-id]');
    if (!el) return;
    const id = el.dataset.id;
    if (id === 'TEMPO-TAP') {                                              // RESET: the beat restarts from bar 1, beat 1
      if (!inCreate() && st.running) api('/api/cmd', { op: 'restart' });
    } else if (id === 'MUTE-TAP') api('/api/cmd', { op: 'mute' });          // silences the sound
    else if (id === 'EFX') api('/api/cmd', { op: 'efx' });                  // reverb + chorus on/off
    else if (id === 'PERC-MENU2' && typeof showRootMenu === 'function') showRootMenu();
  });

  // R-1..R-4: tap = recall, hold (or right-click) = save the current style, section, tempo and volumes
  let regTimer = null, regHeld = false;
  async function regSave(n) {
    const vols = typeof componentVolumes !== 'undefined' ? Object.assign({}, componentVolumes) : {};
    await api('/api/cmd', { op: 'reg_save', arg: n, vols: vols, master: typeof masterVolume !== 'undefined' ? masterVolume : 100 });
    toast('REGISTRATION ' + n + ' SAVED');
  }
  async function regRecall(n) {
    const r = await api('/api/cmd', { op: 'reg_recall', arg: n });
    if (!r || !r.ok) { toast('REGISTRATION ' + n + ' IS EMPTY - hold the button to save'); return; }
    if (typeof componentVolumes !== 'undefined' && r.vols) Object.assign(componentVolumes, r.vols);
    if (typeof masterVolume !== 'undefined') masterVolume = r.master;
    toast('REGISTRATION ' + n + ' RECALLED');
  }
  document.addEventListener('pointerdown', ev => {
    const b = ev.target.closest('[data-id^="R-"]');
    if (!b || !/^R-[1-4]$/.test(b.dataset.id)) return;
    clearTimeout(regTimer);
    regHeld = false;
    regTimer = setTimeout(() => { regHeld = true; regSave(Number(b.dataset.id.slice(2))); }, 700);
  }, true);
  ['pointerup', 'pointercancel'].forEach(n => document.addEventListener(n, () => clearTimeout(regTimer), true));
  document.addEventListener('click', ev => {
    const b = ev.target.closest('[data-id^="R-"]');
    if (!b || !/^R-[1-4]$/.test(b.dataset.id)) return;
    if (regHeld) { regHeld = false; return; }
    regRecall(Number(b.dataset.id.slice(2)));
  });
  document.addEventListener('contextmenu', ev => {
    const b = ev.target.closest('[data-id^="R-"]');
    if (b && /^R-[1-4]$/.test(b.dataset.id)) { ev.preventDefault(); regSave(Number(b.dataset.id.slice(2))); }
  });

  // ---- state from the engine ---------------------------------------------
  // Section buttons: light blue glow = the style has that section, orange glow (pulsing with the tempo) = playing
  // now; sections the style lacks are dimmed. SYNCRO start, A.FH, MUTE, EFX and pads keep the panel's own lit look.
  function paintLamps() {
    const mu = $id('MUTE-TAP'), fx = $id('EFX');
    if (mu) mu.classList.toggle('active', !!st.muted);
    if (fx) fx.classList.toggle('active', !!st.efx);
    if (!inCreate()) (st.pads || []).forEach((p, i) => {      
      const el = $id('P-' + (i + 1));                         // light blue = a pad file is loaded, orange = playing
      if (!el) return;
      el.classList.toggle('playing', !!p.playing);
      el.classList.toggle('avail', !!p.name && !p.playing);
      el.classList.remove('active');
    });
    if (st.pad_error && st.pad_error !== lastPadError) toast(st.pad_error, 5000);
    lastPadError = st.pad_error || '';
    if (!st.loaded || inCreate()) return;
    const sec = st.section || '';
    const last = sec.slice(-1);
    const lit = {}, playing = {};
    if (st.running) {
      lit['SY-1'] = true;
      if (sec.indexOf('Intro ') === 0) playing['I-' + last] = true;
      else if (sec === 'Fill In BA') playing['BR-1'] = true;
      else if (sec.indexOf('Ending ') === 0) playing['EN-' + (sec.charCodeAt(sec.length - 1) - 64)] = true;
      else if (sec) playing['V-' + last] = true;          // Main X, or the fill leading into it
    } else if (st.intro) lit['I-' + st.intro] = true;
    if (st.main) lit['V-' + st.main.slice(-1)] = true;
    if (st.auto_fill) lit['AFH'] = true;
    LAMPS.forEach(id => {
      const el = $id(id);
      if (!el) return;
      const need = NEEDS_SECTION[id];
      const has = !need || !st.available || st.available.indexOf(need) >= 0;
      if (SECTION_LAMP[id]) {                    // section buttons: light blue = available, orange = playing now
        el.classList.toggle('playing', has && !!playing[id]);
        el.classList.toggle('avail', has && !playing[id]);
        el.classList.remove('active');
      } else {
        el.classList.toggle('active', has && (!!lit[id] || !!playing[id]));
      }
      el.style.opacity = has ? '' : '0.4';
      const bulb = el.querySelector('.bulb');
      if (bulb) bulb.style.animation = playing[id] ? 'beat-pulse-orange var(--beat-duration, 0.5s) linear infinite' : '';
    });
  }

  async function poll() {
    const s = await api('/api/state');
    if (s) {
      st = s;
      if (st.rev !== lastRev) {                  // a new style was loaded
        if (lastRev !== -1 && bpmBox && st.loaded) {
          fromServer = true;
          bpmBox.value = Math.round(st.bpm);
          bpmBox.dispatchEvent(new Event('input', { bubbles: true }));
          fromServer = false;
          toast(st.message || ('Loaded ' + st.name), 4000);
        }
        lastRev = st.rev;
      }
      const sig = JSON.stringify(st.plugins) + st.drums_only;
      if (sig !== plSig) { plSig = sig; if (plVisible()) renderLcdMenu(); }
      paintLamps();
      kitPoll();
    } else {
      st = { loaded: false };
    }
    renderBadge();
  }
  setInterval(poll, 250);
  poll();
})();
