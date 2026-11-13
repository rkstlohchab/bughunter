// GameLab.tsx — operator-mode game cheat workbench UI.
// Lives alongside Investigation / OperatorMode / Team panels. Not part of
// the investigation-agent surface.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { gameLab } from './gameLabApi';
import type { GameId, StackId, GameProfile, StackProfile, Project, Recommendation, HardwareInventory } from './gameLabApi';
import GameLabChat from './GameLabChat';

type View = 'overview' | 'new' | 'project';

export default function GameLab() {
  const [view, setView] = useState<View>('overview');
  const [games, setGames] = useState<GameProfile[]>([]);
  const [stacks, setStacks] = useState<StackProfile[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [hw, setHw] = useState<HardwareInventory | null>(null);
  const [active, setActive] = useState<Project | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [g, s, p, h] = await Promise.all([
        gameLab.listGames(), gameLab.listStacks(),
        gameLab.listProjects(), gameLab.hardwareCheck(),
      ]);
      setGames(g.games);
      setStacks(s.stacks);
      setProjects(p.projects);
      setHw(h);
    } catch (e: unknown) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  return (
    <div className="gamelab">
      <header className="gl-head">
        <h2>GameLab</h2>
        <div className="gl-head-right">
          <button onClick={() => setView('overview')}>Overview</button>
          <button onClick={() => setView('new')}>New project</button>
          <button onClick={() => gameLab.hardwareCheck(true).then(setHw)}>Rescan hardware</button>
        </div>
      </header>
      {err && <div className="gl-err">Error: {err}</div>}
      {view === 'overview' && (
        <Overview projects={projects} hw={hw} onOpen={(p) => { setActive(p); setView('project'); }} />
      )}
      {view === 'new' && games.length > 0 && hw && (
        <NewProject games={games} stacks={stacks} hw={hw}
                     onCreated={(p) => { setActive(p); setView('project'); reload(); }} />
      )}
      {view === 'project' && active && (
        <ProjectDetail project={active} onReload={async () => {
          const fresh = await gameLab.getProject(active.id);
          setActive(fresh);
        }} />
      )}
    </div>
  );
}

// ---- Overview -----------------------------------------------------------

