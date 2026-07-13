// inspect_ui.js — the "剖析" (inspection) control panel. Builds a small DOM
// overlay in the top-right corner and wires it to an inspect system created by
// inspect.js. Shown only while the viewer is in inspect mode (viewer.html toggles
// it). Mirrors blast_ui.js's {element, setVisible} contract and visual tokens.

const css = `
.inspect-panel{position:fixed;top:60px;right:12px;z-index:11;width:230px;background:rgba(24,28,34,.94);
  border:1px solid #333b45;border-radius:10px;padding:12px;color:#dfe4ea;font:13px/1.5 system-ui,sans-serif;
  box-shadow:0 6px 24px rgba(0,0,0,.4)}
.inspect-panel h4{margin:0 0 8px;font-size:13px;color:#8fb2ff;font-weight:600}
.inspect-panel .modes{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:4px}
.inspect-panel .modes button{flex:1 1 40%;width:auto;margin:0;padding:6px 4px;border:0;border-radius:7px;
  background:#2a313a;color:#cbd3db;cursor:pointer;font-size:12px}
.inspect-panel .modes button.active{background:#3a7afe;color:#fff}
.inspect-panel .row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:7px 0}
.inspect-panel label{color:#9aa4af;flex:0 0 auto}
.inspect-panel input[type=range]{flex:1;accent-color:#3a7afe}
.inspect-panel .val{flex:0 0 40px;text-align:right;color:#cfd6dd;font-variant-numeric:tabular-nums}
.inspect-panel .axis{display:flex;gap:4px}
.inspect-panel .axis button{width:auto;margin:0;padding:4px 10px;border:0;border-radius:6px;
  background:#1b2027;color:#cbd3db;cursor:pointer;font-size:12px;border:1px solid #39424d}
.inspect-panel .axis button.active{background:#3a7afe;color:#fff;border-color:#3a7afe}
.inspect-panel .ctl{display:none;margin-top:2px}
.inspect-panel .ctl.show{display:block}
.inspect-panel .flip{width:100%;padding:7px;border:0;border-radius:8px;background:#2a313a;color:#cbd3db;
  cursor:pointer;font-size:12px;margin-top:4px}
.inspect-panel .sep{height:1px;background:#2c343d;margin:9px 0}
.inspect-panel .hint{color:#6b7683;font-size:11px;margin-top:8px}
`;

export function createInspectUI(inspect) {
  if (!document.getElementById('inspect-panel-css')) {
    const st = document.createElement('style'); st.id = 'inspect-panel-css'; st.textContent = css;
    document.head.appendChild(st);
  }
  const d = inspect.defaults();

  const el = document.createElement('div');
  el.className = 'inspect-panel';
  el.innerHTML = `
    <h4>剖析模式</h4>
    <div class="modes">
      <button data-m="none" class="active">正常</button>
      <button data-m="xray">半透明透视</button>
      <button data-m="explode">分层展开</button>
      <button data-m="section">剖切</button>
    </div>
    <div class="sep"></div>
    <div class="ctl" data-for="xray">
      <div class="row"><label>透明度</label>
        <input type="range" data-k="opacity" min="10" max="90" value="${Math.round(d.opacity * 100)}">
        <span class="val" data-k="opacity-v">${Math.round(d.opacity * 100)}%</span></div>
    </div>
    <div class="ctl" data-for="explode">
      <div class="row"><label>层高</label>
        <input type="range" data-k="floorH" min="1" max="${d.floorHMax}" value="${d.floorH}">
        <span class="val" data-k="floorH-v">${d.floorH}</span></div>
      <div class="row"><label>层间距</label>
        <input type="range" data-k="gap" min="0" max="${d.gapMax}" value="${d.gap}">
        <span class="val" data-k="gap-v">${d.gap}</span></div>
    </div>
    <div class="ctl" data-for="section">
      <div class="row"><label>方向</label><span class="axis">
        <button data-ax="x">X</button><button data-ax="y" class="active">Y</button><button data-ax="z">Z</button>
      </span></div>
      <div class="row"><label>位置</label>
        <input type="range" data-k="t" min="0" max="100" value="${Math.round(d.t * 100)}">
        <span class="val" data-k="t-v">${Math.round(d.t * 100)}%</span></div>
      <button class="flip" data-k="flip">翻转方向</button>
    </div>
    <div class="hint">选择一种模式查看建筑内部结构。<br>切回其他视图会自动还原。</div>`;

  const q = (k) => el.querySelector(`[data-k="${k}"]`);
  const ctl = (m) => el.querySelector(`.ctl[data-for="${m}"]`);
  const modeBtns = [...el.querySelectorAll('.modes button')];

  // --- mode selection (single-choice) ---
  function selectMode(m) {
    modeBtns.forEach(b => b.classList.toggle('active', b.dataset.m === m));
    el.querySelectorAll('.ctl').forEach(c => c.classList.toggle('show', c.dataset.for === m));
    inspect.setMode(m);
  }
  modeBtns.forEach(b => b.addEventListener('click', () => selectMode(b.dataset.m)));

  // --- X-Ray ---
  q('opacity').addEventListener('input', () => {
    const v = Number(q('opacity').value); q('opacity-v').textContent = v + '%'; inspect.setOpacity(v / 100);
  });

  // --- Exploded Floors ---
  q('floorH').addEventListener('input', () => {
    const v = Number(q('floorH').value); q('floorH-v').textContent = v; inspect.setFloorHeight(v);
  });
  q('gap').addEventListener('input', () => {
    const v = Number(q('gap').value); q('gap-v').textContent = v; inspect.setGap(v);
  });

  // --- Section Cut ---
  const axisBtns = [...el.querySelectorAll('.axis button')];
  axisBtns.forEach(b => b.addEventListener('click', () => {
    axisBtns.forEach(x => x.classList.toggle('active', x === b));
    inspect.setSection({ axis: b.dataset.ax });
  }));
  q('t').addEventListener('input', () => {
    const v = Number(q('t').value); q('t-v').textContent = v + '%'; inspect.setSection({ t: v / 100 });
  });
  let flipped = false;
  q('flip').addEventListener('click', () => { flipped = !flipped; inspect.setSection({ flip: flipped }); });

  el.style.display = 'none';
  return {
    element: el,
    setVisible: (v) => {
      el.style.display = v ? 'block' : 'none';
      if (!v) selectMode('none');   // reset the mode to 正常 on hide (matches inspect.reset); slider/flip prefs persist
    },
  };
}
