// blast_ui.js — the "打击" (strike) control panel. Builds a small DOM overlay and
// wires it to a blast system created by blast.js. The panel is shown only while
// the viewer is in blast mode (viewer.html toggles it).
//
// Strike-point picking is handled by viewer.html (it owns the canvas + mode
// state) and calls blast.pick(); this module only owns the panel controls.

const PRESET_LABELS = {
  surface: '表面混合（杀爆）',
  pen_pair: '侵彻对（钻地×2）',
  carpet: '地毯侵彻（钻地×8）',
  breach_pen: '先开坑后侵彻',
};

const css = `
.blast-panel{position:fixed;top:60px;left:12px;z-index:11;width:230px;background:rgba(24,28,34,.94);
  border:1px solid #333b45;border-radius:10px;padding:12px;color:#dfe4ea;font:13px/1.5 system-ui,sans-serif;
  box-shadow:0 6px 24px rgba(0,0,0,.4)}
.blast-panel h4{margin:0 0 8px;font-size:13px;color:#8fb2ff;font-weight:600}
.blast-panel .row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:7px 0}
.blast-panel label{color:#9aa4af;flex:0 0 auto}
.blast-panel select,.blast-panel input[type=number]{background:#1b2027;color:#e6e6e6;border:1px solid #39424d;
  border-radius:6px;padding:4px 6px;font-size:13px;max-width:120px}
.blast-panel input[type=range]{flex:1;accent-color:#3a7afe}
.blast-panel .val{flex:0 0 34px;text-align:right;color:#cfd6dd;font-variant-numeric:tabular-nums}
.blast-panel button{width:100%;padding:8px;border:0;border-radius:8px;background:#3a7afe;color:#fff;
  cursor:pointer;font-size:13px;margin-top:6px}
.blast-panel button.ghost{background:#2a313a;color:#cbd3db}
.blast-panel .hint{color:#6b7683;font-size:11px;margin-top:8px}
.blast-panel .sep{height:1px;background:#2c343d;margin:10px 0}
.blast-panel .badge{font-size:10px;padding:1px 6px;border-radius:20px;margin-left:6px}
`;

export function createBlastUI(blast) {
  if (!document.getElementById('blast-panel-css')) {
    const st = document.createElement('style'); st.id = 'blast-panel-css'; st.textContent = css;
    document.head.appendChild(st);
  }
  const munis = blast.munitions();
  const muniOptions = munis.map(m => `<option value="${m.id}">${m.label}</option>`).join('');
  const presetOptions = blast.presetKeys().map(k => `<option value="${k}">${PRESET_LABELS[k] || k}</option>`).join('');

  const el = document.createElement('div');
  el.className = 'blast-panel';
  el.innerHTML = `
    <h4>打击控制</h4>
    <div class="row"><label>预设方案</label><select data-k="preset">${presetOptions}</select></div>
    <button class="ghost" data-k="run-preset">运行预设</button>
    <div class="sep"></div>
    <div class="row"><label>弹种</label><select data-k="muni">${muniOptions}</select></div>
    <div class="row"><label>俯冲角</label><input type="range" data-k="dive" min="20" max="90" value="80"><span class="val" data-k="dive-v">80°</span></div>
    <div class="row"><label>方位角</label><input type="range" data-k="az" min="0" max="359" value="0"><span class="val" data-k="az-v">0°</span></div>
    <div class="row"><label>连投数</label><input type="number" data-k="count" min="1" max="16" value="1"></div>
    <div class="row"><label>散布(格)</label><input type="number" data-k="spread" min="1" max="30" value="8"></div>
    <button data-k="drop">投弹</button>
    <button class="ghost" data-k="reset">重置</button>
    <div class="hint">在建筑上<b>点击</b>设置打击点，然后投弹。<br>连投数&gt;1 时以打击点为中心随机散布。</div>`;

  const q = (k) => el.querySelector(`[data-k="${k}"]`);
  const num = (k) => Number(q(k).value);

  const syncAim = () => blast.setAim(num('dive'), num('az'));   // live-update the incidence arrow
  q('dive').addEventListener('input', () => { q('dive-v').textContent = q('dive').value + '°'; syncAim(); });
  q('az').addEventListener('input', () => { q('az-v').textContent = q('az').value + '°'; syncAim(); });

  q('run-preset').addEventListener('click', () => blast.runPreset(q('preset').value));
  q('reset').addEventListener('click', () => blast.reset());

  q('drop').addEventListener('click', () => {
    if (!blast.hasStrike()) { flash(q('drop'), '先在建筑上点击选点'); return; }
    const munitionId = q('muni').value, dive = num('dive'), azimuth = num('az');
    const n = Math.max(1, Math.min(16, num('count') || 1));
    if (n > 1) blast.fireSalvo({ munitionId, dive, azimuth, count: n, spread: Math.max(1, num('spread') || 8) });
    else blast.fireSingle({ munitionId, dive, azimuth });
  });

  let flashTimer = null;
  function flash(btn, msg) {
    const old = btn.textContent; btn.textContent = msg; btn.style.background = '#b4553a';
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { btn.textContent = old; btn.style.background = ''; }, 1200);
  }

  el.style.display = 'none';
  return {
    element: el,
    setVisible: (v) => { el.style.display = v ? 'block' : 'none'; },
  };
}