function Overview({ projects, hw, onOpen }: {
  projects: Project[]; hw: HardwareInventory | null;
  onOpen: (p: Project) => void;
}) {
  return (
    <>
      <section className="gl-section">
        <h3>Hardware inventory</h3>
        {hw ? (
          <ul className="gl-hw">
            <li>Platform: <code>{hw.host_platform}</code></li>
            <li>CPU: <code>{hw.cpu || '?'}</code></li>
            <li>GPU: <code>{hw.gpu_vendor} {hw.gpu_name}</code></li>
            <li>CUDA: {hw.cuda_available ? 'yes' : 'no'}</li>
            <li>MLX: {hw.mlx_available ? 'yes' : 'no'}</li>
            <li>Capture cards: {hw.capture_cards.length ? hw.capture_cards.join(', ') : 'none detected'}</li>
            <li>Arduino candidates: {hw.arduino_candidates.length
              ? hw.arduino_candidates.map(a => `${a.device}(${a.product || 'n/a'})`).join(', ')
              : 'none'}</li>
            <li>ADB devices: {hw.adb_devices.length
              ? hw.adb_devices.map(d => `${d.serial}(${d.state})`).join(', ')
              : 'none'}</li>
            <li>PCILeech (DMA): {hw.pcileech_device ? 'yes' : 'not detected'}</li>
            <li>scrcpy: {hw.scrcpy_installed ? 'installed' : 'missing'}</li>
            <li>YOLO (ultralytics): {hw.yolo_installed ? 'installed' : 'missing'}</li>
            <li>OpenCV: {hw.opencv_installed ? 'installed' : 'missing'}</li>
          </ul>
        ) : <p>Loading…</p>}
      </section>

      <section className="gl-section">
        <h3>Projects</h3>
        {projects.length === 0 && <p>None. Click <em>New project</em>.</p>}
        <ul className="gl-projects">
          {projects.map(p => (
            <li key={p.id}>
              <button className="gl-link" onClick={() => onOpen(p)}>
                <strong>{p.name}</strong> — {p.game} / {p.stack || 'no stack'} ·
                <em> {p.status}</em>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

// ---- New project --------------------------------------------------------

function NewProject({ games, hw, onCreated }: {
  games: GameProfile[]; stacks: StackProfile[]; hw: HardwareInventory;
  onCreated: (p: Project) => void;
}) {
  const [name, setName] = useState('');
  const [game, setGame] = useState<GameId | ''>('');
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [stack, setStack] = useState<StackId | ''>('');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!game) { setRec(null); return; }
    gameLab.recommendStack(game as GameId, hw).then(setRec);
  }, [game, hw]);

  const go = async () => {
    if (!name || !game) return;
    setBusy(true);
    try {
      const p = await gameLab.createProject(name, game as GameId,
                                             stack as StackId || null, notes);
      onCreated(p);
    } finally { setBusy(false); }
  };

  return (
    <section className="gl-section">
      <h3>New GameLab project</h3>
      <label>Name<br />
        <input value={name} onChange={e => setName(e.target.value)} />
      </label>
      <br />
      <label>Game<br />
        <select value={game} onChange={e => setGame(e.target.value as GameId)}>
          <option value="">— choose —</option>
          {games.map(g => <option key={g.id} value={g.id}>{g.label}</option>)}
        </select>
      </label>
      {rec && (
        <div className="gl-rec">
          <p><strong>Anticheat:</strong> {rec.anticheat}</p>
          <p className="gl-note">{rec.notes}</p>
          <h4>Recommended stacks</h4>
          <ul>
            {rec.picks.map(p => (
              <li key={p.stack}>
                <label>
                  <input type="radio" name="stack" value={p.stack}
                         checked={stack === p.stack}
                         onChange={() => setStack(p.stack)} />
                  <strong>{p.label}</strong> — lifespan: <em>{p.lifespan_class}</em> ·
                  cost floor: ${p.cost_floor_usd} ·
                  {p.ready ? ' ready' : ` missing: ${p.missing_hardware.join(', ')}`}
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
      <label>Notes<br />
        <textarea value={notes} onChange={e => setNotes(e.target.value)} />
      </label>
      <br />
      <button disabled={busy || !name || !game} onClick={go}>Create project</button>
    </section>
  );
}

// ---- Project detail -----------------------------------------------------

function ProjectDetail({ project, onReload }: {
  project: Project; onReload: () => Promise<void>;
}) {
  const [log, setLog] = useState<string[]>([]);
  const recipes = useMemo(() => project.recipes || [], [project]);

  // Universal feature toggles
  const [features, setFeatures] = useState({
    memory_esp: true, radar: true,
    aim: false, trigger: false, recoil: false,
    draw_box: true, draw_head_dot: true, draw_snapline: true,
    draw_skeleton: true, draw_name: true, draw_hp_bar: true,
    draw_distance: true, draw_weapon: true, draw_loot: false,
    chams_mode: 'team',
    aim_smooth: 0.42, miss_rate: 0.08, reaction_floor_ms: 180,
    fov_px: 180, radar_range_m: 300,
  });
  const [hid, setHid] = useState({
    backend: 'arduino' as 'arduino' | 'kmbox' | 'adb',
    arduino_port: '', kmbox_endpoint: '', adb_serial: '',
  });

  const start = async (adapter: string, config: Record<string, unknown>) => {
    const r = await gameLab.startRecipe(project.id, adapter, config);
    setLog(l => [...l, `start ${adapter} → ${r.status}${r.error ? ' / ' + r.error : ''}`]);
    await onReload();
  };
  const stop = async (rid: string) => {
    await gameLab.stopRecipe(rid);
    await onReload();
  };
  const startFullKit = async () => {
    const r = await gameLab.startFullKit(project.id, features, hid);
    setLog(l => [...l, `full kit → ${r.steps.length} recipes started`]);
    await onReload();
  };

  const toggle = (k: string) => setFeatures(f => ({ ...f, [k]: !(f as any)[k] }));

  return (
    <section className="gl-section">
      <h3>{project.name} <small>— {project.game} / {project.stack || 'no stack'}</small></h3>
      <p>Status: {project.status}</p>
      {project.notes && <p className="gl-note">{project.notes}</p>}

      <h4>Feature toggles</h4>
      <div className="gl-feature-grid">
        <label><input type="checkbox" checked={features.memory_esp}
                       onChange={() => toggle('memory_esp')} /> Memory ESP</label>
        <label><input type="checkbox" checked={features.radar}
                       onChange={() => toggle('radar')} /> 2D radar</label>
        <label><input type="checkbox" checked={features.aim}
                       onChange={() => toggle('aim')} /> Aim</label>
        <label><input type="checkbox" checked={features.trigger}
                       onChange={() => toggle('trigger')} /> Trigger</label>
        <label><input type="checkbox" checked={features.recoil}
                       onChange={() => toggle('recoil')} /> No-recoil (writes)</label>
      </div>
      <h5>ESP primitives</h5>
      <div className="gl-feature-grid">
        {['draw_box','draw_head_dot','draw_snapline','draw_skeleton',
          'draw_name','draw_hp_bar','draw_distance','draw_weapon','draw_loot']
          .map(k => (
            <label key={k}>
              <input type="checkbox" checked={(features as any)[k]}
                     onChange={() => toggle(k)} /> {k.replace('draw_','')}
            </label>
          ))}
      </div>
      <label>Chams mode:{' '}
        <select value={features.chams_mode}
                onChange={e => setFeatures(f => ({ ...f, chams_mode: e.target.value }))}>
          <option value="team">team</option>
          <option value="visibility">visibility</option>
          <option value="distance">distance</option>
          <option value="off">off</option>
        </select>
      </label>

      <h4>HID output</h4>
      <div className="gl-feature-grid">
        <label>Backend:{' '}
          <select value={hid.backend}
                  onChange={e => setHid(h => ({ ...h, backend: e.target.value as 'arduino' | 'kmbox' | 'adb' }))}>
            <option value="arduino">Arduino HID</option>
            <option value="kmbox">KMBox Net</option>
            <option value="adb">ADB (mobile)</option>
          </select>
        </label>
        <label>Arduino port: <input value={hid.arduino_port}
          onChange={e => setHid(h => ({ ...h, arduino_port: e.target.value }))}
          placeholder="/dev/ttyACM0" /></label>
        <label>KMBox endpoint: <input value={hid.kmbox_endpoint}
          onChange={e => setHid(h => ({ ...h, kmbox_endpoint: e.target.value }))}
          placeholder="192.168.1.50:65432" /></label>
        <label>ADB serial: <input value={hid.adb_serial}
          onChange={e => setHid(h => ({ ...h, adb_serial: e.target.value }))}
          placeholder="phone serial" /></label>
      </div>

      <h4>One-click</h4>
      <div className="gl-recipe-row">
        <button onClick={startFullKit}>Start full kit</button>
        <button onClick={() => start('game_offsets',
          { mode: 'refresh', game: project.game })}>
          Refresh offsets
        </button>
        <button onClick={() => start('hwid_spoof',
          { mode: 'snapshot', label: 'pre-session' })}>
          HWID snapshot
        </button>
      </div>

      <h4>Legacy single recipes</h4>
      <div className="gl-recipe-row">
        <button onClick={() => start('vision_esp',
          { esp_overlay: true, aimbot_enabled: false })}>
          Start ESP (capture-card vision)
        </button>
        <button onClick={() => start('dma_memread',
          { game: project.game })}>
          Start DMA reader (old pipeline)
        </button>
        {['bgmi','cod_mobile','free_fire'].includes(project.game) &&
          <button onClick={() => start('bgmi_mobile',
            { aim_via: 'none', overlay: true })}>
            Start scrcpy ESP (vision-only)
          </button>}
      </div>
      <ul className="gl-recipes">
        {recipes.map(r => (
          <li key={r.id}>
            <code>{r.adapter}</code> · {r.status}
            {r.pid ? <> (pid {r.pid})</> : null}
            {r.status === 'running' &&
              <button onClick={() => stop(r.id)} style={{ marginLeft: 8 }}>Stop</button>}
            {r.error && <div className="gl-err">{r.error}</div>}
          </li>
        ))}
      </ul>

      <h4>Chat with the GameLab guide</h4>
      <GameLabChat projectId={project.id} />

      {log.length > 0 && (
        <>
          <h4>Session log</h4>
          <pre className="gl-log">{log.join('\n')}</pre>
        </>
      )}
    </section>
  );
}
