// Shared top navigation + theme switcher (dark / light / military).
// Each page includes <div id="topnav"></div> and <script src="/nav.js"></script>.
// Theme is kept in sessionStorage so all three pages stay in sync within the same
// browser tab; closing the tab clears it and the next visit defaults to dark.
(function () {
  const THEME_KEY = 'agent3d-theme';
  const TABS = [
    { href: '/',           label: '🏛️ 生成模型', match: p => p === '/' || p.endsWith('/index.html') },
    { href: '/scene.html', label: '🌆 实景模型', match: p => p.endsWith('/scene.html') },
    { href: '/blast.html', label: '💥 爆炸仿真', match: p => p.endsWith('/blast.html') },
  ];

  const THEMES = [
    { id: 'dark',      label: '深色' },
    { id: 'light',     label: '浅色' },
    { id: 'military',  label: '战术' },
  ];
  const THEME_IDS = new Set(THEMES.map(t => t.id));

  function readStoredTheme() {
    try {
      const v = sessionStorage.getItem(THEME_KEY);
      return THEME_IDS.has(v) ? v : 'dark';
    } catch (_) {
      return 'dark';
    }
  }

  function writeStoredTheme(id) {
    try { sessionStorage.setItem(THEME_KEY, id); } catch (_) {}
  }

  // Apply as early as this script runs so navigations between pages keep the theme.
  document.documentElement.setAttribute('data-theme', readStoredTheme());

  const CSS = `
  /* ---- theme tokens (dark = default, also :root fallback on each page) ---- */
  :root, [data-theme="dark"]{
    color-scheme:dark;
    --bg:#0f1216; --panel:#12161c; --card:#151a21; --line:#232830; --line2:#2a303a;
    --text:#e6e8eb; --muted:#8a919c; --accent:#3a7afe; --accent2:#5b93ff;
    --ok:#34d399; --err:#ff6b6b; --fire:#ff7a59; --fire2:#e5482a;
    --input:#0f141a; --input2:#0d1116; --nav-bg:#232a34; --nav-hover:#2b333f; --nav-text:#dbe3ee;
    --dot-bg:#1c222b; --dot-active:#1a2740; --chip:#20283a; --label:#aab1bc;
    --drop-border:#333b46; --drop-hover:#48546a; --hint-soft:#c7d2fe; --empty:#4b5563; --hint:#6b7280;
    --stage1:#1a2030; --stage2:#12151a; --stage-blast1:#1e1518;
    --btn-disabled-bg:#20242a; --btn-disabled-fg:#5b626c; --model-fg:#9fb3d1;
    --warn-border:#e5a13a; --warn-fg:#ffcf87; --warn-bg:#2a2415;
    --accent-glow:rgba(58,122,254,.15); --accent-glow-sm:rgba(58,122,254,.08);
    --accent-glow-md:rgba(58,122,254,.12); --accent-glow-lg:rgba(58,122,254,.20);
    --accent-shadow:rgba(58,122,254,.7); --fire-shadow:rgba(229,72,42,.75);
    --hover-border:#39424d; --desc:#7f8894; --novox:#c9a24a; --busy:#e2cb4e;
    --chk-on-ok:#0b0e12; --modal-scrim:rgba(6,9,13,.62); --x-hover:#20262e;
  }
  [data-theme="light"]{
    color-scheme:light;
    --bg:#f4f6f9; --panel:#eef1f6; --card:#ffffff; --line:#d8dee8; --line2:#c9d1dc;
    --text:#1a2332; --muted:#5c6778; --accent:#2f6fed; --accent2:#4b86f7;
    --ok:#059669; --err:#dc2626; --fire:#e85d3a; --fire2:#c73a1f;
    --input:#ffffff; --input2:#f0f3f8; --nav-bg:#e4e9f0; --nav-hover:#d5dce6; --nav-text:#2a3444;
    --dot-bg:#e8edf4; --dot-active:#dce8ff; --chip:#e8eef8; --label:#5c6778;
    --drop-border:#c9d1dc; --drop-hover:#9aa8bc; --hint-soft:#3a5a9a; --empty:#8a94a3; --hint:#8a94a3;
    --stage1:#e8eef8; --stage2:#f4f6f9; --stage-blast1:#f5ebe8;
    --btn-disabled-bg:#e4e9f0; --btn-disabled-fg:#9aa3b0; --model-fg:#3a5a9a;
    --warn-border:#d97706; --warn-fg:#b45309; --warn-bg:#fff7ed;
    --accent-glow:rgba(47,111,237,.18); --accent-glow-sm:rgba(47,111,237,.10);
    --accent-glow-md:rgba(47,111,237,.14); --accent-glow-lg:rgba(47,111,237,.22);
    --accent-shadow:rgba(47,111,237,.45); --fire-shadow:rgba(199,58,31,.55);
    --hover-border:#b0bac8; --desc:#6b7585; --novox:#b45309; --busy:#a16207;
    --chk-on-ok:#fff; --modal-scrim:rgba(20,28,40,.45); --x-hover:#e8edf4;
  }
  [data-theme="military"]{
    color-scheme:dark;
    --bg:#12160f; --panel:#161b12; --card:#1a2116; --line:#2a3324; --line2:#35402c;
    --text:#e4ead8; --muted:#8f9a7c; --accent:#6b8f3c; --accent2:#8fb85a;
    --ok:#7cb342; --err:#e07050; --fire:#ff7a59; --fire2:#e5482a;
    --input:#0f140c; --input2:#0c100a; --nav-bg:#2a3324; --nav-hover:#35402c; --nav-text:#d8e0c8;
    --dot-bg:#1e2618; --dot-active:#243018; --chip:#243018; --label:#9aa68a;
    --drop-border:#3a4530; --drop-hover:#546040; --hint-soft:#c5d4a8; --empty:#5a6450; --hint:#6b7560;
    --stage1:#1a2414; --stage2:#12160f; --stage-blast1:#1e1812;
    --btn-disabled-bg:#22281a; --btn-disabled-fg:#5a6450; --model-fg:#a8bc80;
    --warn-border:#c9a24a; --warn-fg:#e8c86a; --warn-bg:#2a2410;
    --accent-glow:rgba(107,143,60,.20); --accent-glow-sm:rgba(107,143,60,.10);
    --accent-glow-md:rgba(107,143,60,.14); --accent-glow-lg:rgba(107,143,60,.22);
    --accent-shadow:rgba(107,143,60,.55); --fire-shadow:rgba(229,72,42,.75);
    --hover-border:#455038; --desc:#7a8568; --novox:#c9a24a; --busy:#d4b84a;
    --chk-on-ok:#0e120a; --modal-scrim:rgba(6,10,4,.65); --x-hover:#22281a;
  }

  /* ---- topnav layout ---- */
  #topnav{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  #topnav .nav-tabs{display:flex;align-items:center;gap:6px}
  #topnav a.nav-tab{display:inline-flex;align-items:center;gap:6px;text-decoration:none;
    font-size:13px;font-weight:600;color:var(--nav-text);background:var(--nav-bg);border:1px solid var(--line2);
    padding:7px 14px;border-radius:9px;transition:background .15s,color .15s,box-shadow .2s,transform .12s}
  #topnav a.nav-tab:hover{background:var(--nav-hover);color:var(--text);transform:translateY(-1px)}
  #topnav a.nav-tab.active{color:#fff;background:linear-gradient(180deg,var(--accent2),var(--accent));
    border-color:transparent;box-shadow:0 6px 18px -8px var(--accent-shadow);cursor:default}
  #topnav a.nav-tab.active:hover{transform:none}

  #theme-switch{display:inline-flex;align-items:center;gap:2px;background:var(--input);
    border:1px solid var(--line2);border-radius:9px;padding:2px}
  #theme-switch button{appearance:none;border:0;background:transparent;color:var(--muted);
    font-size:12px;font-weight:600;font-family:inherit;padding:5px 10px;border-radius:7px;
    cursor:pointer;transition:background .15s,color .15s}
  #theme-switch button:hover{color:var(--text);background:var(--nav-bg)}
  #theme-switch button.active{color:#fff;background:linear-gradient(180deg,var(--accent2),var(--accent))}
  @media (prefers-reduced-motion: reduce){
    #topnav a.nav-tab,#theme-switch button{transition:none}
  }
  `;

  function currentTheme() {
    const attr = document.documentElement.getAttribute('data-theme');
    return THEME_IDS.has(attr) ? attr : readStoredTheme();
  }

  function setTheme(id) {
    if (!THEME_IDS.has(id)) id = 'dark';
    document.documentElement.setAttribute('data-theme', id);
    writeStoredTheme(id);
    const box = document.getElementById('theme-switch');
    if (!box) return;
    box.querySelectorAll('button').forEach(b => {
      b.classList.toggle('active', b.dataset.theme === id);
    });
  }

  function mount() {
    const host = document.getElementById('topnav');
    if (!host) return;

    if (!document.getElementById('agent3d-theme-css')) {
      const style = document.createElement('style');
      style.id = 'agent3d-theme-css';
      style.textContent = CSS;
      document.head.appendChild(style);
    }

    // Re-apply stored theme on every page so all three stay in sync.
    setTheme(readStoredTheme());

    const path = location.pathname;
    const tabs = TABS.map(t => {
      const active = t.match(path);
      return `<a class="nav-tab${active ? ' active' : ''}" href="${t.href}"` +
             `${active ? ' aria-current="page"' : ''}>${t.label}</a>`;
    }).join('');

    const cur = currentTheme();
    const themes = THEMES.map(t =>
      `<button type="button" data-theme="${t.id}" class="${t.id === cur ? 'active' : ''}"` +
      ` title="切换为${t.label}">${t.label}</button>`
    ).join('');

    host.innerHTML =
      `<div class="nav-tabs">${tabs}</div>` +
      `<div id="theme-switch" role="group" aria-label="颜色风格">${themes}</div>`;

    host.querySelector('#theme-switch').addEventListener('click', e => {
      const btn = e.target.closest('button[data-theme]');
      if (!btn) return;
      setTheme(btn.dataset.theme);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
